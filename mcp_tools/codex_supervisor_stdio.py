"""Codex-facing stdio MCP server for supervisor control.

This module is intentionally independent of the Claude Agent SDK wrappers in
`mcp_tools.codex_tools` and `mcp_tools.supervisor_tools`. Codex loads this
server through its external MCP configuration and receives ordinary MCP tools.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping

from supervisor.config import Config, PLANNING_RUBRIC_MIN_THRESHOLD
from supervisor.agent_mailbox import (
    AgentMailboxMessage,
    codex_confidence_report,
    codex_review_packet,
    critical_review_from_outcome,
    critical_review_prompt,
    outcome_confidence_report,
    planning_artifact_refs,
)
from supervisor.cursor_agent import (
    CursorInvocationRequest,
    CursorInvocationResult,
    CursorRunner,
    DEFAULT_CURSOR_SDK_MODEL,
    DEFAULT_STRUCTURED_REVIEWER_MODEL,
    cursor_accepts,
    invoke_cursor_agent,
)
from supervisor.reviewer_registry import (
    adjudicate_reviewer_panel,
    configured_reviewers,
    evaluate_reviewer_panel,
    independent_reviewer_results_from_review_results,
    load_reviewer_panel_calibration,
)
from supervisor.dual_agent import GateRound, ProbeResult, evaluate_deadlock_budget
from supervisor.dual_agent_artifacts import (
    ScreenshotArtifact,
    default_dual_agent_artifact_dir,
    export_dual_agent_run_artifacts,
)
from supervisor.dual_agent_workflow import (
    WORKFLOW_GATES,
    claude_accepts,
    cursor_review_gates_for_workflow,
    ensure_workflow_source_artifacts,
    mandatory_artifact_status,
    prerequisites_for_route,
    required_artifacts_for_complexity,
    required_planning_kinds_for_complexity,
    select_workflow_route,
    verify_gate_deliverable_evidence,
    verify_prd_tdd_skill_receipts,
    verify_workflow_claims,
    workflow_visual_evidence_policy,
    workflow_milestone_text,
    workflow_resume_prompt,
)
from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts
from supervisor.dynamic_workflow import prepare_dynamic_workflow_preview
from supervisor.agentic_executor import produce_agentic_worker_receipts
from supervisor.agentic_workers import discover_agentic_worker_receipts
from supervisor.runtime_evidence import capture_runtime_baseline, collect_runtime_evidence
from supervisor.receipt_provenance import (
    provenance_downgrade_event_payload,
    sanitize_receipt_provenance,
)
from supervisor.review_packets import (
    ChangedFile,
    PlanningRef,
    ReceiptRef,
    build_review_packet,
    context_validation_payload,
    reviewer_context_receipt_from_payload,
    review_context_incomplete_reason,
    review_packet_event_kind,
    validate_reviewer_context_receipt,
    validate_review_packet,
)
from supervisor.worker_dispatch_ledger import (
    CROSS_VENDOR_REVIEW_SELECTED,
    DEGRADED_REVIEW_UNAVAILABLE,
    EVIDENCE_ATTEMPT_RECORDED,
    WORKER_BLOCKED,
    WORKER_CANCELLED,
    WORKER_COMPLETED,
    WORKER_DISPATCHED,
    WORKER_FAILED,
    WORKER_ROSTER_CHECKED,
    WORKER_SESSION_CREATED,
    EvidenceAttempt,
    RosterEntry,
    RosterPreflight,
    WorkerDispatch,
    build_cross_vendor_payload,
    build_dispatch_payload,
    build_evidence_attempt_payload,
    build_roster_payload,
    compute_attempt_id,
    provider_family_for,
    select_cross_vendor_reviewer,
)
from supervisor.lessons import (
    append_lesson_block,
    build_lesson_injection,
    record_lessons_for_run,
)
from supervisor.quality_trends import record_quality_trends_for_run, record_transport_incident
from supervisor.policy_overlay import (
    apply_policy_overlay_to_instruction,
    draft_policy_regression_rollbacks_for_trend_rows,
    load_policy_overlay,
)
from supervisor.no_mistakes import (
    NoMistakesConfig,
    NoMistakesValidationRequest,
    NoMistakesValidationResult,
    build_no_mistakes_command,
    run_no_mistakes_validation,
)
from supervisor.planning_validator import _tdd_test_names
from supervisor.autoresearch.policy_evolution import (
    approve_policy_proposal,
    create_policy_evolution_proposals,
    derive_policy_evolution_proposals_from_report,
    deny_policy_proposal,
    rollback_policy_proposal,
)
from supervisor.autoresearch.generator import (
    AutoResearchGeneratorConfig,
    activate_autoresearch_experiment,
    generate_autoresearch_experiment_drafts,
    park_autoresearch_experiment,
)
from supervisor.dual_agent_runner import (
    DualAgentGateResult,
    DualAgentGateSpec,
    request_deadlock_escalation,
    resume_pending_gates,
    run_dual_agent_gate,
    run_dual_agent_gate_with_escalation,
)
from supervisor.dual_agent_lead import GateName, PlanningArtifact
from supervisor.redaction import redact
from supervisor.state import State
from supervisor.trace_envelope import ensure_tool_call_timing, stamp_trace_envelope, timed_tool_call
from supervisor.telegram import TelegramNotifier, telegram_enabled


Runner = Callable[..., subprocess.CompletedProcess[str]]
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_REASONING_EFFORT = "xhigh"
STRICT_ARTIFACT_GATES = {"implementation_plan", "execution", "outcome_review"}
STRICT_ARTIFACT_REQUIREMENTS = {
    "prd_review": ("prd",),
    "issues_review": ("prd", "issues", "grill_findings"),
    "tdd_review": ("prd", "issues", "tdd_plan", "grill_findings"),
    "implementation_plan": ("prd", "tdd_plan", "grill_findings", "issues"),
    "execution": ("prd", "tdd_plan", "grill_findings", "issues", "implementation_plan"),
    "outcome_review": ("prd", "tdd_plan", "grill_findings", "issues", "implementation_plan"),
}
GATE_PREREQUISITES = {
    "issues_review": ("prd_review",),
    "tdd_review": ("prd_review", "issues_review"),
    "implementation_plan": ("prd_review", "issues_review", "tdd_review"),
    "execution": ("implementation_plan",),
    "outcome_review": ("execution",),
}
RELAXED_ARTIFACT_POLICIES = {"relaxed", "audit", "off"}
VISUAL_VALIDATION_SOURCES = {"browser", "browser_use", "browser-use", "computer_use", "computer-use", "computer"}
VISUAL_VALIDATION_PASSED = {"passed", "pass", "accepted", "accept", "ok"}
REVIEWER_UNAVAILABLE_POLICIES = {"block", "escalate", "proceed_degraded"}
NO_MISTAKES_POLICIES = {"off", "advisory", "required", "shipping"}


def _canonical_workflow_job_payload(payload: dict[str, Any]) -> str:
    """Stable request identity for no-token detached workflow retries.

    The field set is exactly the workflow request payload that will be written
    to request.json and consumed by the CLI worker: semantic workflow knobs,
    artifacts, reviewer settings, and receipts. It intentionally excludes
    submit-only transport fields such as job_id, client_token, generated paths,
    pid, and config_path so a transport retry reattaches to the same job.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _workflow_job_idempotency_token(
    *,
    run_id: str,
    payload: dict[str, Any],
    client_token: str | None,
) -> str:
    supplied = (client_token or "").strip()
    if supplied:
        digest = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        return f"client:{digest}"
    canonical = _canonical_workflow_job_payload(payload)
    digest = hashlib.sha256(f"{run_id}\n{canonical}".encode("utf-8")).hexdigest()
    return f"derived:{digest}"


def _resolve_repo_path(repo_root: Path, value: str) -> Path:
    path = Path(str(value or "").strip()).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve(strict=False)


def _read_json_mapping(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"unable to read JSON artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON artifact: {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return loaded


def _proposal_payload(
    *,
    proposal: dict[str, Any] | None,
    proposal_path: str | None,
    repo_root: Path,
) -> dict[str, Any]:
    if proposal is not None:
        return dict(proposal)
    if proposal_path:
        return _read_json_mapping(_resolve_repo_path(repo_root, proposal_path))
    raise ValueError("proposal or proposal_path is required")


def _rollback_pointer_payload(
    *,
    rollback_pointer: dict[str, Any] | None,
    rollback_pointer_path: str | None,
    repo_root: Path,
) -> dict[str, Any]:
    if rollback_pointer is not None:
        return dict(rollback_pointer)
    if rollback_pointer_path:
        return _read_json_mapping(_resolve_repo_path(repo_root, rollback_pointer_path))
    raise ValueError("rollback_pointer or rollback_pointer_path is required")


def _runtime_baseline_for_gate(
    cwd: str,
    *,
    task_id: str,
    gate: str,
    round_index: int,
) -> dict[str, Any]:
    """Pre-execution-anchored runtime baseline (wall eight, r-2026-06-12).

    Execution gates capture a fresh head each round and persist the round-1
    head to the task artifact dir; outcome_review gates reuse the persisted
    pre-execution head so committed execution work is visible in the diff —
    including when a parked task resumes at outcome_review in a new process.
    Fallback to a fresh capture is journaled through the baseline reason.
    """
    marker = default_dual_agent_artifact_dir(cwd, task_id) / "runtime-baseline-execution.json"
    if gate == "execution":
        baseline = capture_runtime_baseline(cwd)
        if round_index == 1 and baseline.get("status") == "passed":
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    json.dumps(
                        {
                            "head": baseline.get("head"),
                            "captured_at": baseline.get("captured_at"),
                            "task_id": task_id,
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass  # persistence is best-effort; execution itself is unaffected
        return baseline
    try:
        persisted = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        persisted = None
    if isinstance(persisted, dict) and persisted.get("head"):
        return {
            "status": "passed",
            "head": str(persisted["head"]),
            "captured_at": persisted.get("captured_at"),
            "reason": "persisted_execution_baseline",
        }
    fallback = capture_runtime_baseline(cwd)
    if fallback.get("status") == "passed":
        fallback["reason"] = "fresh_fallback_no_persisted_execution_baseline"
    return fallback


class CodexSupervisorMcpAPI:
    def __init__(
        self,
        cfg: Config,
        state: State,
        *,
        runner: Runner = subprocess.run,
        codex_runner: Runner = subprocess.run,
        cursor_runner: CursorRunner | None = None,
        no_mistakes_runner: Runner = subprocess.run,
        notifier: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.runner = runner
        self.codex_runner = codex_runner
        self.cursor_runner = cursor_runner or invoke_cursor_agent
        self.no_mistakes_runner = no_mistakes_runner
        self.notifier = notifier

    async def start_dual_agent_gate(
        self,
        *,
        task_id: str,
        run_id: str,
        gate: GateName,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None = None,
        expected_decisions: list[str] | None = None,
        expected_objections: list[str] | None = None,
        quality: str = "best",
        model: str | None = None,
        budget_usd: float = 5.0,
        timeout_s: int = 600,
        execution_layer_mode: str = "lead_direct",
        dynamic_workflow_task_class: str | None = None,
        agentic_lead_policy: str | None = None,
        min_subagents: int | None = None,
        required_roles: list[str] | None = None,
        solo_exception_for_artifact_only_gates: bool | None = None,
        required_evidence_grade: str | None = None,
        planning_artifacts: list[dict[str, Any]] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        artifact_policy: str = "strict",
        user_facing: bool = False,
        screenshots: list[dict[str, Any]] | None = None,
        required_artifacts: tuple[str, ...] | list[str] | None = None,
        required_prerequisite_gates: tuple[str, ...] | list[str] | None = None,
        required_planning_kinds: tuple[str, ...] | list[str] | None = None,
        injected_lesson_block: str = "",
        injected_lesson_block_sha256: str = "",
        injected_lesson_ids: list[str] | tuple[str, ...] | None = None,
        policy_overlay_block: str = "",
        policy_overlay_block_sha256: str = "",
        policy_overlay_hash: str = "",
        policy_proposal_id: str = "",
        policy_overlay_task_class: str = "",
        policy_overlay_frozen: bool = False,
        policy_overlay_task_class_hash: str = "",
        planning_rubric_threshold: float | None = None,
        planning_rubric_unavailable_policy: str | None = None,
        sanitize_tool_receipts: bool = True,
    ) -> dict[str, Any]:
        execution_layer_mode = _canonical_execution_layer_mode(execution_layer_mode)
        dynamic_workflow_task_class = _canonical_dynamic_workflow_task_class(dynamic_workflow_task_class)
        agentic_policy = _agentic_lead_policy_config(
            self.cfg,
            agentic_lead_policy=agentic_lead_policy,
            min_subagents=min_subagents,
            required_roles=required_roles,
            solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
            required_evidence_grade=required_evidence_grade,
        )
        if sanitize_tool_receipts:
            tool_receipts = _normalise_receipt_payloads(tool_receipts or [])
            self._write_receipt_provenance_downgrade_events(
                run_id=run_id,
                task_id=task_id,
                receipts=tool_receipts,
                scope=f"{gate}:caller_tool_receipts",
            )
        artifact_preflight = _artifact_preflight(
            state=self.state,
            run_id=run_id,
            task_id=task_id,
            gate=str(gate),
            planning_artifacts=planning_artifacts or [],
            artifact_policy=artifact_policy,
            user_facing=user_facing,
            screenshots=screenshots or [],
            required_artifacts=required_artifacts,
            required_prerequisite_gates=required_prerequisite_gates,
        )
        if artifact_preflight["status"] == "blocked":
            payload = _artifact_blocked_payload(
                task_id=task_id,
                gate=str(gate),
                artifact_preflight=artifact_preflight,
            )
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_gate_result",
                payload=payload,
            )
            payload["artifact_export"] = self.export_gate_artifacts(
                run_id=run_id,
                task_id=task_id,
                cwd=cwd,
                screenshots=screenshots,
            )
            return redact(payload)

        dynamic_receipt_block = self._dynamic_workflow_receipt_block(
            run_id=run_id,
            task_id=task_id,
            gate=str(gate),
            execution_layer_mode=execution_layer_mode,
            dynamic_workflow_task_class=dynamic_workflow_task_class,
            agentic_policy=agentic_policy,
            tool_receipts=tool_receipts,
            cwd=cwd,
            screenshots=screenshots,
            artifact_rigor=artifact_preflight,
        )
        if dynamic_receipt_block is not None:
            return dynamic_receipt_block

        spec = self._gate_spec(
            task_id=task_id,
            run_id=run_id,
            gate=gate,
            instruction=instruction,
            cwd=cwd,
            expected_specialists=expected_specialists,
            expected_decisions=expected_decisions,
            expected_objections=expected_objections,
            quality=quality,
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            execution_layer_mode=execution_layer_mode,  # type: ignore[arg-type]
            dynamic_workflow_task_class=dynamic_workflow_task_class,  # type: ignore[arg-type]
            agentic_policy=agentic_policy,
            planning_artifacts=planning_artifacts,
            required_planning_kinds=required_planning_kinds,
            injected_lesson_block=injected_lesson_block,
            injected_lesson_block_sha256=injected_lesson_block_sha256,
            injected_lesson_ids=tuple(str(item) for item in (injected_lesson_ids or ())),
            policy_overlay_block=policy_overlay_block,
            policy_overlay_block_sha256=policy_overlay_block_sha256,
            policy_overlay_hash=policy_overlay_hash,
            policy_proposal_id=policy_proposal_id,
            policy_overlay_task_class=policy_overlay_task_class,
            policy_overlay_frozen=policy_overlay_frozen,
            policy_overlay_task_class_hash=policy_overlay_task_class_hash,
            planning_rubric_threshold=planning_rubric_threshold,
            planning_rubric_unavailable_policy=planning_rubric_unavailable_policy,
        )
        notifier = self._notifier()
        with timed_tool_call(
            "start_dual_agent_gate",
            args={
                "task_id": task_id,
                "gate": gate,
                "artifact_policy": artifact_policy,
                "planning_artifact_count": len(planning_artifacts or []),
                "user_facing": user_facing,
                "screenshot_count": len(screenshots or []),
                "execution_layer_mode": execution_layer_mode,
                "dynamic_workflow_task_class": dynamic_workflow_task_class,
                "agentic_lead_policy": agentic_policy["agentic_lead_policy"],
                "min_subagents": agentic_policy["min_subagents"],
                "required_roles": agentic_policy["required_roles"],
                "required_evidence_grade": agentic_policy["required_evidence_grade"],
            },
        ) as gate_tool_call:
            if notifier is not None:
                result = await run_dual_agent_gate_with_escalation(
                    spec,
                    state=self.state,
                    notifier=notifier,
                    runner=self.runner,
                )
            else:
                result = run_dual_agent_gate(spec, runner=self.runner, state=self.state)
        gate_tool_call.update({
            "status": "completed",
            "attempts": result.attempts,
            "handoff_packet_path": str(result.handoff_packet_path),
            "result_summary": {
                "claude_gate_status": result.status,
                "supervisor_final_status": result.status,
                "probe_statuses": {
                    key: value.status for key, value in result.probes.items()
                },
            },
        })
        payload = _gate_result_payload(result, gate_tool_call=gate_tool_call)
        payload["artifact_rigor"] = artifact_preflight
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload=payload,
        )
        payload["artifact_export"] = self.export_gate_artifacts(
            run_id=run_id,
            task_id=task_id,
            cwd=cwd,
            screenshots=screenshots,
        )
        return payload

    def poll_resume_signal(
        self,
        *,
        task_id: str,
        run_id: str,
        gate: GateName,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None = None,
        expected_decisions: list[str] | None = None,
        expected_objections: list[str] | None = None,
        quality: str = "best",
        model: str | None = None,
        budget_usd: float = 5.0,
        timeout_s: int = 600,
        execution_layer_mode: str = "lead_direct",
        dynamic_workflow_task_class: str | None = None,
        agentic_lead_policy: str | None = None,
        min_subagents: int | None = None,
        required_roles: list[str] | None = None,
        solo_exception_for_artifact_only_gates: bool | None = None,
        required_evidence_grade: str | None = None,
        planning_artifacts: list[dict[str, Any]] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        artifact_policy: str = "strict",
        user_facing: bool = False,
        screenshots: list[dict[str, Any]] | None = None,
        required_artifacts: list[str] | None = None,
        required_prerequisite_gates: list[str] | None = None,
        required_planning_kinds: list[str] | None = None,
        planning_rubric_threshold: float | None = None,
        planning_rubric_unavailable_policy: str | None = None,
    ) -> dict[str, Any]:
        execution_layer_mode = _canonical_execution_layer_mode(execution_layer_mode)
        dynamic_workflow_task_class = _canonical_dynamic_workflow_task_class(dynamic_workflow_task_class)
        agentic_policy = _agentic_lead_policy_config(
            self.cfg,
            agentic_lead_policy=agentic_lead_policy,
            min_subagents=min_subagents,
            required_roles=required_roles,
            solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
            required_evidence_grade=required_evidence_grade,
        )
        artifact_preflight = _artifact_preflight(
            state=self.state,
            run_id=run_id,
            task_id=task_id,
            gate=str(gate),
            planning_artifacts=planning_artifacts or [],
            artifact_policy=artifact_policy,
            user_facing=user_facing,
            screenshots=screenshots or [],
        )
        if artifact_preflight["status"] == "blocked":
            payload = _artifact_blocked_payload(
                task_id=task_id,
                gate=str(gate),
                artifact_preflight=artifact_preflight,
            )
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_gate_result",
                payload=payload,
            )
            payload["artifact_export"] = self.export_gate_artifacts(
                run_id=run_id,
                task_id=task_id,
                cwd=cwd,
                screenshots=screenshots,
            )
            return redact(payload)

        dynamic_receipt_block = self._dynamic_workflow_receipt_block(
            run_id=run_id,
            task_id=task_id,
            gate=str(gate),
            execution_layer_mode=execution_layer_mode,
            dynamic_workflow_task_class=dynamic_workflow_task_class,
            agentic_policy=agentic_policy,
            tool_receipts=tool_receipts,
            cwd=cwd,
            screenshots=screenshots,
            artifact_rigor=artifact_preflight,
        )
        if dynamic_receipt_block is not None:
            return dynamic_receipt_block

        spec = self._gate_spec(
            task_id=task_id,
            run_id=run_id,
            gate=gate,
            instruction=instruction,
            cwd=cwd,
            expected_specialists=expected_specialists,
            expected_decisions=expected_decisions,
            expected_objections=expected_objections,
            quality=quality,
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            execution_layer_mode=execution_layer_mode,  # type: ignore[arg-type]
            dynamic_workflow_task_class=dynamic_workflow_task_class,  # type: ignore[arg-type]
            agentic_policy=agentic_policy,
            planning_artifacts=planning_artifacts,
        )
        results = resume_pending_gates([spec], state=self.state, runner=self.runner)
        if not results:
            return {"status": "no_signal", "task_id": task_id, "run_id": run_id}
        payload = _gate_result_payload(results[0])
        payload["artifact_rigor"] = artifact_preflight
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload=payload,
        )
        payload["artifact_export"] = self.export_gate_artifacts(
            run_id=run_id,
            task_id=task_id,
            cwd=cwd,
            screenshots=screenshots,
        )
        return payload

    async def run_dual_agent_workflow(
        self,
        *,
        cwd: str,
        task_id: str,
        run_id: str,
        intent: str,
        user_facing: bool = False,
        visual_evidence_policy: str = "auto",
        max_rounds_per_gate: int = 5,
        quality: str = "best",
        budget_usd: float = 100.0,
        timeout_s: int = 900,
        execution_layer_mode: str = "lead_direct",
        dynamic_workflow_task_class: str | None = None,
        agentic_lead_policy: str | None = None,
        min_subagents: int | None = None,
        required_roles: list[str] | None = None,
        solo_exception_for_artifact_only_gates: bool | None = None,
        required_evidence_grade: str | None = None,
        reviewer_unavailable_policy: str | None = None,
        planning_artifacts: list[dict[str, Any]] | None = None,
        screenshots: list[dict[str, Any]] | None = None,
        verified_claims: list[str] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        require_skill_receipts: bool = True,
        cursor_review: bool = True,
        cursor_review_profile: str = "default",
        cursor_review_gates: list[str] | None = None,
        cursor_model: str | None = None,
        reviewer_model: str | None = None,
        reviewer_output_mode: str | None = None,
        reviewer_max_tokens: int | None = None,
        reviewer_infra_retry_limit: int | None = None,
        reviewer_infra_retry_backoff_s: float | None = None,
        reviewer_low_confidence_threshold: float | None = None,
        reviewer_panel_calibration_path: str | None = None,
        task_complexity: str | None = None,
        no_mistakes_policy: str | None = None,
        no_mistakes_skip_steps: list[str] | None = None,
        no_mistakes_timeout_s: int | None = None,
    ) -> dict[str, Any]:
        execution_layer_mode = _canonical_execution_layer_mode(execution_layer_mode)
        dynamic_workflow_task_class = _canonical_dynamic_workflow_task_class(dynamic_workflow_task_class)
        agentic_policy = _agentic_lead_policy_config(
            self.cfg,
            agentic_lead_policy=agentic_lead_policy,
            min_subagents=min_subagents,
            required_roles=required_roles,
            solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
            required_evidence_grade=required_evidence_grade,
        )
        reviewer_policy = _reviewer_unavailable_policy_config(
            self.cfg,
            reviewer_unavailable_policy=reviewer_unavailable_policy,
        )
        reviewer_output_mode_value = _reviewer_output_mode_config(
            self.cfg,
            reviewer_output_mode=reviewer_output_mode,
        )
        reviewer_model_value = _reviewer_model_config(
            self.cfg,
            reviewer_model=reviewer_model,
            cursor_model=cursor_model,
            reviewer_output_mode=reviewer_output_mode_value,
        )
        reviewer_max_tokens_value = _reviewer_max_tokens_config(
            self.cfg,
            reviewer_max_tokens=reviewer_max_tokens,
        )
        reviewer_infra_retry_limit_value = _reviewer_infra_retry_limit_config(
            self.cfg,
            reviewer_infra_retry_limit=reviewer_infra_retry_limit,
        )
        reviewer_infra_retry_backoff_s_value = _reviewer_infra_retry_backoff_s_config(
            self.cfg,
            reviewer_infra_retry_backoff_s=reviewer_infra_retry_backoff_s,
        )
        reviewer_low_confidence_threshold_value = _reviewer_low_confidence_threshold_config(
            self.cfg,
            reviewer_low_confidence_threshold=reviewer_low_confidence_threshold,
        )
        reviewer_panel_calibration_path_value = _reviewer_panel_calibration_path_config(
            self.cfg,
            cwd=cwd,
            reviewer_panel_calibration_path=reviewer_panel_calibration_path,
        )
        reviewer_panel_calibration = load_reviewer_panel_calibration(
            reviewer_panel_calibration_path_value
        )
        no_mistakes_config = _no_mistakes_config(
            self.cfg,
            policy=no_mistakes_policy,
            skip_steps=no_mistakes_skip_steps,
            timeout_s=no_mistakes_timeout_s,
        )
        max_rounds = max(1, int(max_rounds_per_gate))
        screenshot_payloads = screenshots or []
        receipt_payloads = _normalise_receipt_payloads(tool_receipts or [])
        self._write_receipt_provenance_downgrade_events(
            run_id=run_id,
            task_id=task_id,
            receipts=receipt_payloads,
            scope="caller_tool_receipts",
        )
        source_artifacts = ensure_workflow_source_artifacts(
            cwd=cwd,
            task_id=task_id,
            intent=intent,
        )
        gate_artifacts = _merge_planning_artifacts(
            list(source_artifacts.planning_artifacts),
            planning_artifacts or [],
        )
        visual_policy = workflow_visual_evidence_policy(
            intent=intent,
            task_id=task_id,
            user_facing=user_facing,
            planning_artifacts=gate_artifacts,
            operator_policy=visual_evidence_policy,
        )
        if (
            str(visual_policy.get("operator_policy") or "") == "not_required"
            and visual_policy.get("matched_terms")
        ):
            self.state.write_event(
                run_id=run_id,
                source="supervisor",
                kind="visual_evidence_override_asserted",
                payload={
                    "schema_version": "supervisor-visual-evidence-override/v1",
                    "task_id": task_id,
                    "operator_policy": "not_required",
                    "matched_terms": list(visual_policy.get("matched_terms") or []),
                    "artifact_matches": list(visual_policy.get("artifact_matches") or []),
                    "observational_only": True,
                    "gate_authority": "unchanged",
                },
            )
        effective_user_facing = bool(visual_policy["required"])
        workflow_route = select_workflow_route(
            intent=intent,
            user_facing=effective_user_facing,
            task_complexity=task_complexity,
        )
        route_gates = tuple(str(gate) for gate in workflow_route["gates"])
        required_skill_stages = tuple(str(stage) for stage in workflow_route["required_skill_stages"])
        selected_cursor_gates = cursor_review_gates_for_workflow(
            route_gates=route_gates,
            task_complexity=str(workflow_route["task_complexity"]),
            cursor_review=cursor_review,
            cursor_review_profile=cursor_review_profile,
            cursor_review_gates=cursor_review_gates,
        )
        effective_cursor_review = bool(selected_cursor_gates)
        lesson_task_class = _lesson_task_class(
            dynamic_workflow_task_class=dynamic_workflow_task_class,
            workflow_route=workflow_route,
        )
        workflow_route = {
            **workflow_route,
            "requested_cursor_review": bool(cursor_review),
            "effective_cursor_review": effective_cursor_review,
            "cursor_review_profile": cursor_review_profile,
            "cursor_review_gates": list(selected_cursor_gates),
            "reviewer_model": reviewer_model_value,
            "reviewer_output_mode": reviewer_output_mode_value,
            "reviewer_max_tokens": reviewer_max_tokens_value,
            "reviewer_infra_retry_limit": reviewer_infra_retry_limit_value,
            "reviewer_infra_retry_backoff_s": reviewer_infra_retry_backoff_s_value,
            "reviewer_low_confidence_threshold": reviewer_low_confidence_threshold_value,
            "reviewer_panel_calibration_path": reviewer_panel_calibration_path_value,
            "reviewer_panel_calibration_active": reviewer_panel_calibration is not None,
            "no_mistakes": {
                "policy": no_mistakes_config.policy,
                "skip_steps": list(no_mistakes_config.skip_steps),
                "auto_yes": no_mistakes_config.auto_yes,
                "timeout_s": no_mistakes_config.timeout_s,
                "allow_shipping_steps": no_mistakes_config.allow_shipping_steps,
                "require_clean_committed_branch": no_mistakes_config.require_clean_committed_branch,
            },
            "execution_layer_mode": execution_layer_mode,
            "dynamic_workflow_task_class": dynamic_workflow_task_class,
            "lesson_task_class": lesson_task_class,
            "lesson_snapshot": _workflow_lesson_snapshot(
                self.state,
                lesson_task_class=lesson_task_class,
                route_gates=route_gates,
            ),
            "requires_dynamic_workflow_receipts": _is_dynamic_workflow_preview(execution_layer_mode),
            "agentic_lead_policy": _workflow_agentic_policy_route(agentic_policy),
            "reviewer_unavailable_policy": reviewer_policy,
            "requires_agentic_lead_policy_receipts": _is_agentic_lead_policy_active(
                agentic_policy["agentic_lead_policy"]
            ),
        }
        hydrated_agentic_receipts: list[dict[str, Any]] = []
        if _is_agentic_lead_policy_active(agentic_policy["agentic_lead_policy"]):
            hydrated_agentic_receipts = _hydrate_agentic_worker_receipts(
                state=self.state,
                cwd=cwd,
                run_id=run_id,
                task_id=task_id,
            )
            if hydrated_agentic_receipts:
                receipt_payloads = _dedupe_receipt_payloads(_normalise_receipt_payloads([
                    *receipt_payloads,
                    *hydrated_agentic_receipts,
                ]))
            workflow_route = {
                **workflow_route,
                "agentic_worker_hydration": {
                    "status": "hydrated" if hydrated_agentic_receipts else "empty",
                    "receipt_count": len(hydrated_agentic_receipts),
                    "receipt_ids": _receipt_ids(hydrated_agentic_receipts),
                },
            }
        dynamic_preparation = None
        if _is_dynamic_workflow_preview(execution_layer_mode):
            dynamic_preparation = prepare_dynamic_workflow_preview(
                cwd=cwd,
                task_id=task_id,
                run_id=run_id,
                dynamic_workflow_task_class=dynamic_workflow_task_class,
                budget_usd=budget_usd,
                timeout_s=timeout_s,
                tool_receipts=receipt_payloads,
                output_dir=source_artifacts.output_dir,
            )
            if dynamic_preparation.synthesized_receipts:
                receipt_payloads = _normalise_receipt_payloads([
                    *receipt_payloads,
                    *dynamic_preparation.synthesized_receipts,
                ])
            workflow_route = {
                **workflow_route,
                "dynamic_workflow_manifest": dynamic_preparation.manifest,
                "dynamic_workflow_manifest_ref": dynamic_preparation.manifest_ref,
                "dynamic_workflow_manifest_sha256": dynamic_preparation.manifest_sha256,
                "dynamic_workflow_synthesis": dynamic_preparation.synthesis,
                "synthesized_dynamic_receipt_count": len(dynamic_preparation.synthesized_receipts),
            }
        agentic_worker_production = None
        agentic_worker_tool_call: dict[str, Any] | None = None
        if _is_agentic_lead_policy_active(agentic_policy["agentic_lead_policy"]):
            with timed_tool_call(
                "produce_agentic_worker_receipts",
                args={
                    "task_id": task_id,
                    "run_id": run_id,
                    "agentic_lead_policy": agentic_policy["agentic_lead_policy"],
                    "min_subagents": agentic_policy["min_subagents"],
                    "required_roles": agentic_policy["required_roles"],
                    "existing_receipt_count": len(receipt_payloads),
                },
                receipt_ids=_receipt_ids(receipt_payloads),
            ) as agentic_tool_call:
                agentic_worker_production = produce_agentic_worker_receipts(
                    cwd=cwd,
                    task_id=task_id,
                    run_id=run_id,
                    intent=intent,
                    agentic_policy=agentic_policy,
                    existing_receipts=receipt_payloads,
                    timeout_s=timeout_s,
                    budget_usd=budget_usd,
                    quality=quality,  # type: ignore[arg-type]
                    runner=self.runner,
                )
            agentic_tool_call.update({
                "status": agentic_worker_production.status,
                "result_summary": {
                    "status": agentic_worker_production.status,
                    "receipt_count": len(agentic_worker_production.receipts),
                    "blocking_findings": agentic_worker_production.blocking_findings,
                },
            })
            agentic_worker_tool_call = ensure_tool_call_timing(agentic_tool_call)
            if agentic_worker_production.receipts:
                receipt_payloads = _dedupe_receipt_payloads(_normalise_receipt_payloads([
                    *receipt_payloads,
                    *agentic_worker_production.receipts,
                ]))
            workflow_route = {
                **workflow_route,
                "agentic_worker_production": {
                    "status": agentic_worker_production.status,
                    "receipt_count": len(agentic_worker_production.receipts),
                    "blocking_findings": agentic_worker_production.blocking_findings,
                    "hydrated_receipt_count": len(hydrated_agentic_receipts),
                },
            }
        prior_resume = _workflow_resume_state(
            self.state,
            run_id=run_id,
            task_id=task_id,
            route_gates=route_gates,
        )
        workflow_route = {
            **workflow_route,
            "resume": prior_resume,
        }
        skill_probe = (
            verify_prd_tdd_skill_receipts(
                receipt_payloads,
                required_stages=required_skill_stages,
            )
            if require_skill_receipts and required_skill_stages
            else None
        )
        with timed_tool_call(
            "verify_dynamic_workflow_receipts",
            args={
                "task_id": task_id,
                "execution_layer_mode": execution_layer_mode,
                "dynamic_workflow_task_class": dynamic_workflow_task_class,
                "agentic_lead_policy": agentic_policy["agentic_lead_policy"],
                "min_subagents": agentic_policy["min_subagents"],
                "required_roles": agentic_policy["required_roles"],
                "required_evidence_grade": agentic_policy["required_evidence_grade"],
                "receipt_count": len(receipt_payloads),
            },
            receipt_ids=_receipt_ids(receipt_payloads),
        ) as dynamic_receipt_tool_call:
            dynamic_workflow_probe = verify_dynamic_workflow_receipts(
                execution_layer_mode=execution_layer_mode,
                dynamic_workflow_task_class=dynamic_workflow_task_class,
                tool_receipts=receipt_payloads,
                cwd=cwd,
                gate="workflow_start",
                **agentic_policy,
            )
        dynamic_receipt_tool_call.update({
            "status": dynamic_workflow_probe.status,
            "probe_id": dynamic_workflow_probe.probe_id,
            "reason": dynamic_workflow_probe.reason,
            "result_summary": {
                "probe_id": dynamic_workflow_probe.probe_id,
                "status": dynamic_workflow_probe.status,
                "reason": dynamic_workflow_probe.reason,
                "missing_gates": dynamic_workflow_probe.details.get("missing_gates", []),
                "verified_gates": dynamic_workflow_probe.details.get("verified_gates", []),
            },
        })
        self.state.upsert_dual_agent_workflow(
            run_id=run_id,
            task_id=task_id,
            cwd=str(Path(cwd).expanduser()),
            intent=intent,
            current_gate=prior_resume["pending_gates"][0] if prior_resume["pending_gates"] else route_gates[-1],
            status="running",
            max_rounds_per_gate=max_rounds,
            user_facing=effective_user_facing,
        )
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_workflow_route",
            payload={
                **workflow_route,
                "task_id": task_id,
                "run_id": run_id,
                "intent": intent,
                "effective_user_facing": effective_user_facing,
                "requested_cursor_review": bool(cursor_review),
                "effective_cursor_review": effective_cursor_review,
            },
        )
        if dynamic_preparation is not None:
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_dynamic_workflow_manifest",
                payload={
                    "task_id": task_id,
                    "gate": "workflow_start",
                    "status": "accepted",
                    "manifest": dynamic_preparation.manifest,
                    "manifest_ref": dynamic_preparation.manifest_ref,
                    "manifest_sha256": dynamic_preparation.manifest_sha256,
                    "synthesis": dynamic_preparation.synthesis,
                    "synthesized_receipt_count": len(dynamic_preparation.synthesized_receipts),
                },
            )
        if agentic_worker_production is not None:
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_agentic_worker_production",
                payload={
                    "task_id": task_id,
                    "gate": "workflow_start",
                    **agentic_worker_production.to_event_payload(),
                    "tool_calls": [agentic_worker_tool_call] if agentic_worker_tool_call is not None else [],
                },
            )
            for receipt in agentic_worker_production.receipts:
                worker_id = str(receipt.get("worker_id") or receipt.get("agent_id") or "")
                role = str(receipt.get("role") or "")
                self.state.write_event(
                    run_id=run_id,
                    source="dual_agent",
                    kind="dual_agent_agentic_worker_progress",
                    payload={
                        "schema_version": "agentic-worker-progress/v1",
                        "task_id": task_id,
                        "gate": "workflow_start",
                        "status": "finished",
                        "worker_id": worker_id,
                        "role": role,
                        "receipt_id": receipt.get("receipt_id"),
                        "worker_status": receipt.get("status"),
                        "output_ref": receipt.get("output_ref"),
                        "transcript_ref": receipt.get("transcript_ref"),
                    },
                )
        notifier = self._notifier()
        await self._emit_workflow_milestone(
            notifier=notifier,
            run_id=run_id,
            task_id=task_id,
            milestone="started",
        )
        if skill_probe is not None:
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_skill_receipt_validation",
                payload={
                    "task_id": task_id,
                    "gate": "workflow_start",
                    "status": "accepted" if skill_probe.ok else "blocked",
                    "probe": asdict(skill_probe),
                    "tool_receipts": receipt_payloads,
                },
            )
            if not skill_probe.ok:
                final_payload = {
                    "task_id": task_id,
                    "gate": "workflow_start",
                    "status": "blocked",
                    "attempts": 0,
                    "handoff_packet_path": None,
                    "probes": {"P12": asdict(skill_probe)},
                    "outcome": None,
                    "escalation": {
                        "type": "skill_receipt_validation",
                        "reason": skill_probe.reason,
                        "details": skill_probe.details,
                    },
                    "artifact_rigor": {
                        "status": "blocked",
                        "reason": "missing_prd_tdd_skill_receipts",
                    },
                }
                self.state.write_event(
                    run_id=run_id,
                    source="dual_agent",
                    kind="dual_agent_gate_result",
                    payload=final_payload,
                )
                self.state.record_dual_agent_workflow_step(
                    run_id=run_id,
                    task_id=task_id,
                    gate="workflow_start",
                    status="blocked",
                    attempt_count=0,
                    latest_event_id=self.state.latest_event_id(run_id),
                )
                self.state.update_dual_agent_workflow(
                    run_id=run_id,
                    task_id=task_id,
                    status="blocked",
                    current_gate="workflow_start",
                )
                await self._emit_workflow_milestone(
                    notifier=notifier,
                    run_id=run_id,
                    task_id=task_id,
                    milestone="needs_user_input",
                    gate="workflow_start",
                )
                return self._workflow_result(
                    run_id=run_id,
                    task_id=task_id,
                    status="blocked",
                    current_gate="workflow_start",
                    steps=[_workflow_step_dict("workflow_start", "blocked", 0)],
                    final_gate_result=final_payload,
                    cwd=cwd,
                    screenshots=screenshot_payloads,
                    visual_evidence_policy=visual_policy,
                    workflow_route=workflow_route,
                )

        if _requires_p13_receipt_validation(
            execution_layer_mode,
            agentic_policy["agentic_lead_policy"],
        ):
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_dynamic_workflow_receipt_validation",
                payload={
                    "task_id": task_id,
                    "gate": "workflow_start",
                    "status": "accepted" if dynamic_workflow_probe.ok else "blocked",
                    "probe": asdict(dynamic_workflow_probe),
                    "tool_receipts": receipt_payloads,
                    "tool_calls": [ensure_tool_call_timing(dynamic_receipt_tool_call)],
                },
            )
            if not dynamic_workflow_probe.ok:
                final_payload = {
                    "task_id": task_id,
                    "gate": "workflow_start",
                    "status": "blocked",
                    "attempts": 0,
                    "handoff_packet_path": None,
                    "probes": {"P13": asdict(dynamic_workflow_probe)},
                    "outcome": None,
                    "escalation": {
                        "type": "dynamic_workflow_receipt_validation",
                        "reason": dynamic_workflow_probe.reason,
                        "details": dynamic_workflow_probe.details,
                    },
                    "artifact_rigor": {
                        "status": "blocked",
                        "reason": dynamic_workflow_probe.reason,
                    },
                }
                self.state.write_event(
                    run_id=run_id,
                    source="dual_agent",
                    kind="dual_agent_gate_result",
                    payload=final_payload,
                )
                self.state.record_dual_agent_workflow_step(
                    run_id=run_id,
                    task_id=task_id,
                    gate="workflow_start",
                    status="blocked",
                    attempt_count=0,
                    latest_event_id=self.state.latest_event_id(run_id),
                )
                self.state.update_dual_agent_workflow(
                    run_id=run_id,
                    task_id=task_id,
                    status="blocked",
                    current_gate="workflow_start",
                )
                await self._emit_workflow_milestone(
                    notifier=notifier,
                    run_id=run_id,
                    task_id=task_id,
                    milestone="needs_user_input",
                    gate="workflow_start",
                )
                return self._workflow_result(
                    run_id=run_id,
                    task_id=task_id,
                    status="blocked",
                    current_gate="workflow_start",
                    steps=[_workflow_step_dict("workflow_start", "blocked", 0)],
                    final_gate_result=final_payload,
                    cwd=cwd,
                    screenshots=screenshot_payloads,
                    visual_evidence_policy=visual_policy,
                    workflow_route=workflow_route,
                )
            if (
                dynamic_preparation is not None
                and dynamic_preparation.synthesis.get("status") == "blocked"
            ):
                synthesis_probe = ProbeResult(
                    "P14",
                    "red",
                    "dynamic_workflow_synthesis_blocked",
                    dynamic_preparation.synthesis,
                )
                self.state.write_event(
                    run_id=run_id,
                    source="dual_agent",
                    kind="dual_agent_dynamic_workflow_synthesis",
                    payload={
                        "task_id": task_id,
                        "gate": "workflow_start",
                        "status": "blocked",
                        "synthesis": dynamic_preparation.synthesis,
                    },
                )
                final_payload = {
                    "task_id": task_id,
                    "gate": "workflow_start",
                    "status": "blocked",
                    "attempts": 0,
                    "handoff_packet_path": None,
                    "probes": {
                        "P13": asdict(dynamic_workflow_probe),
                        "P14": asdict(synthesis_probe),
                    },
                    "outcome": None,
                    "escalation": {
                        "type": "dynamic_workflow_synthesis",
                        "reason": synthesis_probe.reason,
                        "details": synthesis_probe.details,
                    },
                    "artifact_rigor": {
                        "status": "blocked",
                        "reason": "dynamic_workflow_synthesis_blocked",
                    },
                }
                self.state.write_event(
                    run_id=run_id,
                    source="dual_agent",
                    kind="dual_agent_gate_result",
                    payload=final_payload,
                )
                self.state.record_dual_agent_workflow_step(
                    run_id=run_id,
                    task_id=task_id,
                    gate="workflow_start",
                    status="blocked",
                    attempt_count=0,
                    latest_event_id=self.state.latest_event_id(run_id),
                )
                self.state.update_dual_agent_workflow(
                    run_id=run_id,
                    task_id=task_id,
                    status="blocked",
                    current_gate="workflow_start",
                )
                return self._workflow_result(
                    run_id=run_id,
                    task_id=task_id,
                    status="blocked",
                    current_gate="workflow_start",
                    steps=[_workflow_step_dict("workflow_start", "blocked", 0)],
                    final_gate_result=final_payload,
                    cwd=cwd,
                    screenshots=screenshot_payloads,
                    visual_evidence_policy=visual_policy,
                    workflow_route=workflow_route,
                )

        steps: list[dict[str, Any]] = [
            _workflow_step_dict(
                str(item["gate"]),
                "accepted",
                int(item["attempt_count"]),
            )
            for item in prior_resume["skipped_steps"]
        ]
        final_payload: dict[str, Any] | None = None
        for gate in prior_resume["pending_gates"]:
            cursor_reviews_this_gate = gate in selected_cursor_gates
            self.state.update_dual_agent_workflow(
                run_id=run_id,
                task_id=task_id,
                status="running",
                current_gate=gate,
            )
            await self._emit_workflow_milestone(
                notifier=notifier,
                run_id=run_id,
                task_id=task_id,
                milestone="gate_started",
                gate=gate,
            )
            accepted = False
            attempts = 0
            corrective_context = ""
            gate_rounds: list[GateRound] = []
            for round_index in range(1, max_rounds + 1):
                attempts = round_index
                # r-2026-06-12 (wall eight, B event 701068 / D event 708767):
                # capturing a fresh baseline per round means outcome_review
                # baselines AFTER the execution commit, so the declared
                # changed_files can never appear in the diff and
                # runtime_changed_files_missing_from_diff is structural.
                # Execution captures fresh per round (its rounds re-anchor
                # honestly) and PERSISTS the round-1 head to the artifact dir;
                # outcome_review REUSES that persisted pre-execution head —
                # including across process restarts, which is how parked
                # tasks resume — and only falls back to a fresh capture
                # (journaled via reason) when no persisted baseline exists.
                runtime_baseline = (
                    _runtime_baseline_for_gate(
                        cwd,
                        task_id=task_id,
                        gate=str(gate),
                        round_index=round_index,
                    )
                    if gate in {"execution", "outcome_review"}
                    else None
                )
                payload = await self.start_dual_agent_gate(
                    **self._workflow_gate_start_kwargs(
                        run_id=run_id,
                        task_id=task_id,
                        gate=gate,
                        intent=intent,
                        corrective_context=corrective_context,
                        lesson_task_class=str(workflow_route["lesson_task_class"]),
                        cwd=cwd,
                        planning_artifacts=gate_artifacts,
                        lesson_snapshot=workflow_route.get("lesson_snapshot")
                        if isinstance(workflow_route.get("lesson_snapshot"), dict)
                        else None,
                        round_index=round_index,
                    ),
                    cwd=cwd,
                    expected_specialists=[],
                    expected_decisions=[],
                    expected_objections=[],
                    quality=quality,
                    budget_usd=budget_usd,
                    timeout_s=timeout_s,
                    execution_layer_mode=execution_layer_mode,
                    dynamic_workflow_task_class=dynamic_workflow_task_class,
                    agentic_lead_policy=agentic_policy["agentic_lead_policy"],
                    min_subagents=agentic_policy["min_subagents"],
                    required_roles=agentic_policy["required_roles"],
                    solo_exception_for_artifact_only_gates=agentic_policy[
                        "solo_exception_for_artifact_only_gates"
                    ],
                    required_evidence_grade=agentic_policy["required_evidence_grade"],
                    planning_artifacts=gate_artifacts,
                    tool_receipts=receipt_payloads,
                    artifact_policy="strict",
                    user_facing=effective_user_facing and gate == "outcome_review",
                    screenshots=screenshot_payloads,
                    required_artifacts=required_artifacts_for_complexity(
                        gate=gate,
                        task_complexity=str(workflow_route["task_complexity"]),
                    ),
                    required_prerequisite_gates=prerequisites_for_route(
                        gate=gate,
                        route_gates=route_gates,
                    ),
                    required_planning_kinds=required_planning_kinds_for_complexity(
                        gate=gate,
                        task_complexity=str(workflow_route["task_complexity"]),
                    ),
                    sanitize_tool_receipts=False,
                )
                final_payload = payload
                claim_probe = None
                deliverable_probe = None
                runtime_probe = None
                cursor_result: CursorInvocationResult | None = None
                cursor_payload: dict[str, Any] | None = None
                cursor_tool_calls: list[dict[str, Any]] = []
                review_results: list[tuple[Any, CursorInvocationResult]] = []
                independent_reviewer_results: list[dict[str, Any]] = []
                independent_reviewer_adjudication: dict[str, Any] | None = None
                current_runtime_receipt_ids: set[str] = set()
                runtime_evidence_result = None
                supervisor_review_packet = None
                supervisor_review_packet_payload: dict[str, Any] | None = None
                review_packet_failures: list[Any] = []
                review_context_complete = True
                review_context_failures: list[dict[str, Any]] = []
                if payload.get("status") == "accepted" and gate in {"execution", "outcome_review"}:
                    runtime_evidence_result = collect_runtime_evidence(
                        cwd=cwd,
                        task_id=task_id,
                        run_id=run_id,
                        gate=str(gate),
                        round_index=round_index,
                        baseline=runtime_baseline or {},
                        outcome_payload=payload.get("outcome")
                        if isinstance(payload.get("outcome"), dict)
                        else {},
                        planning_artifacts=gate_artifacts,
                        test_timeout_s=min(timeout_s, 120),
                    )
                    current_runtime_receipt_ids = _trusted_runtime_receipt_ids(
                        list(runtime_evidence_result.receipts)
                    )
                    runtime_receipts = _normalise_receipt_payloads(
                        list(runtime_evidence_result.receipts),
                        trusted_runtime_receipt_ids=current_runtime_receipt_ids,
                    )
                    receipt_payloads = _merge_runtime_receipt_payloads(
                        receipt_payloads,
                        runtime_receipts,
                    )
                    payload["runtime_evidence"] = runtime_evidence_result.event_payload
                    runtime_probe = runtime_evidence_result.probe
                    self.state.write_event(
                        run_id=run_id,
                        source="dual_agent",
                        kind="dual_agent_runtime_evidence",
                        payload=runtime_evidence_result.event_payload,
                    )
                    self.export_gate_artifacts(
                        run_id=run_id,
                        task_id=task_id,
                        cwd=cwd,
                        screenshots=screenshot_payloads,
                    )
                if gate == "outcome_review" and payload.get("status") == "accepted":
                    claim_probe = verify_workflow_claims(
                        outcome_payload=payload.get("outcome"),
                        user_facing=effective_user_facing,
                        screenshots=screenshot_payloads,
                        verified_claims=verified_claims,
                        tool_receipts=receipt_payloads,
                        trusted_runtime_receipt_ids=current_runtime_receipt_ids,
                    )
                    payload["claim_verification"] = asdict(claim_probe)
                if payload.get("status") == "accepted" and gate in {"execution", "outcome_review"}:
                    deliverable_probe = verify_gate_deliverable_evidence(
                        gate=str(gate),
                        task_id=task_id,
                        intent=intent,
                        outcome_payload=payload.get("outcome")
                        if isinstance(payload.get("outcome"), dict)
                        else None,
                        tool_receipts=receipt_payloads,
                        trusted_runtime_receipt_ids=current_runtime_receipt_ids,
                    )
                    payload.setdefault("probes", {})["P11"] = asdict(deliverable_probe)
                if (
                    cursor_reviews_this_gate
                    and payload.get("status") == "accepted"
                    and (deliverable_probe is None or deliverable_probe.ok)
                ):
                    cursor_evidence_refs = _receipt_evidence_refs(receipt_payloads, screenshot_payloads)
                    cursor_would_change_if = (
                        "Cursor finds an unresolved blocker, missing receipt, "
                        "or contradiction in the evidence."
                    )
                    independent_reviewers = configured_reviewers(
                        reviewer_output_mode=reviewer_output_mode_value,
                        reviewer_model=reviewer_model_value,
                        runner=self.cursor_runner,
                        codex_runner=self.codex_runner,
                    )
                    roster = _reviewer_roster_preflight(
                        task_id=task_id,
                        run_id=run_id,
                        gate=str(gate),
                        reviewers=independent_reviewers,
                        cfg=self.cfg,
                        cursor_runner=self.cursor_runner,
                        codex_runner=self.codex_runner,
                    )
                    available_reviewer_ids = set(roster.available_worker_ids())
                    available_reviewers = [
                        reviewer
                        for reviewer in independent_reviewers
                        if reviewer.spec.reviewer_id in available_reviewer_ids
                    ]
                    self.state.write_event(
                        run_id=run_id,
                        source="dual_agent",
                        kind=WORKER_ROSTER_CHECKED,
                        payload={
                            "schema_version": "supervisor-worker-roster/v1",
                            **build_roster_payload(roster),
                        },
                    )
                    review_branch = _git_branch(cwd)
                    for unavailable_entry in (
                        entry for entry in roster.entries if not entry.available
                    ):
                        cancelled = WorkerDispatch(
                            task_id=task_id,
                            run_id=run_id,
                            gate=str(gate),
                            worker_id=unavailable_entry.worker_id,
                            reviewer_id=unavailable_entry.worker_id,
                            purpose=unavailable_entry.purpose,
                            provider_family=unavailable_entry.provider_family,
                            runtime=unavailable_entry.runtime,
                            model=unavailable_entry.model,
                            status="cancelled",
                            started_at=_utc_now_iso(),
                            completed_at=_utc_now_iso(),
                            worktree_ref=str(Path(cwd).expanduser()),
                            branch=review_branch,
                            reason=(
                                unavailable_entry.failure_reason
                                or unavailable_entry.boot_status
                                or "reviewer_unavailable"
                            ),
                            extra={
                                "boot_status": unavailable_entry.boot_status,
                                "availability": "unavailable",
                            },
                        )
                        self.state.write_event(
                            run_id=run_id,
                            source="dual_agent",
                            kind=WORKER_CANCELLED,
                            payload={
                                "schema_version": "supervisor-worker-dispatch/v1",
                                **build_dispatch_payload(cancelled),
                            },
                        )
                    cross_vendor_selection = select_cross_vendor_reviewer(
                        implementation_provider_family="anthropic",
                        roster=roster,
                        policy=(
                            "block"
                            if reviewer_policy == "block"
                            else "degraded"
                        ),
                        task_id=task_id,
                        run_id=run_id,
                        gate=str(gate),
                    )
                    cross_vendor_kind = (
                        CROSS_VENDOR_REVIEW_SELECTED
                        if cross_vendor_selection.selection_status == "selected"
                        else DEGRADED_REVIEW_UNAVAILABLE
                    )
                    self.state.write_event(
                        run_id=run_id,
                        source="dual_agent",
                        kind=cross_vendor_kind,
                        payload={
                            "schema_version": "supervisor-cross-vendor-review/v1",
                            **build_cross_vendor_payload(cross_vendor_selection),
                        },
                    )
                    if cross_vendor_selection.selection_status == "block":
                        payload["cross_vendor_review"] = build_cross_vendor_payload(
                            cross_vendor_selection
                        )
                    supervisor_review_packet, supervisor_review_packet_payload, review_packet_failures = (
                        _build_supervisor_review_packet(
                            task_id=task_id,
                            run_id=run_id,
                            gate=str(gate),
                            round_index=round_index,
                            cwd=cwd,
                            gate_artifacts=gate_artifacts,
                            runtime_baseline=runtime_baseline,
                            receipt_payloads=receipt_payloads,
                            trusted_runtime_receipt_ids=current_runtime_receipt_ids,
                            reviewer_ids=[
                                reviewer.spec.reviewer_id
                                for reviewer in available_reviewers
                            ],
                            policy_overlay_hash=str(
                                workflow_route.get("policy_overlay_hash") or ""
                            ),
                            lesson_hashes=[
                                str(item.get("lesson_id") or item.get("hash"))
                                for item in (
                                    workflow_route.get("lesson_snapshot", {}).get("lessons", [])
                                    if isinstance(workflow_route.get("lesson_snapshot"), dict)
                                    else []
                                )
                                if isinstance(item, dict)
                            ],
                        )
                    )
                    self.state.write_event(
                        run_id=run_id,
                        source="dual_agent",
                        kind=review_packet_event_kind(),
                        payload=supervisor_review_packet_payload,
                    )
                    review_request_event_id = self._write_interaction_message(
                        run_id=run_id,
                        message=AgentMailboxMessage(
                            task_id=task_id,
                            gate=gate,
                            round_index=round_index,
                            sender="codex",
                            recipient="cursor",
                            message_type="review_request",
                            persona_id="codex.lifecycle_reviewer",
                            content=_cursor_review_instruction(
                                gate=gate,
                                intent=intent,
                                corrective_context=corrective_context,
                            ),
                            addresses=_interaction_addresses(payload),
                            claims=_outcome_claims(payload),
                            objections=_outcome_objections(payload),
                            questions=(
                                "Do the planning artifacts, Claude outcome, and evidence receipts justify advancing this gate?",
                            ),
                            tool_receipts=tuple(receipt_payloads),
                            evidence_refs=cursor_evidence_refs,
                            raw_transcript_refs=_raw_transcript_refs(payload),
                            would_change_if=cursor_would_change_if,
                            critical_review=critical_review_from_outcome(
                                payload.get("outcome") if isinstance(payload.get("outcome"), dict) else None,
                                evidence_refs=cursor_evidence_refs,
                                would_change_if=cursor_would_change_if,
                            ),
                            review_packet=supervisor_review_packet_payload,
                            artifacts=planning_artifact_refs(gate_artifacts),
                            metadata={
                                "claude_outcome": payload.get("outcome"),
                                "review_policy": "Cursor reviews only after Claude gate acceptance.",
                                "supervisor_review_packet": supervisor_review_packet_payload,
                                "cross_vendor_review": build_cross_vendor_payload(cross_vendor_selection),
                            },
                        ),
                    )
                    with timed_tool_call(
                        "invoke_cursor_agent",
                        args={
                            "task_id": task_id,
                            "gate": gate,
                            "quality": quality,
                            "model": cursor_model,
                            "reviewer_model": reviewer_model_value,
                            "reviewer_output_mode": reviewer_output_mode_value,
                            "reviewer_max_tokens": reviewer_max_tokens_value,
                            "reviewer_infra_retry_limit": reviewer_infra_retry_limit_value,
                            "reviewer_infra_retry_backoff_s": reviewer_infra_retry_backoff_s_value,
                            "timeout_s": timeout_s,
                            "reviewer_count": len(available_reviewers),
                            "reviewer_ids": [reviewer.spec.reviewer_id for reviewer in available_reviewers],
                            "planning_artifact_count": len(gate_artifacts),
                            "receipt_count": len(receipt_payloads),
                        },
                        receipt_ids=_receipt_ids(receipt_payloads),
                    ) as cursor_tool_call:
                        reviewer_request = CursorInvocationRequest(
                            task_id=task_id,
                            gate=gate,  # type: ignore[arg-type]
                            instruction=_cursor_review_instruction(
                                gate=gate,
                                intent=intent,
                                corrective_context=corrective_context,
                            ),
                            cwd=cwd,
                            claude_outcome=payload.get("outcome")
                            if isinstance(payload.get("outcome"), dict)
                            else None,
                            planning_artifacts=tuple(
                                artifact
                                for artifact in (
                                    _maybe_artifact(item)
                                    for item in gate_artifacts
                                )
                                if artifact is not None
                            ),
                            quality=quality,
                            model=cursor_model,
                            reviewer_model=reviewer_model_value,
                            reviewer_output_mode=reviewer_output_mode_value,  # type: ignore[arg-type]
                            reviewer_max_tokens=reviewer_max_tokens_value,
                            reviewer_infra_retry_limit=reviewer_infra_retry_limit_value,
                            reviewer_infra_retry_backoff_s=reviewer_infra_retry_backoff_s_value,
                            openai_api_key=self.cfg.models.openai_api_key,
                            openai_base_url=self.cfg.models.openai_base_url,
                            timeout_s=timeout_s,
                            tool_receipts=tuple(receipt_payloads),
                            review_packet=supervisor_review_packet_payload,
                        )
                        for independent_reviewer in available_reviewers:
                            spec = independent_reviewer.spec
                            reviewer_started_at = _utc_now_iso()
                            base_dispatch = WorkerDispatch(
                                task_id=task_id,
                                run_id=run_id,
                                gate=str(gate),
                                worker_id=spec.reviewer_id,
                                reviewer_id=spec.reviewer_id,
                                purpose="review",
                                provider_family=spec.provider_family,
                                runtime=spec.runtime,
                                model=spec.model or "",
                                status="session_created",
                                started_at=reviewer_started_at,
                                worktree_ref=str(Path(cwd).expanduser()),
                                branch=review_branch,
                            )
                            self.state.write_event(
                                run_id=run_id,
                                source="dual_agent",
                                kind=WORKER_SESSION_CREATED,
                                payload={
                                    "schema_version": "supervisor-worker-dispatch/v1",
                                    **build_dispatch_payload(base_dispatch),
                                },
                            )
                            dispatched = WorkerDispatch(
                                **{
                                    **base_dispatch.__dict__,
                                    "status": "dispatched",
                                }
                            )
                            self.state.write_event(
                                run_id=run_id,
                                source="dual_agent",
                                kind=WORKER_DISPATCHED,
                                payload={
                                    "schema_version": "supervisor-worker-dispatch/v1",
                                    **build_dispatch_payload(dispatched),
                                },
                            )
                            reviewer_started = time.monotonic()
                            result = independent_reviewer.review(reviewer_request)
                            receipt_id = f"reviewer-{spec.reviewer_id}-{gate}-{round_index}"
                            attempt = EvidenceAttempt(
                                attempt_id=compute_attempt_id(
                                    task_id=task_id,
                                    run_id=run_id,
                                    gate=str(gate),
                                    worker_id=spec.reviewer_id,
                                    purpose="review",
                                    finding_kind="independent_reviewer_result",
                                    receipt_ids=[receipt_id],
                                ),
                                task_id=task_id,
                                run_id=run_id,
                                gate=str(gate),
                                worker_id=spec.reviewer_id,
                                purpose="review",
                                finding_kind="independent_reviewer_result",
                                receipt_ids=[receipt_id],
                                output_hash=hashlib.sha256(
                                    (
                                        result.outcome.model_dump_json()
                                        if result.outcome is not None
                                        else ""
                                    ).encode("utf-8")
                                ).hexdigest()
                                if result.outcome is not None
                                else None,
                                transcript_hash=hashlib.sha256(
                                    (result.transcript or "").encode("utf-8")
                                ).hexdigest(),
                                finding_summary=(
                                    result.outcome.summary
                                    if result.outcome is not None
                                    else result.probe.reason
                                ),
                            )
                            self.state.write_event(
                                run_id=run_id,
                                source="dual_agent",
                                kind=EVIDENCE_ATTEMPT_RECORDED,
                                payload={
                                    "schema_version": "supervisor-evidence-attempt-link/v1",
                                    **build_evidence_attempt_payload(attempt),
                                },
                            )
                            terminal_status = _review_worker_terminal_status(result)
                            completed = WorkerDispatch(
                                task_id=task_id,
                                run_id=run_id,
                                gate=str(gate),
                                worker_id=spec.reviewer_id,
                                reviewer_id=spec.reviewer_id,
                                purpose="review",
                                provider_family=spec.provider_family,
                                runtime=result.reviewer_runtime or spec.runtime,
                                model=result.model or spec.model or "",
                                status=terminal_status,
                                started_at=reviewer_started_at,
                                completed_at=_utc_now_iso(),
                                wall_clock_s=round(time.monotonic() - reviewer_started, 3),
                                duration_ms=result.duration_ms,
                                evidence_attempt_id=attempt.attempt_id,
                                receipt_ids=[receipt_id],
                                worktree_ref=str(Path(cwd).expanduser()),
                                branch=review_branch,
                                transcript_hash=attempt.transcript_hash,
                                output_hash=attempt.output_hash,
                                session_id=result.agent_id,
                                conversation_id=result.run_id,
                                reason=_review_worker_terminal_reason(result, terminal_status),
                                extra={
                                    "decision": _review_worker_decision(result),
                                    "failure_classification": result.failure_classification,
                                    "recoverable": result.recoverable,
                                },
                            )
                            self.state.write_event(
                                run_id=run_id,
                                source="dual_agent",
                                kind=_review_worker_event_kind(terminal_status),
                                payload={
                                    "schema_version": "supervisor-worker-dispatch/v1",
                                    **build_dispatch_payload(completed),
                                },
                            )
                            review_results.append((spec, result))
                    cursor_result = review_results[0][1] if review_results else None
                    if cursor_result is not None:
                        cursor_tool_call.update(_cursor_tool_call_fields(cursor_result))
                    cursor_tool_call.update({
                        "reviewer_count": len(review_results),
                        "reviewer_ids": [spec.reviewer_id for spec, _result in review_results],
                        "reviewer_result_summary": [
                            {
                                "reviewer_id": spec.reviewer_id,
                                "accepted": cursor_accepts(result),
                                "probe_status": result.probe.status,
                                "probe_reason": result.probe.reason,
                                "failure_classification": result.failure_classification,
                                "recoverable": result.recoverable,
                                "reviewer_runtime": result.reviewer_runtime,
                                "model": result.model,
                            }
                            for spec, result in review_results
                        ],
                    })
                    cursor_tool_call = ensure_tool_call_timing(cursor_tool_call)
                    cursor_tool_calls = [cursor_tool_call]
                    independent_reviewer_results = independent_reviewer_results_from_review_results(
                        review_results,
                        task_id=task_id,
                        gate=str(gate),
                        round_index=round_index,
                    )
                    if supervisor_review_packet is not None:
                        for spec, result in review_results:
                            if result.outcome is None:
                                continue
                            outcome_payload = result.outcome.model_dump()
                            context_receipt = reviewer_context_receipt_from_payload(
                                reviewer_id=spec.reviewer_id,
                                payload=outcome_payload,
                            )
                            if context_receipt is None:
                                context_receipt = reviewer_context_receipt_from_payload(
                                    reviewer_id=spec.reviewer_id,
                                    payload={
                                        "reviewer_context_receipt": {
                                            "reviewer_id": spec.reviewer_id,
                                            "files_reviewed": [],
                                            "criteria_checked": [],
                                            "receipts_considered": [],
                                            "missing_context": [
                                                "reviewer_context_receipt_missing"
                                            ],
                                        }
                                    },
                                )
                            if context_receipt is None:
                                continue
                            context_validation = validate_reviewer_context_receipt(
                                supervisor_review_packet,
                                context_receipt,
                            )
                            context_payload = context_validation_payload(
                                packet=supervisor_review_packet,
                                reviewer_id=spec.reviewer_id,
                                receipt=context_receipt,
                                validation=context_validation,
                            )
                            self.state.write_event(
                                run_id=run_id,
                                source="dual_agent",
                                kind="supervisor_review_context_validation",
                                payload=context_payload,
                            )
                            if not context_validation.complete:
                                review_context_complete = False
                                review_context_failures.append(context_payload)
                    cursor_payload = _cursor_result_payload(cursor_result) if cursor_result is not None else None
                    if cursor_payload is None:
                        cursor_payload = {"schema_version": "independent-reviewer-result/v1", "accepted": False}
                    cursor_payload["independent_reviewer_results"] = independent_reviewer_results
                    if supervisor_review_packet_payload is not None:
                        cursor_payload["supervisor_review_packet"] = supervisor_review_packet_payload
                    cursor_payload["cross_vendor_review"] = build_cross_vendor_payload(
                        cross_vendor_selection
                    )
                    if review_context_failures:
                        cursor_payload["review_context_validation"] = {
                            "status": "failed",
                            "reason": review_context_incomplete_reason(),
                            "failures": review_context_failures,
                        }
                    payload["cursor_review"] = cursor_payload
                    payload["independent_reviewer"] = cursor_payload
                    payload["independent_reviewer_results"] = independent_reviewer_results
                    cursor_response_would_change_if = (
                        "Claude or Codex provides evidence resolving Cursor's objections."
                    )
                    self._write_interaction_message(
                        run_id=run_id,
                        message=AgentMailboxMessage(
                            task_id=task_id,
                            gate=gate,
                            round_index=round_index,
                            sender="cursor",
                            recipient="codex",
                            message_type="review_response",
                            persona_id="cursor.independent_reviewer",
                            content=(
                                cursor_result.outcome.summary
                                if cursor_result.outcome is not None
                                else cursor_result.probe.reason
                            ),
                            addresses=(f"event:{review_request_event_id}",),
                            confidence=outcome_confidence_report(
                                cursor_result.outcome.model_dump()
                                if cursor_result.outcome is not None else None,
                                source="cursor",
                            ),
                            claims=tuple(cursor_result.outcome.claims)
                            if cursor_result.outcome is not None else (),
                            objections=tuple(cursor_result.outcome.objections)
                            if cursor_result.outcome is not None else (cursor_result.probe.reason,),
                            questions=(),
                            tool_receipts=tuple(receipt_payloads),
                            evidence_refs=cursor_evidence_refs,
                            raw_transcript_refs=(
                                {
                                    "kind": "cursor_transcript_tail",
                                    "ref": f"tri_agent_cursor_review:{task_id}:{gate}:{round_index}",
                                    "chars": min(len(cursor_result.transcript), 4000),
                                },
                            ),
                            would_change_if=cursor_response_would_change_if,
                            critical_review=critical_review_from_outcome(
                                cursor_result.outcome.model_dump()
                                if cursor_result.outcome is not None else None,
                                decision="accept" if cursor_accepts(cursor_result) else "revise",
                                evidence_refs=cursor_evidence_refs,
                                would_change_if=cursor_response_would_change_if,
                            ),
                            artifacts=planning_artifact_refs(gate_artifacts),
                            metadata={
                                "cursor_review": cursor_payload,
                                "independent_reviewer": cursor_payload,
                                "independent_reviewer_results": independent_reviewer_results,
                                "tool_calls": [cursor_tool_call],
                            },
                        ),
                    )
                claude_decision = (
                    "accept"
                    if payload.get("status") == "accepted" and claude_accepts(payload.get("outcome"))
                    else "revise"
                )
                cursor_decision = (
                    "accept"
                    if not cursor_reviews_this_gate
                    else "revise"
                )
                independent_reviewer_panel_decision = evaluate_reviewer_panel(
                    independent_reviewer_results,
                    low_confidence_threshold=reviewer_low_confidence_threshold_value,
                    calibration=reviewer_panel_calibration,
                )
                independent_reviewer_adjudication = adjudicate_reviewer_panel(
                    independent_reviewer_results,
                    cwd=cwd,
                )
                if independent_reviewer_adjudication is not None:
                    independent_reviewer_panel_decision = {
                        **independent_reviewer_panel_decision,
                        "adjudication": independent_reviewer_adjudication,
                    }
                    if (
                        independent_reviewer_panel_decision["decision"] == "accept"
                        and independent_reviewer_adjudication["decision"] == "escalate"
                    ):
                        independent_reviewer_panel_decision = {
                            **independent_reviewer_panel_decision,
                            "decision": "escalate",
                            "reason": "adjudicated_strong_objection",
                        }
                cursor_decision = (
                    "accept"
                    if independent_reviewer_panel_decision["decision"] == "accept"
                    else "revise"
                )
                panel_recoverable_failures = _panel_recoverable_infrastructure_failures(
                    independent_reviewer_results
                )
                reviewer_failure_classification = _panel_failure_classification(
                    panel_recoverable_failures,
                    cursor_payload,
                )
                if cursor_payload is not None:
                    cursor_payload["independent_reviewer_results"] = independent_reviewer_results
                    cursor_payload["independent_reviewer_panel_decision"] = (
                        independent_reviewer_panel_decision
                    )
                    if panel_recoverable_failures:
                        cursor_payload["panel_recoverable_failures"] = panel_recoverable_failures
                        cursor_payload["unavailable_reviewer_ids"] = [
                            str(item.get("reviewer_id"))
                            for item in panel_recoverable_failures
                            if str(item.get("reviewer_id") or "").strip()
                        ]
                        cursor_payload["panel_failure_classification"] = (
                            reviewer_failure_classification
                        )
                payload["independent_reviewer_panel_decision"] = independent_reviewer_panel_decision
                payload["independent_reviewer_results"] = independent_reviewer_results
                claim_payload = asdict(claim_probe) if claim_probe is not None else None
                cursor_infrastructure_failure = bool(panel_recoverable_failures)
                reviewer_unavailable_recovery: dict[str, Any] | None = None
                available_reviewers_accept = (
                    payload.get("status") == "accepted"
                    and claude_decision == "accept"
                    and (runtime_probe is None or runtime_probe.ok)
                    and (claim_probe is None or claim_probe.ok)
                    and _panel_available_reviewers_accept(independent_reviewer_results)
                )
                if cursor_infrastructure_failure and cursor_payload is not None:
                    reviewer_unavailable_recovery = _reviewer_unavailable_recovery_plan(
                        state=self.state,
                        run_id=run_id,
                        task_id=task_id,
                        gate=str(gate),
                        policy=reviewer_policy,
                        classification=reviewer_failure_classification,
                        available_reviewers_accept=available_reviewers_accept,
                        agentic_policy=agentic_policy,
                        required_evidence_grade=agentic_policy["required_evidence_grade"],
                        user_facing=effective_user_facing and gate == "outcome_review",
                    )
                    reviewer_unavailable_recovery["unavailable_reviewers"] = [
                        str(item.get("reviewer_id"))
                        for item in panel_recoverable_failures
                        if str(item.get("reviewer_id") or "").strip()
                    ]
                    cursor_payload = {
                        **cursor_payload,
                        "reviewer_unavailable_recovery": reviewer_unavailable_recovery,
                    }
                    payload["cursor_review"] = cursor_payload
                    payload["independent_reviewer"] = cursor_payload
                    payload["independent_reviewer_results"] = independent_reviewer_results
                    payload["independent_reviewer_panel_decision"] = independent_reviewer_panel_decision
                if cursor_payload is not None and cursor_reviews_this_gate:
                    if independent_reviewer_adjudication is not None:
                        self.state.write_event(
                            run_id=run_id,
                            source="dual_agent",
                            kind="independent_reviewer_adjudication",
                            payload={
                                "task_id": task_id,
                                "gate": gate,
                                "adjudication": independent_reviewer_adjudication,
                                "independent_reviewer_panel_decision": independent_reviewer_panel_decision,
                                "independent_reviewer_results": independent_reviewer_results,
                            },
                        )
                    self.state.write_event(
                        run_id=run_id,
                        source="dual_agent",
                        kind="independent_reviewer_review",
                        payload={
                            "task_id": task_id,
                            "gate": gate,
                            "independent_reviewer_results": independent_reviewer_results,
                            "independent_reviewer_panel_decision": independent_reviewer_panel_decision,
                            "cursor_review": cursor_payload,
                            "independent_reviewer": cursor_payload,
                            "tool_calls": cursor_tool_calls,
                        },
                    )
                    self.state.write_event(
                        run_id=run_id,
                        source="dual_agent",
                        kind="tri_agent_cursor_review",
                        payload={
                            "task_id": task_id,
                            "gate": gate,
                            "cursor_review": cursor_payload,
                            "independent_reviewer": cursor_payload,
                            "independent_reviewer_results": independent_reviewer_results,
                            "independent_reviewer_panel_decision": independent_reviewer_panel_decision,
                            "tool_calls": cursor_tool_calls,
                        },
                    )
                codex_decision = (
                    "accept"
                    if (
                        cursor_infrastructure_failure
                        and reviewer_unavailable_recovery is not None
                        and reviewer_unavailable_recovery["decision"] == "proceed_degraded"
                        and available_reviewers_accept
                    )
                    else "accept"
                    if (
                        payload.get("status") == "accepted"
                        and (runtime_probe is None or runtime_probe.ok)
                        and (deliverable_probe is None or deliverable_probe.ok)
                        and (claim_probe is None or claim_probe.ok)
                        and not review_packet_failures
                        and review_context_complete
                        and not (
                            isinstance(payload.get("cross_vendor_review"), dict)
                            and payload["cross_vendor_review"].get("selection_status") == "block"
                        )
                        and cursor_decision == "accept"
                    )
                    else "deny"
                    if payload.get("status") == "blocked"
                    and not _payload_blocked_for_lead_revision(payload)
                    else "revise"
                )
                payload["cursor_decision"] = cursor_decision
                payload["codex_decision"] = codex_decision
                payload["claude_decision"] = claude_decision
                probe_statuses = {
                    probe_id: str(probe.get("status") or "")
                    for probe_id, probe in (payload.get("probes") or {}).items()
                    if isinstance(probe, dict)
                }
                objection = (
                    "both agents accepted"
                    if (
                        codex_decision == "accept"
                        and claude_decision == "accept"
                        and cursor_decision == "accept"
                    )
                    else _workflow_round_objection(
                        payload=payload,
                        runtime_probe=asdict(runtime_probe) if runtime_probe is not None else None,
                        deliverable_probe=asdict(deliverable_probe) if deliverable_probe is not None else None,
                        claim_probe=claim_payload,
                        cursor_review=cursor_payload,
                        round_index=round_index,
                        max_rounds=max_rounds,
                    )
                )
                evidence_refs = _receipt_evidence_refs(receipt_payloads, screenshot_payloads)
                codex_report = codex_confidence_report(
                    decision=codex_decision,
                    gate_status=str(payload.get("status") or ""),
                    probe_statuses=probe_statuses,
                    claim_verification=claim_payload,
                    cursor_review=cursor_payload,
                )
                claude_report = outcome_confidence_report(
                    payload.get("outcome") if isinstance(payload.get("outcome"), dict) else None,
                    source="claude_code",
                )
                review_packet = codex_review_packet(
                    task_id=task_id,
                    gate=gate,
                    decision=codex_decision,
                    confidence=codex_report,
                    probe_statuses=probe_statuses,
                    claim_verification=claim_payload,
                    cursor_review=cursor_payload,
                    objection=objection,
                    evidence_refs=evidence_refs,
                    tool_receipts=tuple(receipt_payloads),
                )
                if (
                    codex_decision == "accept"
                    and review_packet.get("round_policy", {}).get("force_next_round")
                ):
                    codex_decision = "revise"
                    blocking = review_packet.get("round_policy", {}).get("blocking_findings") or []
                    objection = (
                        f"blocking_review_findings: {', '.join(str(item) for item in blocking)}"
                        if blocking else "blocking_review_findings"
                    )
                    codex_report = codex_confidence_report(
                        decision=codex_decision,
                        gate_status=str(payload.get("status") or ""),
                        probe_statuses=probe_statuses,
                        claim_verification=claim_payload,
                        cursor_review=cursor_payload,
                    )
                    review_packet = codex_review_packet(
                        task_id=task_id,
                        gate=gate,
                        decision=codex_decision,
                        confidence=codex_report,
                        probe_statuses=probe_statuses,
                        claim_verification=claim_payload,
                        cursor_review=cursor_payload,
                        objection=objection,
                        evidence_refs=evidence_refs,
                        tool_receipts=tuple(receipt_payloads),
                    )
                round_result = self.record_gate_round(
                    run_id=run_id,
                    task_id=task_id,
                    gate=gate,  # type: ignore[arg-type]
                    round_index=round_index,
                    codex_decision=codex_decision,
                    claude_decision=claude_decision,
                    codex_confidence=codex_report.value if codex_report.value is not None else 0.0,
                    claude_confidence=claude_report.value if claude_report.value is not None else 0.0,
                    objection=objection,
                    cwd=cwd,
                )
                self._write_interaction_message(
                    run_id=run_id,
                        message=AgentMailboxMessage(
                            task_id=task_id,
                            gate=gate,
                            round_index=round_index,
                            sender="codex",
                            recipient="supervisor",
                            message_type="gate_decision",
                            persona_id="codex.lifecycle_reviewer",
                            content=objection,
                            addresses=(f"event:{round_result['event_id']}",),
                            confidence=codex_report,
                            claims=(
                                f"codex_decision={codex_decision}",
                                f"claude_decision={claude_decision}",
                                f"cursor_decision={cursor_decision}",
                            ),
                            objections=() if codex_decision == "accept" else (objection,),
                            questions=(
                                "What corrective input should be applied before the next attempt?",
                            ) if codex_decision != "accept" else (),
                            tool_receipts=tuple(receipt_payloads),
                            evidence_refs=evidence_refs,
                            raw_transcript_refs=_raw_transcript_refs(payload),
                            would_change_if="All required probes, claim receipts, and optional Cursor review accept.",
                            critical_review=(
                                review_packet.get("critical_review")
                                if isinstance(review_packet.get("critical_review"), dict)
                                else None
                            ),
                            review_packet=review_packet,
                            artifacts=planning_artifact_refs(gate_artifacts),
                            metadata={
                                "codex_decision": codex_decision,
                                "claude_decision": claude_decision,
                                "cursor_decision": cursor_decision,
                                "independent_reviewer_panel_decision": independent_reviewer_panel_decision,
                                "round_event_id": round_result["event_id"],
                            },
                    ),
                )
                gate_rounds.append(_gate_round_from_payload(round_result["round"]))

                if cursor_infrastructure_failure:
                    classification = reviewer_failure_classification
                    recovery = reviewer_unavailable_recovery or {}
                    if recovery.get("decision") == "proceed_degraded":
                        recovery_event = self._record_reviewer_unavailable_recovery(
                            run_id=run_id,
                            task_id=task_id,
                            gate=str(gate),
                            status="proceeded_degraded",
                            policy=reviewer_policy,
                            classification=classification,
                            cursor_review=cursor_payload,
                            recovery=recovery,
                            available_reviewers={
                                "claude": claude_decision,
                                "codex": codex_decision,
                            },
                        )
                        cursor_payload["reviewer_unavailable_recovery"] = {
                            **recovery,
                            "event_id": recovery_event["event_id"],
                        }
                        payload["cursor_review"] = cursor_payload
                        payload["independent_reviewer"] = cursor_payload
                    elif reviewer_policy == "block":
                        recovery_event = self._record_reviewer_unavailable_recovery(
                            run_id=run_id,
                            task_id=task_id,
                            gate=str(gate),
                            status="blocked",
                            policy=reviewer_policy,
                            classification=classification,
                            cursor_review=cursor_payload,
                            recovery=recovery,
                            available_reviewers={
                                "claude": claude_decision,
                                "codex": codex_decision,
                            },
                        )
                        cursor_payload["reviewer_unavailable_recovery"] = {
                            **recovery,
                            "event_id": recovery_event["event_id"],
                        }
                        payload["cursor_review"] = cursor_payload
                        payload["independent_reviewer"] = cursor_payload
                        final_payload = self._write_workflow_gate_override(
                            run_id=run_id,
                            task_id=task_id,
                            gate=gate,
                            attempts=attempts,
                            reason=classification,
                            source_payload=payload,
                            claim_probe=claim_payload,
                        )
                        self.state.record_dual_agent_workflow_step(
                            run_id=run_id,
                            task_id=task_id,
                            gate=gate,
                            status="blocked",
                            attempt_count=attempts,
                            latest_event_id=self.state.latest_event_id(run_id),
                        )
                        self.state.update_dual_agent_workflow(
                            run_id=run_id,
                            task_id=task_id,
                            status="blocked",
                            current_gate=gate,
                        )
                        await self._emit_workflow_milestone(
                            notifier=notifier,
                            run_id=run_id,
                            task_id=task_id,
                            milestone="gate_blocked",
                            gate=gate,
                        )
                        await self._emit_workflow_milestone(
                            notifier=notifier,
                            run_id=run_id,
                            task_id=task_id,
                            milestone="needs_user_input",
                            gate=gate,
                        )
                        return self._workflow_result(
                            run_id=run_id,
                            task_id=task_id,
                            status="blocked",
                            current_gate=gate,
                            steps=steps + [_workflow_step_dict(gate, "blocked", attempts)],
                            final_gate_result=final_payload,
                            cwd=cwd,
                            screenshots=screenshot_payloads,
                            visual_evidence_policy=visual_policy,
                            workflow_route=workflow_route,
                        )
                    else:
                        escalation = await self._request_reviewer_unavailable_escalation(
                            notifier=notifier,
                            run_id=run_id,
                            task_id=task_id,
                            gate=str(gate),
                            policy=reviewer_policy,
                            classification=classification,
                            cursor_review=cursor_payload,
                            recovery=recovery,
                            available_reviewers={
                                "claude": claude_decision,
                                "codex": codex_decision,
                            },
                        )
                        recovery_event = self._record_reviewer_unavailable_recovery(
                            run_id=run_id,
                            task_id=task_id,
                            gate=str(gate),
                            status="paused_for_human",
                            policy=reviewer_policy,
                            classification=classification,
                            cursor_review=cursor_payload,
                            recovery={**recovery, "escalation": escalation},
                            available_reviewers={
                                "claude": claude_decision,
                                "codex": codex_decision,
                            },
                        )
                        cursor_payload["reviewer_unavailable_recovery"] = {
                            **recovery,
                            "event_id": recovery_event["event_id"],
                            "escalation": escalation,
                        }
                        payload["cursor_review"] = cursor_payload
                        payload["independent_reviewer"] = cursor_payload
                        final_payload = self._write_workflow_gate_override(
                            run_id=run_id,
                            task_id=task_id,
                            gate=gate,
                            attempts=attempts,
                            reason=classification,
                            source_payload=payload,
                            claim_probe=claim_payload,
                        )
                        final_payload["workflow_reviewer_unavailable_escalation"] = escalation
                        self.state.record_dual_agent_workflow_step(
                            run_id=run_id,
                            task_id=task_id,
                            gate=gate,
                            status="blocked",
                            attempt_count=attempts,
                            latest_event_id=self.state.latest_event_id(run_id),
                        )
                        self.state.update_dual_agent_workflow(
                            run_id=run_id,
                            task_id=task_id,
                            status="blocked",
                            current_gate=gate,
                        )
                        await self._emit_workflow_milestone(
                            notifier=notifier,
                            run_id=run_id,
                            task_id=task_id,
                            milestone="needs_user_input",
                            gate=gate,
                        )
                        return self._workflow_result(
                            run_id=run_id,
                            task_id=task_id,
                            status="blocked",
                            current_gate=gate,
                            steps=steps + [_workflow_step_dict(gate, "blocked", attempts)],
                            final_gate_result=final_payload,
                            cwd=cwd,
                            screenshots=screenshot_payloads,
                            visual_evidence_policy=visual_policy,
                            workflow_route=workflow_route,
                        )

                if (
                    payload.get("status") == "blocked"
                    and not _payload_blocked_for_lead_revision(payload)
                ):
                    self.state.record_dual_agent_workflow_step(
                        run_id=run_id,
                        task_id=task_id,
                        gate=gate,
                        status="blocked",
                        attempt_count=attempts,
                        latest_event_id=int(round_result["event_id"]),
                    )
                    self.state.update_dual_agent_workflow(
                        run_id=run_id,
                        task_id=task_id,
                        status="blocked",
                        current_gate=gate,
                    )
                    await self._emit_workflow_milestone(
                        notifier=notifier,
                        run_id=run_id,
                        task_id=task_id,
                        milestone="gate_blocked",
                        gate=gate,
                    )
                    await self._emit_workflow_milestone(
                        notifier=notifier,
                        run_id=run_id,
                        task_id=task_id,
                        milestone="needs_user_input",
                        gate=gate,
                    )
                    return self._workflow_result(
                        run_id=run_id,
                        task_id=task_id,
                        status="blocked",
                        current_gate=gate,
                        steps=steps + [_workflow_step_dict(gate, "blocked", attempts)],
                        final_gate_result=payload,
                        cwd=cwd,
                        screenshots=screenshot_payloads,
                        visual_evidence_policy=visual_policy,
                        workflow_route=workflow_route,
                    )

                if codex_decision == "accept" and claude_decision == "accept":
                    accepted = True
                    self.state.record_dual_agent_workflow_step(
                        run_id=run_id,
                        task_id=task_id,
                        gate=gate,
                        status="accepted",
                        attempt_count=attempts,
                        latest_event_id=int(round_result["event_id"]),
                    )
                    step = _workflow_step_dict(gate, "accepted", attempts)
                    steps.append(step)
                    await self._emit_workflow_milestone(
                        notifier=notifier,
                        run_id=run_id,
                        task_id=task_id,
                        milestone="gate_accepted",
                        gate=gate,
                    )
                    break

                final_payload = self._write_workflow_gate_override(
                    run_id=run_id,
                    task_id=task_id,
                    gate=gate,
                    attempts=attempts,
                    reason="runtime_evidence_failed"
                    if runtime_probe is not None and not runtime_probe.ok
                    else "claim_verification_failed"
                    if claim_probe is not None and not claim_probe.ok
                    else "deliverable_evidence_failed"
                    if deliverable_probe is not None and not deliverable_probe.ok
                    else "agents_not_converged",
                    source_payload=payload,
                    claim_probe=asdict(claim_probe) if claim_probe is not None else None,
                )
                corrective_context = objection

            if not accepted:
                if notifier is not None:
                    escalation = await request_deadlock_escalation(
                        state=self.state,
                        notifier=notifier,
                        run_id=run_id,
                        task_id=task_id,
                        gate=gate,  # type: ignore[arg-type]
                        rounds=gate_rounds,
                        per_gate_cap=max_rounds,
                        task_budget=max_rounds,
                    )
                    if final_payload is not None:
                        final_payload["workflow_deadlock_escalation"] = redact(asdict(escalation))
                self.state.record_dual_agent_workflow_step(
                    run_id=run_id,
                    task_id=task_id,
                    gate=gate,
                    status="blocked",
                    attempt_count=attempts,
                    latest_event_id=self.state.latest_event_id(run_id),
                )
                self.state.update_dual_agent_workflow(
                    run_id=run_id,
                    task_id=task_id,
                    status="blocked",
                    current_gate=gate,
                )
                await self._emit_workflow_milestone(
                    notifier=notifier,
                    run_id=run_id,
                    task_id=task_id,
                        milestone="v5_exhausted",
                        gate=gate,
                    )
                await self._emit_workflow_milestone(
                    notifier=notifier,
                    run_id=run_id,
                    task_id=task_id,
                    milestone="needs_user_input",
                    gate=gate,
                )
                return self._workflow_result(
                    run_id=run_id,
                    task_id=task_id,
                    status="blocked",
                    current_gate=gate,
                    steps=steps + [_workflow_step_dict(gate, "blocked", attempts)],
                    final_gate_result=final_payload,
                    cwd=cwd,
                    screenshots=screenshot_payloads,
                    visual_evidence_policy=visual_policy,
                    workflow_route=workflow_route,
                )

        no_mistakes_result = self._run_no_mistakes_post_acceptance(
            cwd=cwd,
            task_id=task_id,
            run_id=run_id,
            intent=intent,
            config=no_mistakes_config,
        )
        if no_mistakes_result is not None and _no_mistakes_blocks_workflow(no_mistakes_result):
            final_payload = {
                "task_id": task_id,
                "gate": "no_mistakes_validation",
                "status": "blocked",
                "attempts": 1,
                "handoff_packet_path": None,
                "probes": {
                    "NO_MISTAKES": {
                        "probe_id": "NO_MISTAKES",
                        "status": "red",
                        "reason": no_mistakes_result.reason,
                        "details": no_mistakes_result.to_event_payload(),
                    },
                },
                "outcome": None,
                "escalation": {
                    "type": "no_mistakes_validation",
                    "reason": no_mistakes_result.reason,
                    "details": no_mistakes_result.to_event_payload(),
                },
                "no_mistakes_validation": no_mistakes_result.to_event_payload(),
                "no_mistakes_validation_receipt": no_mistakes_result.to_receipt(),
            }
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_gate_result",
                payload=final_payload,
            )
            self.state.record_dual_agent_workflow_step(
                run_id=run_id,
                task_id=task_id,
                gate="no_mistakes_validation",
                status="blocked",
                attempt_count=1,
                latest_event_id=self.state.latest_event_id(run_id),
            )
            self.state.update_dual_agent_workflow(
                run_id=run_id,
                task_id=task_id,
                status="blocked",
                current_gate="no_mistakes_validation",
            )
            await self._emit_workflow_milestone(
                notifier=notifier,
                run_id=run_id,
                task_id=task_id,
                milestone="needs_user_input",
                gate="no_mistakes_validation",
            )
            return self._workflow_result(
                run_id=run_id,
                task_id=task_id,
                status="blocked",
                current_gate="no_mistakes_validation",
                steps=steps + [_workflow_step_dict("no_mistakes_validation", "blocked", 1)],
                final_gate_result=final_payload,
                cwd=cwd,
                screenshots=screenshot_payloads,
                visual_evidence_policy=visual_policy,
                workflow_route=workflow_route,
                no_mistakes_validation=no_mistakes_result,
            )

        artifact_status = mandatory_artifact_status(
            cwd=cwd,
            task_id=task_id,
            task_complexity=str(workflow_route["task_complexity"]),
        )
        status = "accepted" if artifact_status["status"] == "ok" else "failed"
        self.state.update_dual_agent_workflow(
            run_id=run_id,
            task_id=task_id,
            status=status,
            current_gate="outcome_review",
        )
        await self._emit_workflow_milestone(
            notifier=notifier,
            run_id=run_id,
            task_id=task_id,
            milestone="accepted" if status == "accepted" else "failed",
        )
        if status != "accepted":
            await self._emit_workflow_milestone(
                notifier=notifier,
                run_id=run_id,
                task_id=task_id,
                milestone="needs_user_input",
            )
        return self._workflow_result(
            run_id=run_id,
            task_id=task_id,
            status=status,
            current_gate="outcome_review",
            steps=steps,
            final_gate_result=final_payload,
            cwd=cwd,
            screenshots=screenshot_payloads,
            mandatory_artifacts=artifact_status,
            visual_evidence_policy=visual_policy,
            workflow_route=workflow_route,
            no_mistakes_validation=no_mistakes_result,
        )

    def _run_no_mistakes_post_acceptance(
        self,
        *,
        cwd: str,
        task_id: str,
        run_id: str,
        intent: str,
        config: NoMistakesConfig,
    ) -> NoMistakesValidationResult | None:
        if config.policy == "off":
            return None
        request = NoMistakesValidationRequest(
            cwd=cwd,
            task_id=task_id,
            run_id=run_id,
            intent=intent,
            config=config,
        )
        command = list(build_no_mistakes_command(config, intent=intent))
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="no_mistakes_validation_started",
            payload={
                "kind": "no_mistakes_validation_started",
                "schema_version": "no-mistakes-validation/v1",
                "task_id": task_id,
                "run_id": run_id,
                "policy": config.policy,
                "command": command,
                "skip_steps": _no_mistakes_command_skip_steps(command),
            },
        )
        result = run_no_mistakes_validation(request, runner=self.no_mistakes_runner)
        for finding in result.findings:
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="no_mistakes_finding",
                payload={
                    "kind": "no_mistakes_finding",
                    "schema_version": "no-mistakes-finding/v1",
                    "task_id": task_id,
                    "run_id": run_id,
                    "policy": config.policy,
                    "finding": finding.to_dict(),
                },
            )
        event_kind = (
            "no_mistakes_validation_skipped"
            if result.verdict in {"skipped", "unavailable"}
            else "no_mistakes_validation_failed"
            if result.status == "failed"
            else "no_mistakes_validation_completed"
        )
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind=event_kind,
            payload={
                "kind": event_kind,
                **result.to_event_payload(),
                "receipt": result.to_receipt(),
            },
        )
        return result

    def create_autoresearch_policy_proposals(
        self,
        *,
        report_path: str,
        repo_root: str,
        affected_gates: list[str],
        candidate_changes: dict[str, str] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Create operator-reviewable policy proposals from an accepted AutoResearch report."""
        repo_root_path = Path(repo_root).expanduser().resolve()
        report = _read_json_mapping(_resolve_repo_path(repo_root_path, report_path))
        if candidate_changes is not None:
            proposals = create_policy_evolution_proposals(
                report,
                repo_root=repo_root_path,
                candidate_changes=candidate_changes,
                affected_gates=affected_gates,
                state=self.state,
                run_id=run_id,
            )
            mode = "explicit_candidate_changes"
        else:
            proposals = derive_policy_evolution_proposals_from_report(
                report,
                repo_root=repo_root_path,
                affected_gates=affected_gates,
                state=self.state,
                run_id=run_id,
            )
            mode = "report_derived"
        return redact({
            "status": "ok",
            "mode": mode,
            "proposal_count": len(proposals),
            "proposals": proposals,
        })

    def list_autoresearch_experiments(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        rows = self.state.list_autoresearch_experiment_queue(status=status, limit=limit)
        return redact({
            "status": "ok",
            "count": len(rows),
            "experiments": rows,
            "default_change_allowed": False,
            "gate_authority": "unchanged",
        })

    def generate_autoresearch_experiments(
        self,
        *,
        repo_root: str,
    ) -> dict[str, Any]:
        drafts = generate_autoresearch_experiment_drafts(
            state=self.state,
            repo_root=Path(repo_root).expanduser().resolve(),
            config=AutoResearchGeneratorConfig.from_config(self.cfg),
        )
        return redact({
            "status": "ok",
            "drafted_count": len(drafts),
            "experiments": drafts,
            "default_change_allowed": False,
            "gate_authority": "unchanged",
        })

    def activate_autoresearch_experiment(
        self,
        *,
        experiment_id: str,
        operator: str,
        approval_channel: str,
    ) -> dict[str, Any]:
        row = activate_autoresearch_experiment(
            state=self.state,
            experiment_id=experiment_id,
            operator=operator,
            approval_channel=approval_channel,
        )
        return redact({
            "status": "ok",
            "experiment": row,
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "gate_advanced": False,
        })

    def park_autoresearch_experiment(
        self,
        *,
        experiment_id: str,
        operator: str,
        approval_channel: str,
        reason: str,
    ) -> dict[str, Any]:
        row = park_autoresearch_experiment(
            state=self.state,
            experiment_id=experiment_id,
            operator=operator,
            approval_channel=approval_channel,
            reason=reason,
        )
        return redact({
            "status": "ok",
            "experiment": row,
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "gate_advanced": False,
        })

    def approve_autoresearch_policy_proposal(
        self,
        *,
        proposal: dict[str, Any] | None = None,
        proposal_path: str | None = None,
        repo_root: str,
        run_id: str,
        approver: str,
        approval_channel: str,
        rollback_root: str = ".handoff/policy-rollbacks",
    ) -> dict[str, Any]:
        """Apply exactly one recorded policy proposal after explicit operator approval."""
        repo_root_path = Path(repo_root).expanduser().resolve()
        proposal_payload = _proposal_payload(
            proposal=proposal,
            proposal_path=proposal_path,
            repo_root=repo_root_path,
        )
        approval = approve_policy_proposal(
            proposal_payload,
            state=self.state,
            run_id=run_id,
            repo_root=repo_root_path,
            approver=approver,
            approval_channel=approval_channel,
            rollback_root=rollback_root,
        )
        return redact({"status": "ok", "approval": approval})

    def deny_autoresearch_policy_proposal(
        self,
        *,
        proposal: dict[str, Any] | None = None,
        proposal_path: str | None = None,
        repo_root: str,
        run_id: str,
        approver: str,
        approval_channel: str,
        reason: str,
    ) -> dict[str, Any]:
        """Record an explicit operator denial without applying the proposal."""
        repo_root_path = Path(repo_root).expanduser().resolve()
        proposal_payload = _proposal_payload(
            proposal=proposal,
            proposal_path=proposal_path,
            repo_root=repo_root_path,
        )
        denial = deny_policy_proposal(
            proposal_payload,
            state=self.state,
            run_id=run_id,
            approver=approver,
            approval_channel=approval_channel,
            reason=reason,
        )
        return redact({"status": "ok", "denial": denial})

    def rollback_autoresearch_policy_proposal(
        self,
        *,
        rollback_pointer: dict[str, Any] | None = None,
        rollback_pointer_path: str | None = None,
        repo_root: str,
        run_id: str,
        approver: str,
        approval_channel: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Restore proposal changes through a recorded rollback pointer."""
        repo_root_path = Path(repo_root).expanduser().resolve()
        pointer_payload = _rollback_pointer_payload(
            rollback_pointer=rollback_pointer,
            rollback_pointer_path=rollback_pointer_path,
            repo_root=repo_root_path,
        )
        rollback = rollback_policy_proposal(
            pointer_payload,
            state=self.state,
            run_id=run_id,
            repo_root=repo_root_path,
            approver=approver,
            approval_channel=approval_channel,
            reason=reason,
        )
        return redact({"status": "ok", "rollback": rollback})

    def submit_dual_agent_workflow_job(
        self,
        *,
        cwd: str,
        task_id: str,
        run_id: str,
        intent: str,
        user_facing: bool = False,
        visual_evidence_policy: str = "auto",
        max_rounds_per_gate: int = 5,
        quality: str = "best",
        budget_usd: float = 100.0,
        timeout_s: int = 900,
        execution_layer_mode: str = "lead_direct",
        dynamic_workflow_task_class: str | None = None,
        agentic_lead_policy: str | None = None,
        min_subagents: int | None = None,
        required_roles: list[str] | None = None,
        solo_exception_for_artifact_only_gates: bool | None = None,
        required_evidence_grade: str | None = None,
        reviewer_unavailable_policy: str | None = None,
        planning_artifacts: list[dict[str, Any]] | None = None,
        screenshots: list[dict[str, Any]] | None = None,
        verified_claims: list[str] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        require_skill_receipts: bool = True,
        cursor_review: bool = True,
        cursor_review_profile: str = "default",
        cursor_review_gates: list[str] | None = None,
        cursor_model: str | None = None,
        reviewer_model: str | None = None,
        reviewer_output_mode: str | None = None,
        reviewer_max_tokens: int | None = None,
        reviewer_infra_retry_limit: int | None = None,
        reviewer_infra_retry_backoff_s: float | None = None,
        reviewer_low_confidence_threshold: float | None = None,
        reviewer_panel_calibration_path: str | None = None,
        task_complexity: str | None = None,
        no_mistakes_policy: str | None = None,
        no_mistakes_skip_steps: list[str] | None = None,
        no_mistakes_timeout_s: int | None = None,
        config_path: str | None = None,
        client_token: str | None = None,
    ) -> dict[str, Any]:
        execution_layer_mode = _canonical_execution_layer_mode(execution_layer_mode)
        dynamic_workflow_task_class = _canonical_dynamic_workflow_task_class(dynamic_workflow_task_class)
        agentic_policy = _agentic_lead_policy_config(
            self.cfg,
            agentic_lead_policy=agentic_lead_policy,
            min_subagents=min_subagents,
            required_roles=required_roles,
            solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
            required_evidence_grade=required_evidence_grade,
        )
        reviewer_policy = _reviewer_unavailable_policy_config(
            self.cfg,
            reviewer_unavailable_policy=reviewer_unavailable_policy,
        )
        reviewer_output_mode_value = _reviewer_output_mode_config(
            self.cfg,
            reviewer_output_mode=reviewer_output_mode,
        )
        reviewer_model_value = _reviewer_model_config(
            self.cfg,
            reviewer_model=reviewer_model,
            cursor_model=cursor_model,
            reviewer_output_mode=reviewer_output_mode_value,
        )
        reviewer_max_tokens_value = _reviewer_max_tokens_config(
            self.cfg,
            reviewer_max_tokens=reviewer_max_tokens,
        )
        reviewer_infra_retry_limit_value = _reviewer_infra_retry_limit_config(
            self.cfg,
            reviewer_infra_retry_limit=reviewer_infra_retry_limit,
        )
        reviewer_infra_retry_backoff_s_value = _reviewer_infra_retry_backoff_s_config(
            self.cfg,
            reviewer_infra_retry_backoff_s=reviewer_infra_retry_backoff_s,
        )
        reviewer_low_confidence_threshold_value = _reviewer_low_confidence_threshold_config(
            self.cfg,
            reviewer_low_confidence_threshold=reviewer_low_confidence_threshold,
        )
        reviewer_panel_calibration_path_value = _reviewer_panel_calibration_path_config(
            self.cfg,
            cwd=cwd,
            reviewer_panel_calibration_path=reviewer_panel_calibration_path,
        )
        no_mistakes_config = _no_mistakes_config(
            self.cfg,
            policy=no_mistakes_policy,
            skip_steps=no_mistakes_skip_steps,
            timeout_s=no_mistakes_timeout_s,
        )
        cwd_path = Path(cwd).expanduser().resolve()
        receipt_payloads = _normalise_receipt_payloads(tool_receipts or [])
        submit_visual_policy = workflow_visual_evidence_policy(
            intent=intent,
            task_id=task_id,
            user_facing=user_facing,
            planning_artifacts=planning_artifacts or [],
            operator_policy=visual_evidence_policy,
        )
        if (
            str(submit_visual_policy.get("operator_policy") or "") == "not_required"
            and submit_visual_policy.get("matched_terms")
        ):
            self.state.write_event(
                run_id=run_id,
                source="supervisor",
                kind="visual_evidence_override_asserted",
                payload={
                    "schema_version": "supervisor-visual-evidence-override/v1",
                    "task_id": task_id,
                    "operator_policy": "not_required",
                    "matched_terms": list(submit_visual_policy.get("matched_terms") or []),
                    "artifact_matches": list(submit_visual_policy.get("artifact_matches") or []),
                    "observational_only": True,
                    "gate_authority": "unchanged",
                },
            )
        payload = {
            "cwd": str(cwd_path),
            "task_id": task_id,
            "run_id": run_id,
            "intent": intent,
            "user_facing": user_facing,
            "visual_evidence_policy": visual_evidence_policy,
            "max_rounds_per_gate": max_rounds_per_gate,
            "quality": quality,
            "budget_usd": budget_usd,
            "timeout_s": timeout_s,
            "execution_layer_mode": execution_layer_mode,
            "dynamic_workflow_task_class": dynamic_workflow_task_class,
            "agentic_lead_policy": agentic_policy["agentic_lead_policy"],
            "min_subagents": agentic_policy["min_subagents"],
            "required_roles": agentic_policy["required_roles"],
            "solo_exception_for_artifact_only_gates": agentic_policy[
                "solo_exception_for_artifact_only_gates"
            ],
            "required_evidence_grade": agentic_policy["required_evidence_grade"],
            "reviewer_unavailable_policy": reviewer_policy,
            "planning_artifacts": planning_artifacts,
            "screenshots": screenshots,
            "verified_claims": verified_claims,
            "tool_receipts": receipt_payloads,
            "require_skill_receipts": require_skill_receipts,
            "cursor_review": cursor_review,
            "cursor_review_profile": cursor_review_profile,
            "cursor_review_gates": cursor_review_gates,
            "cursor_model": cursor_model,
            "reviewer_model": reviewer_model_value,
            "reviewer_output_mode": reviewer_output_mode_value,
            "reviewer_max_tokens": reviewer_max_tokens_value,
            "reviewer_infra_retry_limit": reviewer_infra_retry_limit_value,
            "reviewer_infra_retry_backoff_s": reviewer_infra_retry_backoff_s_value,
            "reviewer_low_confidence_threshold": reviewer_low_confidence_threshold_value,
            "reviewer_panel_calibration_path": reviewer_panel_calibration_path_value,
            "task_complexity": task_complexity,
            "no_mistakes_policy": no_mistakes_config.policy,
            "no_mistakes_skip_steps": list(no_mistakes_config.skip_steps),
            "no_mistakes_timeout_s": no_mistakes_config.timeout_s,
        }
        idempotency_token = _workflow_job_idempotency_token(
            run_id=run_id,
            payload=payload,
            client_token=client_token,
        )
        job_id = f"workflow-{uuid.uuid4().hex[:12]}"
        job_dir = cwd_path / ".handoff" / "workflow-jobs" / job_id
        request_path = job_dir / "request.json"
        result_path = job_dir / "result.json"
        log_path = job_dir / "worker.log"
        request_payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        reserved_job, created = self.state.reserve_dual_agent_workflow_job(
            job_id=job_id,
            run_id=run_id,
            task_id=task_id,
            cwd=str(cwd_path),
            status="submitted",
            request_path=str(request_path),
            result_path=str(result_path),
            log_path=str(log_path),
            idempotency_token=idempotency_token,
            request_payload_json=request_payload_json,
            config_path=str(Path(config_path or "~/.codex-supervisor/config.yaml").expanduser()),
        )
        if created:
            self._write_receipt_provenance_downgrade_events(
                run_id=run_id,
                task_id=task_id,
                receipts=receipt_payloads,
                scope="submit:caller_tool_receipts",
            )
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_workflow_job",
                payload={
                    "job_id": reserved_job["job_id"],
                    "task_id": task_id,
                    "status": "submitted",
                    "recovery_point": "reserved",
                    "request_path": str(request_path),
                    "result_path": str(result_path),
                    "log_path": str(log_path),
                    "transport_recovery": "detached_cli_worker",
                },
            )
            return self._workflow_job_response(reserved_job)
        if not created:
            record_transport_incident(
                self.state,
                run_id=run_id,
                task_id=task_id,
                job_id=reserved_job["job_id"],
                client_token=client_token,
                incident_type="same_client_token_reattach",
                interface="mcp",
                details={"status": reserved_job["status"], "recovery_point": reserved_job["recovery_point"]},
            )
            return self._workflow_job_response(reserved_job, reattached=True)

        raise RuntimeError("unreachable workflow job reservation state")

    def _workflow_job_response(
        self,
        row: Any,
        *,
        reattached: bool | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": row["job_id"],
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "status": row["terminal_status"] or row["status"],
            "pid": row["pid"],
            "request_path": row["request_path"],
            "result_path": row["result_path"],
            "log_path": row["log_path"],
            "error": row["error"],
            "recovery_point": row["recovery_point"],
            "poll_tool": "poll_dual_agent_workflow_job",
        }
        if reattached is not None:
            payload["reattached"] = reattached
        terminal_outcome_json = row["terminal_outcome_json"] if "terminal_outcome_json" in row.keys() else None
        if result is None and terminal_outcome_json:
            try:
                loaded = json.loads(str(terminal_outcome_json))
                result = loaded if isinstance(loaded, dict) else {"raw_result": loaded}
            except json.JSONDecodeError:
                result = {
                    "status": "failed",
                    "run_id": row["run_id"],
                    "task_id": row["task_id"],
                    "error": "invalid_terminal_outcome_json",
                }
        if result is not None:
            payload["result"] = result
        return redact(payload)

    def poll_dual_agent_workflow_job(self, *, job_id: str, interface: str = "mcp") -> dict[str, Any]:
        row = self.state.get_dual_agent_workflow_job(job_id=job_id)
        if row is None:
            record_transport_incident(
                self.state,
                run_id="supervisor_transport_incidents",
                job_id=job_id,
                incident_type="poll_failure",
                interface=interface,
                details={"reason": "missing_job"},
            )
            return {"job_id": job_id, "status": "missing"}
        result: dict[str, Any] | None = None
        status = str(row["terminal_status"] or row["status"])
        error = row["error"]
        terminal_outcome_json = (
            row["terminal_outcome_json"] if "terminal_outcome_json" in row.keys() else None
        )
        if terminal_outcome_json:
            try:
                loaded_terminal = json.loads(str(terminal_outcome_json))
                result = loaded_terminal if isinstance(loaded_terminal, dict) else {
                    "raw_result": loaded_terminal
                }
            except json.JSONDecodeError:
                result = {
                    "status": "failed",
                    "run_id": row["run_id"],
                    "task_id": row["task_id"],
                    "error": "invalid_terminal_outcome_json",
                }
            status = str(row["terminal_status"] or row["status"] or result.get("status") or "completed")
        payload = {
            "job_id": job_id,
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "cwd": row["cwd"],
            "status": status,
            "pid": row["pid"],
            "recovery_point": row["recovery_point"],
            "leased_by": row["leased_by"] if "leased_by" in row.keys() else None,
            "lease_expires_at": row["lease_expires_at"] if "lease_expires_at" in row.keys() else None,
            "heartbeat_at": row["heartbeat_at"] if "heartbeat_at" in row.keys() else None,
            "dispatch_attempts": row["dispatch_attempts"] if "dispatch_attempts" in row.keys() else 0,
            "next_dispatch_at": row["next_dispatch_at"] if "next_dispatch_at" in row.keys() else None,
            "parked_reason": row["parked_reason"] if "parked_reason" in row.keys() else None,
            "request_path": row["request_path"],
            "result_path": row["result_path"],
            "log_path": row["log_path"],
            "error": error,
            "result": result,
            "resume": workflow_resume_prompt(self.state, run_id=row["run_id"], task_id=row["task_id"]),
        }
        if status not in {"accepted", "blocked", "failed", "completed", "terminal"}:
            record_transport_incident(
                self.state,
                run_id=row["run_id"],
                task_id=row["task_id"],
                job_id=job_id,
                incident_type="non_terminal_poll",
                interface=interface,
                details={"status": status, "recovery_point": row["recovery_point"]},
            )
        return redact(payload)

    def catch_up_dual_agent_workflow(
        self,
        *,
        run_id: str,
        last_event_id: int | None = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        cursor = max(0, int(last_event_id or 0))
        page_limit = int(limit)
        events = self.state.read_events_since(
            run_id,
            after_event_id=cursor,
            limit=page_limit,
        )
        next_event_id = cursor
        if events:
            next_event_id = max(int(event["event_id"]) for event in events)
        return redact({
            "status": "ok",
            "run_id": run_id,
            "last_event_id": cursor,
            "events": events,
            "count": len(events),
            "next_event_id": next_event_id,
            "has_more": page_limit > 0 and len(events) == page_limit,
        })

    def read_dual_agent_workflow_resume_prompt(
        self,
        *,
        run_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        return workflow_resume_prompt(self.state, run_id=run_id, task_id=task_id)

    def record_gate_round(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: GateName,
        round_index: int,
        codex_decision: str,
        claude_decision: str,
        codex_confidence: float,
        claude_confidence: float,
        objection: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        round_payload = _gate_round_payload(
            round_index=round_index,
            codex_decision=codex_decision,
            claude_decision=claude_decision,
            codex_confidence=codex_confidence,
            claude_confidence=claude_confidence,
            objection=objection,
        )
        round_tool_call = ensure_tool_call_timing({
            "name": "record_gate_round",
            "status": "recorded",
            "args": {
                "task_id": task_id,
                "gate": gate,
                "round_index": round_index,
                "codex_decision": codex_decision,
                "claude_decision": claude_decision,
            },
            "result_summary": {
                "round_index": round_index,
                "codex_decision": codex_decision,
                "claude_decision": claude_decision,
                "has_objection": bool(objection),
            },
        })
        event_id = self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_round",
            payload={
                "task_id": task_id,
                "gate": gate,
                "round": round_payload,
                "tool_calls": [round_tool_call],
            },
        )
        result = {
            "status": "recorded",
            "event_id": event_id,
            "run_id": run_id,
            "task_id": task_id,
            "gate": gate,
            "round": round_payload,
        }
        if cwd is not None:
            result["artifact_export"] = self.export_gate_artifacts(
                run_id=run_id,
                task_id=task_id,
                cwd=cwd,
            )
        return result

    def check_budget(
        self,
        *,
        rounds: list[dict[str, Any]],
        per_gate_cap: int,
        task_budget: int,
    ) -> dict[str, Any]:
        probe = evaluate_deadlock_budget(
            [_gate_round_from_payload(r) for r in rounds],
            per_gate_cap=per_gate_cap,
            task_budget=task_budget,
        )
        return {"probe": asdict(probe)}

    async def escalate_deadlock(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: GateName,
        rounds: list[dict[str, Any]],
        per_gate_cap: int,
        task_budget: int,
    ) -> dict[str, Any]:
        notifier = self._notifier()
        if notifier is None:
            return {
                "status": "telegram_disabled",
                "reason": "deadlock_escalation_requires_telegram",
            }
        escalation = await request_deadlock_escalation(
            state=self.state,
            notifier=notifier,
            run_id=run_id,
            task_id=task_id,
            gate=gate,
            rounds=[_gate_round_from_payload(r) for r in rounds],
            per_gate_cap=per_gate_cap,
            task_budget=task_budget,
        )
        return redact(asdict(escalation))

    def read_outcome(self, *, run_id: str, task_id: str) -> dict[str, Any]:
        rows = self.state._conn.execute(
            """SELECT * FROM events
               WHERE run_id=? AND source='dual_agent'
                 AND kind='dual_agent_gate_result'
               ORDER BY event_id DESC
               LIMIT 50""",
            (run_id,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if str(payload.get("task_id") or "") == task_id:
                return {
                    "status": "ok",
                    "run_id": run_id,
                    "task_id": task_id,
                    "event_id": row["event_id"],
                    "result": redact(payload),
                }
        return {"status": "not_found", "run_id": run_id, "task_id": task_id}

    def read_gate_transcript(self, *, run_id: str, task_id: str) -> dict[str, Any]:
        rows = self.state._conn.execute(
            """SELECT * FROM events
               WHERE run_id=? AND source='dual_agent'
                 AND kind IN (
                   'dual_agent_gate_round',
                   'dual_agent_gate_result',
                   'dual_agent_planning_validation',
                   'dual_agent_skill_receipt_validation',
                   'dual_agent_agentic_worker_production',
                   'dual_agent_agentic_worker_progress',
                   'dual_agent_dynamic_workflow_receipt_validation',
                   'dual_agent_dynamic_workflow_manifest',
                   'dual_agent_dynamic_workflow_synthesis',
                   'dual_agent_runtime_evidence',
                   'dual_agent_reviewer_unavailable_recovery',
                   'dual_agent_workflow_job',
                   'dual_agent_workflow_terminal_outcome',
                   'dual_agent_workflow_terminal_discrepancy',
                   'dual_agent_workflow_route',
                   'dual_agent_interaction_message',
                   'independent_reviewer_adjudication',
                   'independent_reviewer_review',
                   'supervisor_cross_vendor_review_selected',
                   'supervisor_degraded_review_unavailable',
                   'supervisor_evidence_attempt_recorded',
                   'supervisor_review_context_validation',
                   'supervisor_review_packet_created',
                   'supervisor_worker_blocked',
                   'supervisor_worker_cancelled',
                   'supervisor_worker_completed',
                   'supervisor_worker_dispatched',
                   'supervisor_worker_failed',
                   'supervisor_worker_roster_checked',
                   'supervisor_worker_session_created',
                   'tri_agent_cursor_review'
                 )
               ORDER BY event_id ASC""",
            (run_id,),
        ).fetchall()
        rounds: list[dict[str, Any]] = []
        planning_validations: list[dict[str, Any]] = []
        skill_receipt_validations: list[dict[str, Any]] = []
        agentic_worker_productions: list[dict[str, Any]] = []
        agentic_worker_progress: list[dict[str, Any]] = []
        dynamic_workflow_receipt_validations: list[dict[str, Any]] = []
        dynamic_workflow_manifests: list[dict[str, Any]] = []
        dynamic_workflow_syntheses: list[dict[str, Any]] = []
        runtime_evidence: list[dict[str, Any]] = []
        reviewer_unavailable_recoveries: list[dict[str, Any]] = []
        workflow_jobs: list[dict[str, Any]] = []
        workflow_routes: list[dict[str, Any]] = []
        interactions: list[dict[str, Any]] = []
        cursor_reviews: list[dict[str, Any]] = []
        independent_reviewer_reviews: list[dict[str, Any]] = []
        independent_reviewer_adjudications: list[dict[str, Any]] = []
        supervisor_review_packets: list[dict[str, Any]] = []
        supervisor_review_context_validations: list[dict[str, Any]] = []
        worker_roster_checks: list[dict[str, Any]] = []
        worker_dispatch_events: list[dict[str, Any]] = []
        evidence_attempts: list[dict[str, Any]] = []
        cross_vendor_reviews: list[dict[str, Any]] = []
        latest_result: dict[str, Any] | None = None
        latest_result_event_id: int | None = None
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if str(payload.get("task_id") or "") != task_id:
                continue
            if row["kind"] == "dual_agent_gate_round":
                round_payload = dict(payload.get("round") or {})
                rounds.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    "gate": payload.get("gate"),
                    **round_payload,
                }))
            elif row["kind"] == "dual_agent_planning_validation":
                planning_validations.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_skill_receipt_validation":
                skill_receipt_validations.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_agentic_worker_production":
                agentic_worker_productions.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_agentic_worker_progress":
                agentic_worker_progress.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_dynamic_workflow_receipt_validation":
                dynamic_workflow_receipt_validations.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_dynamic_workflow_manifest":
                dynamic_workflow_manifests.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_dynamic_workflow_synthesis":
                dynamic_workflow_syntheses.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_runtime_evidence":
                runtime_evidence.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_reviewer_unavailable_recovery":
                reviewer_unavailable_recoveries.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] in {
                "dual_agent_workflow_job",
                "dual_agent_workflow_terminal_outcome",
                "dual_agent_workflow_terminal_discrepancy",
            }:
                workflow_jobs.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_workflow_route":
                workflow_routes.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_interaction_message":
                interactions.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "tri_agent_cursor_review":
                cursor_reviews.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    "gate": payload.get("gate"),
                    "cursor_review": payload.get("cursor_review"),
                    "independent_reviewer": payload.get("independent_reviewer")
                    or payload.get("cursor_review"),
                    "independent_reviewer_results": payload.get("independent_reviewer_results")
                    or _independent_reviewer_results_from_payload(payload),
                }))
            elif row["kind"] == "independent_reviewer_review":
                independent_reviewer_reviews.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    "gate": payload.get("gate"),
                    "independent_reviewer_results": payload.get("independent_reviewer_results")
                    or _independent_reviewer_results_from_payload(payload),
                    "cursor_review": payload.get("cursor_review"),
                    "independent_reviewer": payload.get("independent_reviewer")
                    or payload.get("cursor_review"),
                }))
            elif row["kind"] == "independent_reviewer_adjudication":
                independent_reviewer_adjudications.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    "gate": payload.get("gate"),
                    "adjudication": payload.get("adjudication"),
                    "independent_reviewer_panel_decision": payload.get(
                        "independent_reviewer_panel_decision"
                    ),
                    "independent_reviewer_results": payload.get("independent_reviewer_results")
                    or _independent_reviewer_results_from_payload(payload),
                }))
            elif row["kind"] == "supervisor_review_packet_created":
                supervisor_review_packets.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "supervisor_review_context_validation":
                supervisor_review_context_validations.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] == "supervisor_worker_roster_checked":
                worker_roster_checks.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] in {
                "supervisor_worker_session_created",
                "supervisor_worker_dispatched",
                "supervisor_worker_completed",
                "supervisor_worker_failed",
                "supervisor_worker_blocked",
                "supervisor_worker_cancelled",
            }:
                worker_dispatch_events.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    "event_kind": row["kind"],
                    **payload,
                }))
            elif row["kind"] == "supervisor_evidence_attempt_recorded":
                evidence_attempts.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    **payload,
                }))
            elif row["kind"] in {
                "supervisor_cross_vendor_review_selected",
                "supervisor_degraded_review_unavailable",
            }:
                cross_vendor_reviews.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    "event_kind": row["kind"],
                    **payload,
                }))
            elif row["kind"] == "dual_agent_gate_result":
                latest_result = redact(payload)
                latest_result_event_id = int(row["event_id"])

        if (
            not rounds and not planning_validations and not skill_receipt_validations
            and not agentic_worker_productions
            and not agentic_worker_progress
            and not dynamic_workflow_receipt_validations and not dynamic_workflow_manifests
            and not dynamic_workflow_syntheses and not runtime_evidence
            and not reviewer_unavailable_recoveries
            and not workflow_jobs and not workflow_routes
            and not cursor_reviews
            and not independent_reviewer_reviews
            and not independent_reviewer_adjudications
            and not supervisor_review_packets
            and not supervisor_review_context_validations
            and not worker_roster_checks
            and not worker_dispatch_events
            and not evidence_attempts
            and not cross_vendor_reviews
            and latest_result is None
        ):
            return {
                "status": "not_found",
                "run_id": run_id,
                "task_id": task_id,
                "rounds": [],
                "planning_validations": [],
                "skill_receipt_validations": [],
                "agentic_worker_productions": [],
                "agentic_worker_progress": [],
                "dynamic_workflow_receipt_validations": [],
                "dynamic_workflow_manifests": [],
                "dynamic_workflow_syntheses": [],
                "runtime_evidence": [],
                "reviewer_unavailable_recoveries": [],
                "workflow_jobs": [],
                "workflow_routes": [],
                "interactions": [],
                "cursor_reviews": [],
                "independent_reviewer_reviews": [],
                "independent_reviewer_adjudications": [],
                "supervisor_review_packets": [],
                "supervisor_review_context_validations": [],
                "worker_roster_checks": [],
                "worker_dispatch_events": [],
                "evidence_attempts": [],
                "cross_vendor_reviews": [],
                "result": None,
                "handoff_packet_path": None,
            }
        return {
            "status": "ok",
            "run_id": run_id,
            "task_id": task_id,
            "rounds": rounds,
            "planning_validations": planning_validations,
            "skill_receipt_validations": skill_receipt_validations,
            "agentic_worker_productions": agentic_worker_productions,
            "agentic_worker_progress": agentic_worker_progress,
            "dynamic_workflow_receipt_validations": dynamic_workflow_receipt_validations,
            "dynamic_workflow_manifests": dynamic_workflow_manifests,
            "dynamic_workflow_syntheses": dynamic_workflow_syntheses,
            "runtime_evidence": runtime_evidence,
            "reviewer_unavailable_recoveries": reviewer_unavailable_recoveries,
            "workflow_jobs": workflow_jobs,
            "workflow_routes": workflow_routes,
            "interactions": interactions,
            "cursor_reviews": cursor_reviews,
            "independent_reviewer_reviews": independent_reviewer_reviews,
            "independent_reviewer_adjudications": independent_reviewer_adjudications,
            "supervisor_review_packets": supervisor_review_packets,
            "supervisor_review_context_validations": supervisor_review_context_validations,
            "worker_roster_checks": worker_roster_checks,
            "worker_dispatch_events": worker_dispatch_events,
            "evidence_attempts": evidence_attempts,
            "cross_vendor_reviews": cross_vendor_reviews,
            "result_event_id": latest_result_event_id,
            "result": latest_result,
            "handoff_packet_path": (
                latest_result.get("handoff_packet_path")
                if latest_result is not None else None
            ),
        }

    def export_gate_artifacts(
        self,
        *,
        run_id: str,
        task_id: str,
        cwd: str,
        output_dir: str | None = None,
        screenshots: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        target_dir = Path(output_dir).expanduser() if output_dir else default_dual_agent_artifact_dir(cwd, task_id)
        result = export_dual_agent_run_artifacts(
            self.state,
            run_id=run_id,
            task_id=task_id,
            output_dir=target_dir,
            screenshots=tuple(
                artifact
                for artifact in (
                    _maybe_screenshot(item)
                    for item in (screenshots or [])
                )
                if artifact is not None
            ),
        )
        cwd_path = Path(cwd).resolve()
        return {
            "status": result.status,
            "run_id": run_id,
            "task_id": task_id,
            "output_dir": _display_path(result.output_dir, cwd_path),
            "files": [_display_path(path, cwd_path) for path in result.files],
        }

    def start_codex_session(
        self,
        *,
        prompt: str,
        cwd: str,
        model: str | None = None,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        execute: bool = False,
        timeout_s: int = 600,
    ) -> dict[str, Any]:
        codex_cfg = self.cfg.target.codex if self.cfg.target and self.cfg.target.codex else None
        cli = codex_cfg.cli_command if codex_cfg is not None else "codex"
        argv = [cli, "exec", "--json", "-C", str(Path(cwd).expanduser())]
        argv.extend(["-m", model or DEFAULT_CODEX_MODEL])
        if reasoning_effort:
            argv.extend(["-c", f'reasoning_effort="{reasoning_effort}"'])
        argv.append(prompt)
        if not execute:
            return {"status": "dry_run", "argv": _redacted_prompt_argv(argv)}
        try:
            completed = self.codex_runner(
                argv,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout_s)),
                cwd=str(Path(cwd).expanduser()),
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "argv": _redacted_prompt_argv(argv),
                "timeout_s": timeout_s,
            }
        except FileNotFoundError:
            return {"status": "failed", "reason": "codex_binary_not_found", "argv": _redacted_prompt_argv(argv)}
        status = "completed" if completed.returncode == 0 else "failed"
        return redact({
            "status": status,
            "returncode": completed.returncode,
            "argv": _redacted_prompt_argv(argv),
            "stdout_tail": (completed.stdout or "")[-4000:],
            "stderr_tail": (completed.stderr or "")[-4000:],
        })

    async def _emit_workflow_milestone(
        self,
        *,
        notifier: Any | None,
        run_id: str,
        task_id: str,
        milestone: str,
        gate: str | None = None,
    ) -> None:
        text = workflow_milestone_text(task_id=task_id, milestone=milestone, gate=gate)
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_workflow_milestone",
            payload={
                "task_id": task_id,
                "milestone": milestone,
                "gate": gate,
                "text": text,
            },
        )
        if notifier is None:
            return
        send = getattr(notifier, "send_message", None)
        if send is not None:
            await send(text)

    def _write_receipt_provenance_downgrade_events(
        self,
        *,
        run_id: str,
        task_id: str,
        receipts: list[dict[str, Any]],
        scope: str,
    ) -> None:
        for receipt in receipts:
            if not receipt.get("provenance_downgraded"):
                continue
            self.state.write_event(
                run_id=run_id,
                source="dual_agent",
                kind="receipt_provenance_downgraded",
                payload=provenance_downgrade_event_payload(
                    receipt,
                    task_id=task_id,
                    run_id=run_id,
                    scope=scope,
                ),
            )

    def _write_interaction_message(
        self,
        *,
        run_id: str,
        message: AgentMailboxMessage,
    ) -> int:
        return self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_interaction_message",
            payload=message.to_event_payload(),
        )

    def _write_workflow_gate_override(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: str,
        attempts: int,
        reason: str,
        source_payload: dict[str, Any],
        claim_probe: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = {
            "task_id": task_id,
            "gate": gate,
            "status": "blocked",
            "attempts": attempts,
            "handoff_packet_path": source_payload.get("handoff_packet_path"),
            "probes": source_payload.get("probes") or {},
            "outcome": source_payload.get("outcome"),
            "cursor_review": source_payload.get("cursor_review"),
            "independent_reviewer": source_payload.get("independent_reviewer")
            or source_payload.get("cursor_review"),
            "independent_reviewer_results": source_payload.get("independent_reviewer_results") or [],
            "independent_reviewer_panel_decision": source_payload.get(
                "independent_reviewer_panel_decision"
            ),
            "cursor_decision": source_payload.get("cursor_decision"),
            "codex_decision": source_payload.get("codex_decision"),
            "claude_decision": source_payload.get("claude_decision"),
            "claim_verification": claim_probe,
            "runtime_evidence": source_payload.get("runtime_evidence"),
            "escalation": {
                "type": "workflow_lifecycle",
                "reason": reason,
                "claim_verification": claim_probe,
            },
            "artifact_rigor": source_payload.get("artifact_rigor"),
        }
        event_id = self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload=payload,
        )
        return {**payload, "event_id": event_id}

    def _dynamic_workflow_receipt_block(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: str,
        execution_layer_mode: str,
        dynamic_workflow_task_class: str | None,
        agentic_policy: dict[str, Any],
        tool_receipts: list[dict[str, Any]] | None,
        cwd: str,
        screenshots: list[dict[str, Any]] | None,
        artifact_rigor: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not _requires_p13_receipt_validation(
            execution_layer_mode,
            str(agentic_policy.get("agentic_lead_policy") or "off"),
        ):
            return None

        receipt_payloads = _normalise_receipt_payloads(tool_receipts or [])
        with timed_tool_call(
            "verify_dynamic_workflow_receipts",
            args={
                "task_id": task_id,
                "gate": gate,
                "execution_layer_mode": execution_layer_mode,
                "dynamic_workflow_task_class": dynamic_workflow_task_class,
                "agentic_lead_policy": agentic_policy.get("agentic_lead_policy"),
                "min_subagents": agentic_policy.get("min_subagents"),
                "required_roles": agentic_policy.get("required_roles"),
                "required_evidence_grade": agentic_policy.get("required_evidence_grade"),
                "receipt_count": len(receipt_payloads),
            },
            receipt_ids=_receipt_ids(receipt_payloads),
        ) as dynamic_receipt_tool_call:
            probe = verify_dynamic_workflow_receipts(
                execution_layer_mode=execution_layer_mode,
                dynamic_workflow_task_class=dynamic_workflow_task_class,
                tool_receipts=receipt_payloads,
                cwd=cwd,
                gate=gate,
                **agentic_policy,
            )
        dynamic_receipt_tool_call.update({
            "status": probe.status,
            "probe_id": probe.probe_id,
            "reason": probe.reason,
            "result_summary": {
                "probe_id": probe.probe_id,
                "status": probe.status,
                "reason": probe.reason,
                "missing_gates": probe.details.get("missing_gates", []),
                "verified_gates": probe.details.get("verified_gates", []),
                "agentic_policy_status": (
                    probe.details.get("agentic_policy", {}).get("status")
                    if isinstance(probe.details.get("agentic_policy"), dict)
                    else None
                ),
            },
        })
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_dynamic_workflow_receipt_validation",
            payload={
                "task_id": task_id,
                "gate": gate,
                "status": "accepted" if probe.ok else "blocked",
                "probe": asdict(probe),
                "tool_receipts": receipt_payloads,
                "tool_calls": [ensure_tool_call_timing(dynamic_receipt_tool_call)],
            },
        )
        if probe.ok:
            return None

        payload = {
            "task_id": task_id,
            "gate": gate,
            "status": "blocked",
            "claude_gate_status": "not_invoked",
            "supervisor_final_status": "blocked",
            "attempts": 0,
            "handoff_packet_path": None,
            "probes": {"P13": asdict(probe)},
            "outcome": None,
            "escalation": {
                "type": "dynamic_workflow_receipt_validation",
                "reason": probe.reason,
                "details": probe.details,
            },
            "artifact_rigor": artifact_rigor,
            "tool_calls": [ensure_tool_call_timing(dynamic_receipt_tool_call)],
        }
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload=payload,
        )
        payload["artifact_export"] = self.export_gate_artifacts(
            run_id=run_id,
            task_id=task_id,
            cwd=cwd,
            screenshots=screenshots,
        )
        return redact(payload)

    def _workflow_result(
        self,
        *,
        run_id: str,
        task_id: str,
        status: str,
        current_gate: str,
        steps: list[dict[str, Any]],
        final_gate_result: dict[str, Any] | None,
        cwd: str,
        screenshots: list[dict[str, Any]],
        visual_evidence_policy: dict[str, Any],
        workflow_route: dict[str, Any] | None = None,
        mandatory_artifacts: dict[str, Any] | None = None,
        no_mistakes_validation: NoMistakesValidationResult | None = None,
    ) -> dict[str, Any]:
        if final_gate_result is not None:
            final_gate_result = stamp_trace_envelope(
                run_id=run_id,
                source="dual_agent",
                kind="dual_agent_gate_result",
                payload=final_gate_result,
            )
        lesson_feedback = self._record_lesson_feedback_for_run(run_id=run_id)
        lesson_records = record_lessons_for_run(
            self.state,
            run_id=run_id,
            task_id=task_id,
            task_class=str((workflow_route or {}).get("lesson_task_class") or "general"),
        )
        quality_trends: dict[str, Any]
        try:
            trend_rows = record_quality_trends_for_run(
                self.state,
                run_id=run_id,
                task_id=task_id,
                task_class=str((workflow_route or {}).get("lesson_task_class") or "general"),
            )
            quality_trends = {
                "recorded_count": len(trend_rows),
                "gates": [str(row.get("gate")) for row in trend_rows],
                "observational_only": True,
                "gate_authority": "unchanged",
            }
            autoresearch_cfg = getattr(self.cfg, "autoresearch", None)
            quality_trends["policy_regression_rollbacks"] = (
                draft_policy_regression_rollbacks_for_trend_rows(
                    self.state,
                    run_id=run_id,
                    trend_rows=trend_rows,
                    min_runs=int(getattr(autoresearch_cfg, "policy_regression_min_runs", 3)),
                    first_pass_drop_threshold=float(getattr(
                        autoresearch_cfg,
                        "policy_regression_first_pass_drop_threshold",
                        0.05,
                    )),
                    false_accept_increase_threshold=float(getattr(
                        autoresearch_cfg,
                        "policy_regression_false_accept_increase_threshold",
                        0.01,
                    )),
                    time_to_accept_increase_ratio=float(getattr(
                        autoresearch_cfg,
                        "policy_regression_time_to_accept_increase_ratio",
                        0.25,
                    )),
                )
            )
        except Exception as exc:
            quality_trends = {
                "recorded_count": 0,
                "observational_only": True,
                "gate_authority": "unchanged",
                "status": "failed",
                "reason": type(exc).__name__,
                "message": str(exc),
            }
        autoresearch_drafts: dict[str, Any]
        try:
            drafts = generate_autoresearch_experiment_drafts(
                state=self.state,
                repo_root=cwd,
                config=AutoResearchGeneratorConfig.from_config(self.cfg),
            )
            autoresearch_drafts = {
                "drafted_count": len(drafts),
                "experiment_ids": [str(item.get("experiment_id")) for item in drafts],
                "statuses": [str(item.get("status")) for item in drafts],
                "default_change_allowed": False,
                "gate_authority": "unchanged",
            }
        except Exception as exc:
            autoresearch_drafts = {
                "drafted_count": 0,
                "status": "failed",
                "reason": type(exc).__name__,
                "message": str(exc),
                "default_change_allowed": False,
                "gate_authority": "unchanged",
            }
        artifact_export = self.export_gate_artifacts(
            run_id=run_id,
            task_id=task_id,
            cwd=cwd,
            screenshots=screenshots,
        )
        mandatory = mandatory_artifacts or mandatory_artifact_status(
            cwd=cwd,
            task_id=task_id,
            task_complexity=str((workflow_route or {}).get("task_complexity") or "large"),
        )
        return redact({
            "status": status,
            "run_id": run_id,
            "task_id": task_id,
            "current_gate": current_gate,
            "steps": steps,
            "final_gate_result": final_gate_result,
            "visual_evidence_policy": visual_evidence_policy,
            "workflow_route": workflow_route,
            "artifact_export": artifact_export,
            "mandatory_artifacts": mandatory,
            "lessons": {
                "recorded_count": len([item for item in lesson_records if item.get("created")]),
                "observed_count": len(lesson_records),
                "lesson_ids": [str(item.get("lesson_id")) for item in lesson_records],
                "feedback": lesson_feedback,
                "advisory_only": True,
            },
            "quality_trends": quality_trends,
            "autoresearch_drafts": autoresearch_drafts,
            "no_mistakes_validation": (
                {
                    **no_mistakes_validation.to_event_payload(),
                    "receipt": no_mistakes_validation.to_receipt(),
                }
                if no_mistakes_validation is not None
                else None
            ),
            "resume": workflow_resume_prompt(self.state, run_id=run_id, task_id=task_id),
        })

    def _record_lesson_feedback_for_run(self, *, run_id: str) -> dict[str, Any]:
        events = self.state.read_events_since(run_id, after_event_id=0, limit=10_000)
        lesson_ids: list[str] = []
        for event in events:
            if event["kind"] != "supervisor_lesson_injection":
                continue
            payload = event["payload"]
            if isinstance(payload, dict):
                lesson_ids.extend(str(item) for item in payload.get("lesson_ids") or [] if str(item))
        unique_lesson_ids = sorted(set(lesson_ids))
        taxonomy_codes = sorted(_failure_taxonomy_codes_from_events(events))
        if not unique_lesson_ids:
            return {
                "lesson_ids": [],
                "recurring_taxonomy_codes": taxonomy_codes,
                "updated": False,
                "advisory_only": True,
                "gate_authority": "unchanged",
            }
        if not _lesson_feedback_has_gate_verdict(events):
            return {
                "lesson_ids": unique_lesson_ids,
                "recurring_taxonomy_codes": taxonomy_codes,
                "updated": False,
                "reason": "unattributable_no_gate_verdict",
                "advisory_only": True,
                "gate_authority": "unchanged",
            }
        autoresearch_cfg = getattr(self.cfg, "autoresearch", None)
        retire_after = int(getattr(autoresearch_cfg, "lesson_retire_after_no_benefit_injections", 3))
        self.state.record_supervisor_lesson_injection_feedback(
            lesson_ids=unique_lesson_ids,
            recurring_taxonomy_codes=taxonomy_codes,
            retire_after=retire_after,
        )
        event_id = self.state.write_event(
            run_id=run_id,
            source="supervisor",
            kind="supervisor_lesson_feedback_recorded",
            payload={
                "schema_version": "supervisor-lesson-feedback/v1",
                "lesson_ids": unique_lesson_ids,
                "recurring_taxonomy_codes": taxonomy_codes,
                "retire_after": retire_after,
                "advisory_only": True,
                "gate_authority": "unchanged",
            },
        )
        return {
            "lesson_ids": unique_lesson_ids,
            "recurring_taxonomy_codes": taxonomy_codes,
            "updated": True,
            "event_id": event_id,
            "advisory_only": True,
            "gate_authority": "unchanged",
        }

    def _workflow_gate_start_kwargs(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: str,
        intent: str,
        corrective_context: str,
        lesson_task_class: str,
        cwd: str | Path = ".",
        planning_artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        lesson_snapshot: dict[str, Any] | None = None,
        round_index: int,
    ) -> dict[str, Any]:
        base_instruction = _workflow_gate_instruction(
            gate=gate,
            intent=intent,
            corrective_context=corrective_context,
            tdd_test_names=_workflow_tdd_test_names(cwd, planning_artifacts or ()),
        )
        policy_overlay = load_policy_overlay(cwd)
        overlay_payload = policy_overlay.to_event_payload(gate=gate, task_class=lesson_task_class)
        self.state.write_event(
            run_id=run_id,
            source="supervisor",
            kind="supervisor_policy_overlay_snapshot",
            payload={
                **overlay_payload,
                "task_id": task_id,
                "round_index": int(round_index),
                "task_class": lesson_task_class,
            },
        )
        snapshot_gates = (
            lesson_snapshot.get("gates")
            if isinstance(lesson_snapshot, dict) and isinstance(lesson_snapshot.get("gates"), dict)
            else {}
        )
        snapshot_injection = snapshot_gates.get(gate) if isinstance(snapshot_gates, dict) else None
        if isinstance(snapshot_injection, dict):
            injection = {
                "schema_version": snapshot_injection.get("schema_version")
                or "supervisor-lesson-injection/v1",
                "lesson_ids": list(snapshot_injection.get("lesson_ids") or []),
                "lesson_count": int(snapshot_injection.get("lesson_count") or 0),
                "block": str(snapshot_injection.get("block") or ""),
                "block_sha256": str(snapshot_injection.get("block_sha256") or ""),
            }
        else:
            lessons = self.state.query_supervisor_lessons(
                task_class=lesson_task_class,
                gate=gate,
                limit=policy_overlay.lesson_limit,
            )
            injection = build_lesson_injection(lessons)
        instruction = apply_policy_overlay_to_instruction(
            base_instruction,
            overlay=policy_overlay,
            gate=gate,
            task_class=lesson_task_class,
        )
        instruction = append_lesson_block(instruction, injection)
        if injection["lesson_count"]:
            self.state.write_event(
                run_id=run_id,
                source="supervisor",
                kind="supervisor_lesson_injection",
                payload={
                    "schema_version": injection["schema_version"],
                    "task_id": task_id,
                    "gate": gate,
                    "round_index": int(round_index),
                    "task_class": lesson_task_class,
                    "lesson_ids": injection["lesson_ids"],
                    "lesson_count": injection["lesson_count"],
                    "block": injection["block"],
                    "block_sha256": injection["block_sha256"],
                    "advisory_only": True,
                    "gate_authority": "unchanged",
                },
            )
        return {
            "task_id": task_id,
            "run_id": run_id,
            "gate": gate,
            "instruction": instruction,
            "planning_rubric_threshold": _planning_rubric_threshold_for_gate(
                self.cfg,
                policy_overlay.rubric_thresholds,
                gate=gate,
            ),
            "planning_rubric_unavailable_policy": self.cfg.planning_rubric.unavailable_policy,
            "injected_lesson_block": str(injection["block"]),
            "injected_lesson_block_sha256": str(injection["block_sha256"]),
            "injected_lesson_ids": list(injection["lesson_ids"]),
            "policy_overlay_block": str(overlay_payload["block"]),
            "policy_overlay_block_sha256": str(overlay_payload["block_sha256"]),
            "policy_overlay_hash": str(policy_overlay.content_hash),
            "policy_proposal_id": str(policy_overlay.proposal_id),
            "policy_overlay_task_class": str(overlay_payload.get("task_class") or ""),
            "policy_overlay_frozen": bool(overlay_payload.get("overlay_frozen")),
            "policy_overlay_task_class_hash": str(overlay_payload.get("task_class_overlay_hash") or ""),
        }

    def _gate_spec(
        self,
        *,
        task_id: str,
        run_id: str,
        gate: GateName,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None,
        expected_decisions: list[str] | None,
        expected_objections: list[str] | None,
        quality: str,
        model: str | None,
        budget_usd: float,
        timeout_s: int,
        execution_layer_mode: str,
        dynamic_workflow_task_class: str | None,
        agentic_policy: dict[str, Any],
        planning_artifacts: list[dict[str, Any]] | None,
        required_planning_kinds: tuple[str, ...] | list[str] | None = None,
        injected_lesson_block: str = "",
        injected_lesson_block_sha256: str = "",
        injected_lesson_ids: tuple[str, ...] = (),
        policy_overlay_block: str = "",
        policy_overlay_block_sha256: str = "",
        policy_overlay_hash: str = "",
        policy_proposal_id: str = "",
        policy_overlay_task_class: str = "",
        policy_overlay_frozen: bool = False,
        policy_overlay_task_class_hash: str = "",
        planning_rubric_threshold: float | None = None,
        planning_rubric_unavailable_policy: str | None = None,
    ) -> DualAgentGateSpec:
        return DualAgentGateSpec(
            task_id=task_id,
            run_id=run_id,
            gate=gate,
            instruction=instruction,
            cwd=cwd,
            expected_specialists=tuple(expected_specialists or ()),
            expected_decisions=tuple(expected_decisions or ()),
            expected_objections=tuple(expected_objections or ()),
            quality=quality,  # type: ignore[arg-type]
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            execution_layer_mode=execution_layer_mode,  # type: ignore[arg-type]
            dynamic_workflow_task_class=dynamic_workflow_task_class,  # type: ignore[arg-type]
            agentic_lead_policy=agentic_policy["agentic_lead_policy"],  # type: ignore[arg-type]
            min_subagents=int(agentic_policy["min_subagents"]),
            required_roles=tuple(str(role) for role in agentic_policy["required_roles"]),
            solo_exception_for_artifact_only_gates=bool(
                agentic_policy["solo_exception_for_artifact_only_gates"]
            ),
            required_evidence_grade=agentic_policy["required_evidence_grade"],  # type: ignore[arg-type]
            required_planning_kinds=(
                tuple(str(kind) for kind in required_planning_kinds)
                if required_planning_kinds is not None else None
            ),
            injected_lesson_block=injected_lesson_block,
            injected_lesson_block_sha256=injected_lesson_block_sha256,
            injected_lesson_ids=injected_lesson_ids,
            policy_overlay_block=policy_overlay_block,
            policy_overlay_block_sha256=policy_overlay_block_sha256,
            policy_overlay_hash=policy_overlay_hash,
            policy_proposal_id=policy_proposal_id,
            policy_overlay_task_class=policy_overlay_task_class,
            policy_overlay_frozen=policy_overlay_frozen,
            policy_overlay_task_class_hash=policy_overlay_task_class_hash,
            planning_rubric_threshold=(
                _clamp_planning_rubric_threshold(planning_rubric_threshold)
                if planning_rubric_threshold is not None
                else float(self.cfg.planning_rubric.threshold)
            ),
            planning_rubric_unavailable_policy=(
                str(planning_rubric_unavailable_policy or self.cfg.planning_rubric.unavailable_policy)
            ),
            planning_artifacts=tuple(
                artifact
                for artifact in (
                    _maybe_artifact(item)
                    for item in (planning_artifacts or [])
                )
                if artifact is not None
            ),
        )

    def _record_reviewer_unavailable_recovery(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: str,
        status: str,
        policy: str,
        classification: str,
        cursor_review: dict[str, Any] | None,
        recovery: dict[str, Any],
        available_reviewers: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "task_id": task_id,
            "gate": gate,
            "status": status,
            "policy": policy,
            "classification": classification,
            "evidence_grade": "degraded",
            "reviewer_verdict_counted_as_accept": False,
            "available_reviewers": available_reviewers,
            "cursor_review": cursor_review,
            "independent_reviewer": cursor_review,
            "authorization": recovery.get("authorization"),
            "forced_by_safety": bool(recovery.get("forced_by_safety")),
            "safety_reasons": list(recovery.get("safety_reasons") or []),
            "unavailable_reviewers": list(recovery.get("unavailable_reviewers") or []),
            "decision": recovery.get("decision"),
            "recovery": recovery,
        }
        event_id = self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_reviewer_unavailable_recovery",
            payload=payload,
        )
        return {"event_id": event_id, **payload}

    async def _request_reviewer_unavailable_escalation(
        self,
        *,
        notifier: Any | None,
        run_id: str,
        task_id: str,
        gate: str,
        policy: str,
        classification: str,
        cursor_review: dict[str, Any] | None,
        recovery: dict[str, Any],
        available_reviewers: dict[str, Any],
    ) -> dict[str, Any]:
        current = int(time.time())
        nonce = uuid.uuid4().hex[:16]
        options = ["Pause", "Kill", "Continue"]
        safety_reasons = list(recovery.get("safety_reasons") or [])
        question = (
            f"[{task_id}] Reviewer unavailable during dual-agent workflow.\n"
            f"gate={gate}\n"
            f"policy={policy}\n"
            f"classification={classification}\n"
            "Cursor did not return a usable verdict. Continue authorizes "
            "proceed-degraded on Claude+Codex only; the missing Cursor verdict "
            "is recorded as degraded evidence, not as accept."
        )
        if safety_reasons:
            question += "\nSafety escalation reasons: " + ", ".join(safety_reasons)
        ask_id = self.state.create_ask(
            run_id,
            question,
            options,
            nonce=nonce,
            expires_at=current + 3600,
        )
        action_id = self.state.record_action(
            run_id=run_id,
            action_type="dual_agent_gate_deadlock",
            requested_by="dual_agent_workflow",
            payload={
                "task_id": task_id,
                "gate": gate,
                "ask_id": ask_id,
                "nonce": nonce,
                "options": options,
                "escalation_type": "reviewer_unavailable",
                "reason": "reviewer_unavailable",
                "classification": classification,
                "policy": policy,
                "forced_by_safety": bool(recovery.get("forced_by_safety")),
                "safety_reasons": safety_reasons,
                "unavailable_reviewers": list(recovery.get("unavailable_reviewers") or []),
                "available_reviewers": available_reviewers,
                "cursor_review": cursor_review,
                "independent_reviewer": cursor_review,
                "reviewer_verdict_counted_as_accept": False,
                "evidence_grade": "degraded",
            },
            status="paused_for_human",
        )
        if notifier is not None:
            await notifier.send_approval_prompt(
                ask_id=ask_id,
                question=question,
                options=options,
                nonce=nonce,
            )
        return redact({
            "status": "paused_for_human",
            "policy": policy,
            "classification": classification,
            "forced_by_safety": bool(recovery.get("forced_by_safety")),
            "safety_reasons": safety_reasons,
            "unavailable_reviewers": list(recovery.get("unavailable_reviewers") or []),
            "action_id": action_id,
            "ask_id": ask_id,
            "nonce": nonce,
            "options": options,
            "reviewer_verdict_counted_as_accept": False,
            "evidence_grade": "degraded",
            "independent_reviewer": cursor_review,
        })

    def _notifier(self) -> Any | None:
        if self.notifier is not None:
            return self.notifier
        if not telegram_enabled(self.cfg):
            return None
        return TelegramNotifier(self.cfg)


def build_codex_supervisor_mcp_server(
    cfg: Config,
    state: State,
    *,
    api: CodexSupervisorMcpAPI | None = None,
    mcp_cls: Any | None = None,
    runner: Runner = subprocess.run,
    codex_runner: Runner = subprocess.run,
    cursor_runner: CursorRunner | None = None,
    no_mistakes_runner: Runner = subprocess.run,
    notifier: Any | None = None,
) -> Any:
    if mcp_cls is None:
        from mcp.server.fastmcp import FastMCP
        mcp_cls = FastMCP

    tool_api = api or CodexSupervisorMcpAPI(
        cfg,
        state,
        runner=runner,
        codex_runner=codex_runner,
        cursor_runner=cursor_runner,
        no_mistakes_runner=no_mistakes_runner,
        notifier=notifier,
    )
    _configure_mcp_transport_logging()
    try:
        mcp = mcp_cls("codex_supervisor", log_level="ERROR")
    except TypeError:
        mcp = mcp_cls("codex_supervisor")
    setattr(mcp, "_codex_supervisor_tool_api", tool_api)

    @mcp.tool()
    async def start_dual_agent_gate(
        task_id: str,
        run_id: str,
        gate: str,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None = None,
        expected_decisions: list[str] | None = None,
        expected_objections: list[str] | None = None,
        quality: str = "best",
        model: str | None = None,
        budget_usd: float = 5.0,
        timeout_s: int = 600,
        execution_layer_mode: str = "lead_direct",
        dynamic_workflow_task_class: str | None = None,
        agentic_lead_policy: str | None = None,
        min_subagents: int | None = None,
        required_roles: list[str] | None = None,
        solo_exception_for_artifact_only_gates: bool | None = None,
        required_evidence_grade: str | None = None,
        planning_artifacts: list[dict[str, Any]] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        artifact_policy: str = "strict",
        user_facing: bool = False,
        screenshots: list[dict[str, Any]] | None = None,
        required_artifacts: list[str] | None = None,
        required_prerequisite_gates: list[str] | None = None,
        required_planning_kinds: list[str] | None = None,
        policy_overlay_task_class: str = "",
        policy_overlay_frozen: bool = False,
        policy_overlay_task_class_hash: str = "",
        planning_rubric_threshold: float | None = None,
        planning_rubric_unavailable_policy: str | None = None,
    ) -> dict[str, Any]:
        return await tool_api.start_dual_agent_gate(
            task_id=task_id,
            run_id=run_id,
            gate=gate,  # type: ignore[arg-type]
            instruction=instruction,
            cwd=cwd,
            expected_specialists=expected_specialists,
            expected_decisions=expected_decisions,
            expected_objections=expected_objections,
            quality=quality,
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            execution_layer_mode=execution_layer_mode,
            dynamic_workflow_task_class=dynamic_workflow_task_class,
            agentic_lead_policy=agentic_lead_policy,
            min_subagents=min_subagents,
            required_roles=required_roles,
            solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
            required_evidence_grade=required_evidence_grade,
            planning_artifacts=planning_artifacts,
            tool_receipts=tool_receipts,
            artifact_policy=artifact_policy,
            user_facing=user_facing,
            screenshots=screenshots,
            required_artifacts=required_artifacts,
            required_prerequisite_gates=required_prerequisite_gates,
            required_planning_kinds=required_planning_kinds,
            policy_overlay_task_class=policy_overlay_task_class,
            policy_overlay_frozen=policy_overlay_frozen,
            policy_overlay_task_class_hash=policy_overlay_task_class_hash,
            planning_rubric_threshold=planning_rubric_threshold,
            planning_rubric_unavailable_policy=planning_rubric_unavailable_policy,
        )

    @mcp.tool()
    def poll_resume_signal(
        task_id: str,
        run_id: str,
        gate: str,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None = None,
        expected_decisions: list[str] | None = None,
        expected_objections: list[str] | None = None,
        quality: str = "best",
        model: str | None = None,
        budget_usd: float = 5.0,
        timeout_s: int = 600,
        execution_layer_mode: str = "lead_direct",
        dynamic_workflow_task_class: str | None = None,
        agentic_lead_policy: str | None = None,
        min_subagents: int | None = None,
        required_roles: list[str] | None = None,
        solo_exception_for_artifact_only_gates: bool | None = None,
        required_evidence_grade: str | None = None,
        planning_artifacts: list[dict[str, Any]] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        artifact_policy: str = "strict",
        user_facing: bool = False,
        screenshots: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return tool_api.poll_resume_signal(
            task_id=task_id,
            run_id=run_id,
            gate=gate,  # type: ignore[arg-type]
            instruction=instruction,
            cwd=cwd,
            expected_specialists=expected_specialists,
            expected_decisions=expected_decisions,
            expected_objections=expected_objections,
            quality=quality,
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            execution_layer_mode=execution_layer_mode,
            dynamic_workflow_task_class=dynamic_workflow_task_class,
            agentic_lead_policy=agentic_lead_policy,
            min_subagents=min_subagents,
            required_roles=required_roles,
            solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
            required_evidence_grade=required_evidence_grade,
            planning_artifacts=planning_artifacts,
            tool_receipts=tool_receipts,
            artifact_policy=artifact_policy,
            user_facing=user_facing,
            screenshots=screenshots,
        )

    @mcp.tool()
    def record_gate_round(
        run_id: str,
        task_id: str,
        gate: str,
        round_index: int,
        codex_decision: str,
        claude_decision: str,
        codex_confidence: float,
        claude_confidence: float,
        objection: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        return tool_api.record_gate_round(
            run_id=run_id,
            task_id=task_id,
            gate=gate,  # type: ignore[arg-type]
            round_index=round_index,
            codex_decision=codex_decision,
            claude_decision=claude_decision,
            codex_confidence=codex_confidence,
            claude_confidence=claude_confidence,
            objection=objection,
            cwd=cwd,
        )

    @mcp.tool()
    def check_budget(
        rounds: list[dict[str, Any]],
        per_gate_cap: int,
        task_budget: int,
    ) -> dict[str, Any]:
        return tool_api.check_budget(
            rounds=rounds,
            per_gate_cap=per_gate_cap,
            task_budget=task_budget,
        )

    @mcp.tool()
    async def escalate_deadlock(
        run_id: str,
        task_id: str,
        gate: str,
        rounds: list[dict[str, Any]],
        per_gate_cap: int,
        task_budget: int,
    ) -> dict[str, Any]:
        return await tool_api.escalate_deadlock(
            run_id=run_id,
            task_id=task_id,
            gate=gate,  # type: ignore[arg-type]
            rounds=rounds,
            per_gate_cap=per_gate_cap,
            task_budget=task_budget,
        )

    @mcp.tool()
    def read_outcome(run_id: str, task_id: str) -> dict[str, Any]:
        return tool_api.read_outcome(run_id=run_id, task_id=task_id)

    @mcp.tool()
    def read_gate_transcript(run_id: str, task_id: str) -> dict[str, Any]:
        return tool_api.read_gate_transcript(run_id=run_id, task_id=task_id)

    @mcp.tool()
    def export_gate_artifacts(
        run_id: str,
        task_id: str,
        cwd: str,
        output_dir: str | None = None,
        screenshots: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return tool_api.export_gate_artifacts(
            run_id=run_id,
            task_id=task_id,
            cwd=cwd,
            output_dir=output_dir,
            screenshots=screenshots,
        )

    @mcp.tool()
    def create_autoresearch_policy_proposals(
        report_path: str,
        repo_root: str,
        affected_gates: list[str],
        candidate_changes: dict[str, str] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        return tool_api.create_autoresearch_policy_proposals(
            report_path=report_path,
            repo_root=repo_root,
            affected_gates=affected_gates,
            candidate_changes=candidate_changes,
            run_id=run_id,
        )

    @mcp.tool()
    def list_autoresearch_experiments(
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return tool_api.list_autoresearch_experiments(status=status, limit=limit)

    @mcp.tool()
    def generate_autoresearch_experiments(
        repo_root: str,
    ) -> dict[str, Any]:
        return tool_api.generate_autoresearch_experiments(repo_root=repo_root)

    @mcp.tool()
    def activate_autoresearch_experiment(
        experiment_id: str,
        operator: str,
        approval_channel: str,
    ) -> dict[str, Any]:
        return tool_api.activate_autoresearch_experiment(
            experiment_id=experiment_id,
            operator=operator,
            approval_channel=approval_channel,
        )

    @mcp.tool()
    def park_autoresearch_experiment(
        experiment_id: str,
        operator: str,
        approval_channel: str,
        reason: str,
    ) -> dict[str, Any]:
        return tool_api.park_autoresearch_experiment(
            experiment_id=experiment_id,
            operator=operator,
            approval_channel=approval_channel,
            reason=reason,
        )

    @mcp.tool()
    def approve_autoresearch_policy_proposal(
        repo_root: str,
        run_id: str,
        approver: str,
        approval_channel: str,
        proposal: dict[str, Any] | None = None,
        proposal_path: str | None = None,
        rollback_root: str = ".handoff/policy-rollbacks",
    ) -> dict[str, Any]:
        return tool_api.approve_autoresearch_policy_proposal(
            proposal=proposal,
            proposal_path=proposal_path,
            repo_root=repo_root,
            run_id=run_id,
            approver=approver,
            approval_channel=approval_channel,
            rollback_root=rollback_root,
        )

    @mcp.tool()
    def deny_autoresearch_policy_proposal(
        repo_root: str,
        run_id: str,
        approver: str,
        approval_channel: str,
        reason: str,
        proposal: dict[str, Any] | None = None,
        proposal_path: str | None = None,
    ) -> dict[str, Any]:
        return tool_api.deny_autoresearch_policy_proposal(
            proposal=proposal,
            proposal_path=proposal_path,
            repo_root=repo_root,
            run_id=run_id,
            approver=approver,
            approval_channel=approval_channel,
            reason=reason,
        )

    @mcp.tool()
    def rollback_autoresearch_policy_proposal(
        repo_root: str,
        run_id: str,
        approver: str,
        approval_channel: str,
        reason: str = "",
        rollback_pointer: dict[str, Any] | None = None,
        rollback_pointer_path: str | None = None,
    ) -> dict[str, Any]:
        return tool_api.rollback_autoresearch_policy_proposal(
            rollback_pointer=rollback_pointer,
            rollback_pointer_path=rollback_pointer_path,
            repo_root=repo_root,
            run_id=run_id,
            approver=approver,
            approval_channel=approval_channel,
            reason=reason,
        )

    @mcp.tool()
    async def run_dual_agent_workflow(
        cwd: str,
        task_id: str,
        run_id: str,
        intent: str,
        user_facing: bool = False,
        visual_evidence_policy: str = "auto",
        max_rounds_per_gate: int = 5,
        quality: str = "best",
        budget_usd: float = 100.0,
        timeout_s: int = 900,
        execution_layer_mode: str = "lead_direct",
        dynamic_workflow_task_class: str | None = None,
        agentic_lead_policy: str | None = None,
        min_subagents: int | None = None,
        required_roles: list[str] | None = None,
        solo_exception_for_artifact_only_gates: bool | None = None,
        required_evidence_grade: str | None = None,
        reviewer_unavailable_policy: str | None = None,
        planning_artifacts: list[dict[str, Any]] | None = None,
        screenshots: list[dict[str, Any]] | None = None,
        verified_claims: list[str] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        require_skill_receipts: bool = True,
        cursor_review: bool = True,
        cursor_review_profile: str = "default",
        cursor_review_gates: list[str] | None = None,
        cursor_model: str | None = None,
        reviewer_model: str | None = None,
        reviewer_output_mode: str | None = None,
        reviewer_max_tokens: int | None = None,
        reviewer_infra_retry_limit: int | None = None,
        reviewer_infra_retry_backoff_s: float | None = None,
        reviewer_low_confidence_threshold: float | None = None,
        reviewer_panel_calibration_path: str | None = None,
        task_complexity: str | None = None,
        no_mistakes_policy: str | None = None,
        no_mistakes_skip_steps: list[str] | None = None,
        no_mistakes_timeout_s: int | None = None,
        config_path: str | None = None,
        client_token: str | None = None,
    ) -> dict[str, Any]:
        payload = tool_api.submit_dual_agent_workflow_job(
            cwd=cwd,
            task_id=task_id,
            run_id=run_id,
            intent=intent,
            user_facing=user_facing,
            visual_evidence_policy=visual_evidence_policy,
            max_rounds_per_gate=max_rounds_per_gate,
            quality=quality,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            execution_layer_mode=execution_layer_mode,
            dynamic_workflow_task_class=dynamic_workflow_task_class,
            agentic_lead_policy=agentic_lead_policy,
            min_subagents=min_subagents,
            required_roles=required_roles,
            solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
            required_evidence_grade=required_evidence_grade,
            reviewer_unavailable_policy=reviewer_unavailable_policy,
            planning_artifacts=planning_artifacts,
            screenshots=screenshots,
            verified_claims=verified_claims,
            tool_receipts=tool_receipts,
            require_skill_receipts=require_skill_receipts,
            cursor_review=cursor_review,
            cursor_review_profile=cursor_review_profile,
            cursor_review_gates=cursor_review_gates,
            cursor_model=cursor_model,
            reviewer_model=reviewer_model,
            reviewer_output_mode=reviewer_output_mode,
            reviewer_max_tokens=reviewer_max_tokens,
            reviewer_infra_retry_limit=reviewer_infra_retry_limit,
            reviewer_infra_retry_backoff_s=reviewer_infra_retry_backoff_s,
            reviewer_low_confidence_threshold=reviewer_low_confidence_threshold,
            reviewer_panel_calibration_path=reviewer_panel_calibration_path,
            task_complexity=task_complexity,
            no_mistakes_policy=no_mistakes_policy,
            no_mistakes_skip_steps=no_mistakes_skip_steps,
            no_mistakes_timeout_s=no_mistakes_timeout_s,
            config_path=config_path,
            client_token=client_token,
        )
        payload["compatibility_tool"] = "run_dual_agent_workflow"
        payload["execution_model"] = "detached_dispatcher_only"
        payload["catch_up_tool"] = "catch_up_dual_agent_workflow"
        payload["help"] = [
            f"Run `poll_dual_agent_workflow_job(job_id={payload['job_id']!r})` for job state.",
            f"Run `catch_up_dual_agent_workflow(run_id={run_id!r})` for the event tail.",
            f"Prefer AXI: `codex-supervisor-axi --json poll {payload['job_id']}`.",
            f"Prefer AXI: `codex-supervisor-axi --json catch-up {run_id}`.",
        ]
        return payload

    @mcp.tool()
    def submit_dual_agent_workflow_job(
        cwd: str,
        task_id: str,
        run_id: str,
        intent: str,
        user_facing: bool = False,
        visual_evidence_policy: str = "auto",
        max_rounds_per_gate: int = 5,
        quality: str = "best",
        budget_usd: float = 100.0,
        timeout_s: int = 900,
        execution_layer_mode: str = "lead_direct",
        dynamic_workflow_task_class: str | None = None,
        agentic_lead_policy: str | None = None,
        min_subagents: int | None = None,
        required_roles: list[str] | None = None,
        solo_exception_for_artifact_only_gates: bool | None = None,
        required_evidence_grade: str | None = None,
        reviewer_unavailable_policy: str | None = None,
        planning_artifacts: list[dict[str, Any]] | None = None,
        screenshots: list[dict[str, Any]] | None = None,
        verified_claims: list[str] | None = None,
        tool_receipts: list[dict[str, Any]] | None = None,
        require_skill_receipts: bool = True,
        cursor_review: bool = True,
        cursor_review_profile: str = "default",
        cursor_review_gates: list[str] | None = None,
        cursor_model: str | None = None,
        reviewer_model: str | None = None,
        reviewer_output_mode: str | None = None,
        reviewer_max_tokens: int | None = None,
        reviewer_infra_retry_limit: int | None = None,
        reviewer_infra_retry_backoff_s: float | None = None,
        reviewer_low_confidence_threshold: float | None = None,
        reviewer_panel_calibration_path: str | None = None,
        task_complexity: str | None = None,
        no_mistakes_policy: str | None = None,
        no_mistakes_skip_steps: list[str] | None = None,
        no_mistakes_timeout_s: int | None = None,
        config_path: str | None = None,
        client_token: str | None = None,
    ) -> dict[str, Any]:
        return tool_api.submit_dual_agent_workflow_job(
            cwd=cwd,
            task_id=task_id,
            run_id=run_id,
            intent=intent,
            user_facing=user_facing,
            visual_evidence_policy=visual_evidence_policy,
            max_rounds_per_gate=max_rounds_per_gate,
            quality=quality,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            execution_layer_mode=execution_layer_mode,
            dynamic_workflow_task_class=dynamic_workflow_task_class,
            agentic_lead_policy=agentic_lead_policy,
            min_subagents=min_subagents,
            required_roles=required_roles,
            solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
            required_evidence_grade=required_evidence_grade,
            reviewer_unavailable_policy=reviewer_unavailable_policy,
            planning_artifacts=planning_artifacts,
            screenshots=screenshots,
            verified_claims=verified_claims,
            tool_receipts=tool_receipts,
            require_skill_receipts=require_skill_receipts,
            cursor_review=cursor_review,
            cursor_review_profile=cursor_review_profile,
            cursor_review_gates=cursor_review_gates,
            cursor_model=cursor_model,
            reviewer_model=reviewer_model,
            reviewer_output_mode=reviewer_output_mode,
            reviewer_max_tokens=reviewer_max_tokens,
            reviewer_infra_retry_limit=reviewer_infra_retry_limit,
            reviewer_infra_retry_backoff_s=reviewer_infra_retry_backoff_s,
            reviewer_low_confidence_threshold=reviewer_low_confidence_threshold,
            reviewer_panel_calibration_path=reviewer_panel_calibration_path,
            task_complexity=task_complexity,
            no_mistakes_policy=no_mistakes_policy,
            no_mistakes_skip_steps=no_mistakes_skip_steps,
            no_mistakes_timeout_s=no_mistakes_timeout_s,
            config_path=config_path,
            client_token=client_token,
        )

    @mcp.tool()
    def poll_dual_agent_workflow_job(job_id: str) -> dict[str, Any]:
        return tool_api.poll_dual_agent_workflow_job(job_id=job_id)

    @mcp.tool()
    def catch_up_dual_agent_workflow(
        run_id: str,
        last_event_id: int | None = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        return tool_api.catch_up_dual_agent_workflow(
            run_id=run_id,
            last_event_id=last_event_id,
            limit=limit,
        )

    @mcp.tool()
    def read_dual_agent_workflow_resume_prompt(run_id: str, task_id: str) -> dict[str, Any]:
        return tool_api.read_dual_agent_workflow_resume_prompt(run_id=run_id, task_id=task_id)

    @mcp.tool()
    def start_codex_session(
        prompt: str,
        cwd: str,
        model: str | None = None,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        execute: bool = False,
        timeout_s: int = 600,
    ) -> dict[str, Any]:
        return tool_api.start_codex_session(
            prompt=prompt,
            cwd=cwd,
            model=model,
            reasoning_effort=reasoning_effort,
            execute=execute,
            timeout_s=timeout_s,
        )

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Codex supervisor MCP server over stdio.")
    parser.add_argument(
        "--config",
        default=str(Path.home() / ".codex-supervisor" / "config.yaml"),
        help="Path to supervisor YAML config.",
    )
    args = parser.parse_args(argv)
    cfg = Config.load(args.config)
    state = State(cfg.supervisor.state_db)
    server = build_codex_supervisor_mcp_server(cfg, state)
    server.run(transport="stdio")


def _configure_mcp_transport_logging() -> None:
    """Keep stdio transports protocol-only and move MCP chatter below warning."""
    for logger_name in (
        "mcp",
        "mcp.server",
        "mcp.server.lowlevel",
        "mcp.server.lowlevel.server",
        "mcp.server.fastmcp",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _gate_result_payload(
    result: DualAgentGateResult,
    *,
    gate_tool_call: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "task_id": result.task_id,
        "gate": result.gate,
        "status": result.status,
        "claude_gate_status": result.status,
        "supervisor_final_status": result.status,
        "attempts": result.attempts,
        "handoff_packet_path": str(result.handoff_packet_path),
        "probes": {
            key: asdict(value)
            for key, value in result.probes.items()
        },
        "tool_calls": _gate_result_tool_calls(result, gate_tool_call=gate_tool_call),
        "outcome": result.outcome.model_dump() if result.outcome is not None else None,
        "escalation": asdict(result.escalation) if result.escalation is not None else None,
    }
    return redact(payload)


def _gate_result_tool_calls(
    result: DualAgentGateResult,
    *,
    gate_tool_call: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    call = dict(gate_tool_call or {})
    call.update({
        "name": "start_dual_agent_gate",
        "status": call.get("status") or "completed",
        "attempts": result.attempts,
        "handoff_packet_path": str(result.handoff_packet_path),
    })
    call.setdefault("args", {
        "task_id": result.task_id,
        "gate": result.gate,
    })
    call.setdefault("result_summary", {
        "claude_gate_status": result.status,
        "supervisor_final_status": result.status,
        "probe_statuses": {
            key: value.status for key, value in result.probes.items()
        },
    })
    gate_call = ensure_tool_call_timing(call)
    gate_tool_call_id = gate_call.get("tool_call_id")
    calls: list[dict[str, Any]] = [gate_call]
    lead_tool_call_id: str | None = None
    if result.lead_result is not None:
        lead_call = ensure_tool_call_timing({
            "name": "invoke_claude_lead",
            "status": "completed" if result.probes.get("P2", result.lead_result.probe).ok else "failed",
            "attempts": result.attempts,
            "parent_tool_call_id": gate_tool_call_id,
            "args": {
                "task_id": result.task_id,
                "gate": result.gate,
            },
            "model": result.lead_result.model,
            "cost_usd": result.lead_result.cost_usd,
            "tokens_in": result.lead_result.tokens_in,
            "tokens_out": result.lead_result.tokens_out,
            "token_usage": result.lead_result.token_usage,
            "stdout_bytes": result.lead_result.stdout_bytes,
            "stderr_bytes": result.lead_result.stderr_bytes,
            "result_summary": {
                "probe_status": result.lead_result.probe.status,
                "probe_reason": result.lead_result.probe.reason,
                "outcome_present": result.outcome is not None,
                "tokens_in": result.lead_result.tokens_in,
                "tokens_out": result.lead_result.tokens_out,
            },
        })
        lead_tool_call_id = str(lead_call.get("tool_call_id") or "")
        calls.append(lead_call)
    for probe_id, probe in result.probes.items():
        calls.append(ensure_tool_call_timing({
            "name": f"probe:{probe_id}",
            "status": probe.status,
            "probe_id": probe.probe_id,
            "reason": probe.reason,
            "parent_tool_call_id": lead_tool_call_id or gate_tool_call_id,
            "args": {"probe_id": probe_id},
            "result_summary": {
                "probe_id": probe.probe_id,
                "status": probe.status,
                "reason": probe.reason,
            },
        }))
    return calls


def _artifact_blocked_payload(
    *,
    task_id: str,
    gate: str,
    artifact_preflight: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "gate": gate,
        "status": "blocked",
        "claude_gate_status": "not_invoked",
        "supervisor_final_status": "blocked",
        "attempts": 0,
        "handoff_packet_path": None,
        "probes": {},
        "outcome": None,
        "escalation": {
            "type": "artifact_rigor",
            "reason": str(artifact_preflight.get("reason") or "required_artifacts_missing"),
            "details": artifact_preflight,
        },
        "artifact_rigor": artifact_preflight,
    }


def _gate_round_payload(
    *,
    round_index: int,
    codex_decision: str,
    claude_decision: str,
    codex_confidence: float,
    claude_confidence: float,
    objection: str | None,
) -> dict[str, Any]:
    return asdict(GateRound(
        round_index=round_index,
        codex_decision=codex_decision,  # type: ignore[arg-type]
        claude_decision=claude_decision,  # type: ignore[arg-type]
        codex_confidence=codex_confidence,
        claude_confidence=claude_confidence,
        objection=objection,
    ))


def _gate_round_from_payload(payload: dict[str, Any]) -> GateRound:
    return GateRound(
        round_index=int(payload["round_index"]),
        codex_decision=str(payload["codex_decision"]),  # type: ignore[arg-type]
        claude_decision=str(payload["claude_decision"]),  # type: ignore[arg-type]
        codex_confidence=float(payload["codex_confidence"]),
        claude_confidence=float(payload["claude_confidence"]),
        objection=payload.get("objection"),
    )


def _workflow_gate_instruction(
    *,
    gate: str,
    intent: str,
    corrective_context: str,
    tdd_test_names: list[str] | tuple[str, ...] = (),
) -> str:
    if gate == "execution":
        gate_action_lines = [
            "Implement this gate against the accepted source artifacts and return a typed dual_agent_outcome.",
            "Edit the worktree as needed; use decisions/objections to report whether execution is complete, blocked, or failed.",
        ]
    else:
        gate_action_lines = [
            "Review this gate against the current source artifacts and return a typed dual_agent_outcome.",
            "Use decisions/objections to say whether the gate should accept, revise, or deny.",
        ]
    lines = [
        f"Supervisor-owned workflow gate: {gate}.",
        "",
        "Intent:",
        intent.strip(),
        "",
        *gate_action_lines,
    ]
    if corrective_context:
        lines.extend([
            "",
            "Corrective context from the previous round:",
            corrective_context.strip(),
        ])
    if gate in {"execution", "outcome_review"} and tdd_test_names:
        lines.extend([
            "",
            "Runtime TDD test contract:",
            (
                "The supervisor runtime floor will verify that every TDD-named test below "
                "appears in supervisor-generated runtime evidence. Include tests/commands "
                "covering all of them in outcome.tests. Explicitly skipped tests must carry "
                "a recorded pytest skip reason; silently absent tests block the gate."
            ),
            (
                "Use only canonical gate decisions (`accept`, `revise`, or `deny`). Do not "
                "return `accept_with_residual`; if test execution needs verification, declare "
                "the exact pytest commands/nodeids and let the supervisor runtime floor rerun them."
            ),
            *[f"- {name}" for name in tdd_test_names],
        ])
    return "\n".join(lines)


def _workflow_tdd_test_names(
    cwd: str | Path,
    planning_artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[str]:
    cwd_path = Path(cwd).expanduser().resolve()
    names: list[str] = []
    for artifact in planning_artifacts:
        kind = str(artifact.get("kind") or "").strip().lower().replace("-", "_")
        path_text = str(artifact.get("path") or "").strip()
        if not path_text:
            continue
        if kind not in {"tdd", "tdd_plan", "test_plan"}:
            file_name = Path(path_text).name.lower()
            if kind or "tdd" not in file_name or "grill" in file_name:
                continue
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = cwd_path / path
        try:
            names.extend(_tdd_test_names(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            continue
    return list(dict.fromkeys(names))


def _lesson_task_class(
    *,
    dynamic_workflow_task_class: str | None,
    workflow_route: dict[str, Any],
) -> str:
    explicit = str(dynamic_workflow_task_class or "").strip().replace("-", "_")
    if explicit:
        return explicit
    route_class = str(workflow_route.get("task_complexity") or "").strip().replace("-", "_")
    return route_class or "general"


def _workflow_lesson_snapshot(
    state: State,
    *,
    lesson_task_class: str,
    route_gates: tuple[str, ...],
) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    for gate in route_gates:
        injection = build_lesson_injection(
            state.query_supervisor_lessons(
                task_class=lesson_task_class,
                gate=gate,
                limit=5,
            )
        )
        if injection["lesson_count"]:
            gates[gate] = injection
    return {
        "schema_version": "supervisor-lesson-snapshot/v1",
        "task_class": lesson_task_class,
        "gates": gates,
        "advisory_only": True,
        "gate_authority": "unchanged",
    }


def _failure_taxonomy_codes_from_events(events: list[dict[str, Any]]) -> set[str]:
    codes: set[str] = set()
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        candidates: list[Any] = []
        if isinstance(payload.get("failure_taxonomy"), dict):
            candidates.append(payload["failure_taxonomy"])
        trace = payload.get("trace_envelope") if isinstance(payload.get("trace_envelope"), dict) else {}
        if isinstance(trace.get("failure_taxonomy"), dict):
            candidates.append(trace["failure_taxonomy"])
        for taxonomy in candidates:
            code = (
                taxonomy.get("mast_code")
                or taxonomy.get("code")
                or taxonomy.get("taxonomy_code")
            )
            if str(code or "").strip():
                codes.add(str(code).strip())
    return codes


def _lesson_feedback_has_gate_verdict(events: list[dict[str, Any]]) -> bool:
    verdict_statuses = {
        "accept",
        "accepted",
        "block",
        "blocked",
        "deny",
        "denied",
        "fail",
        "failed",
        "revise",
        "revision_required",
    }
    for event in events:
        if event.get("kind") != "dual_agent_gate_result":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for key in ("supervisor_final_status", "status", "claude_gate_status", "codex_decision"):
            status = str(payload.get(key) or "").strip().lower()
            if status in verdict_statuses:
                return True
        outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
        decision = str(outcome.get("decision") or "").strip().lower()
        if decision in verdict_statuses:
            return True
    return False


def _cursor_review_instruction(
    *,
    gate: str,
    intent: str,
    corrective_context: str,
) -> str:
    lines = [
        f"Independently review the {gate} gate for this tri-agent workflow.",
        "Accept only if the gate should advance after reading the artifacts and Claude outcome.",
        critical_review_prompt("Claude outcome and gate evidence"),
        "Return the structured critical_review object in the typed outcome.",
        "",
        "Intent:",
        intent.strip(),
    ]
    if corrective_context:
        lines.extend([
            "",
            "Corrective context from the previous round:",
            corrective_context.strip(),
        ])
    return "\n".join(lines)


def _workflow_round_objection(
    *,
    payload: dict[str, Any],
    runtime_probe: dict[str, Any] | None,
    deliverable_probe: dict[str, Any] | None,
    claim_probe: dict[str, Any] | None,
    cursor_review: dict[str, Any] | None,
    round_index: int,
    max_rounds: int,
) -> str:
    if payload.get("status") == "blocked" and not _payload_blocked_for_lead_revision(payload):
        escalation = payload.get("escalation") if isinstance(payload.get("escalation"), dict) else {}
        return str(escalation.get("reason") or "gate blocked")
    if runtime_probe is not None and runtime_probe.get("status") != "green":
        return _probe_failure_objection("runtime_evidence_failed", runtime_probe)
    if deliverable_probe is not None and deliverable_probe.get("status") != "green":
        return _probe_failure_objection("deliverable_evidence_failed", deliverable_probe)
    if claim_probe is not None and claim_probe.get("status") != "green":
        return str(claim_probe.get("reason") or "claim verification failed")
    if cursor_review is not None:
        packet = cursor_review.get("supervisor_review_packet")
        if isinstance(packet, dict):
            validation = packet.get("validation") if isinstance(packet.get("validation"), dict) else {}
            if validation.get("status") == "failed":
                failures = validation.get("failures") if isinstance(validation.get("failures"), list) else []
                reasons = ", ".join(
                    str(item.get("reason") or "")
                    for item in failures
                    if isinstance(item, dict) and str(item.get("reason") or "").strip()
                )
                return f"review_packet_incomplete: {reasons or 'packet validation failed'}"
        context_validation = cursor_review.get("review_context_validation")
        if isinstance(context_validation, dict) and context_validation.get("status") == "failed":
            return f"{review_context_incomplete_reason()}: reviewer did not cover the supervisor review packet"
        panel_decision = cursor_review.get("independent_reviewer_panel_decision")
        if isinstance(panel_decision, dict) and panel_decision.get("decision") != "accept":
            reason = str(panel_decision.get("reason") or "independent_reviewer_panel_not_accepted")
            classification = _cursor_review_failure_classification(cursor_review)
            should_use_panel_reason = bool(cursor_review.get("accepted")) or (
                reason == "missing_reviewer_verdict" and not classification
            )
            if not should_use_panel_reason:
                panel_decision = None
        if isinstance(panel_decision, dict) and panel_decision.get("decision") != "accept":
            reason = str(panel_decision.get("reason") or "independent_reviewer_panel_not_accepted")
            if reason == "adjudicated_strong_objection":
                adjudication = (
                    panel_decision.get("adjudication")
                    if isinstance(panel_decision.get("adjudication"), dict)
                    else {}
                )
                strongest = (
                    adjudication.get("strongest_objection")
                    if isinstance(adjudication.get("strongest_objection"), dict)
                    else {}
                )
                reviewer_id = str(strongest.get("reviewer_id") or "").strip()
                suffix = f": {reviewer_id}" if reviewer_id else ""
                return f"independent_reviewer_adjudicated_strong_objection{suffix}"
            if reason == "low_confidence_accept":
                reviewers = ", ".join(
                    str(item)
                    for item in panel_decision.get("low_confidence_reviewers", [])
                    if str(item).strip()
                )
                suffix = f": {reviewers}" if reviewers else ""
                return f"independent_reviewer_low_confidence_accept{suffix}"
            if reason == "calibrated_dependency_accept":
                calibrated = (
                    panel_decision.get("calibrated_accept")
                    if isinstance(panel_decision.get("calibrated_accept"), dict)
                    else {}
                )
                aggregate = calibrated.get("aggregate_confidence")
                threshold = calibrated.get("accept_confidence_threshold")
                return (
                    "independent_reviewer_calibrated_dependency_accept"
                    f": aggregate={aggregate} threshold={threshold}"
                )
            if reason == "missing_reviewer_verdict":
                reviewers = ", ".join(
                    str(item)
                    for item in panel_decision.get("missing_reviewers", [])
                    if str(item).strip()
                )
                suffix = f": {reviewers}" if reviewers else ""
                return f"independent_reviewer_missing_verdict{suffix}"
            if reason == "blocking_reviewer_objection":
                reviewers = ", ".join(
                    str(item)
                    for item in panel_decision.get("blocking_reviewers", [])
                    if str(item).strip()
                )
                suffix = f": {reviewers}" if reviewers else ""
                return f"independent_reviewer_blocking_objection{suffix}"
            if reason == "reviewer_non_accept":
                reviewers = ", ".join(
                    str(item)
                    for item in panel_decision.get("non_accepting_reviewers", [])
                    if str(item).strip()
                )
                suffix = f": {reviewers}" if reviewers else ""
                return f"independent_reviewer_non_accept{suffix}"
            return f"independent_reviewer_panel_{reason}"
    if cursor_review is not None and not cursor_review.get("accepted"):
        classification = _cursor_review_failure_classification(cursor_review)
        if classification == "reviewer_access_denied":
            return "cursor_reviewer_access_denied: reviewer_access_denied"
        if _cursor_review_recoverable_infrastructure_failure(cursor_review):
            return (
                "cursor_reviewer_infrastructure: "
                f"{classification}"
            )
        outcome = cursor_review.get("outcome") if isinstance(cursor_review.get("outcome"), dict) else {}
        outcome_objections = outcome.get("objections") if isinstance(outcome.get("objections"), list) else []
        reason = "; ".join(str(item) for item in outcome_objections if str(item).strip())
        if not reason:
            decisions = outcome.get("decisions") if isinstance(outcome.get("decisions"), list) else []
            reason = "; ".join(str(item) for item in decisions if str(item).strip())
        probe = cursor_review.get("probe") if isinstance(cursor_review.get("probe"), dict) else {}
        reason = reason or probe.get("reason") or "cursor reviewer did not accept"
        return f"cursor_review_failed: {reason}"
    if round_index >= max_rounds:
        return "max_rounds_per_gate exhausted without both agents accepting"
    return "agents have not both accepted yet; revise and continue"


def _probe_failure_objection(prefix: str, probe: dict[str, Any]) -> str:
    details = probe.get("details") if isinstance(probe.get("details"), dict) else {}
    parts = [prefix]
    reason = str(probe.get("reason") or "").strip()
    if reason:
        parts.append(reason)
    failures = details.get("failures") if isinstance(details.get("failures"), list) else []
    failure_text = ", ".join(str(item) for item in failures if str(item).strip())
    if failure_text:
        parts.append(f"failures={failure_text}")
    coverage = details.get("tdd_test_coverage") if isinstance(details.get("tdd_test_coverage"), dict) else {}
    missing = coverage.get("missing_nodeids") if isinstance(coverage.get("missing_nodeids"), list) else []
    if missing:
        preview = ", ".join(str(item) for item in missing[:10])
        suffix = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
        parts.append(f"missing_nodeids[{len(missing)}]={preview}{suffix}")
    unresolved = coverage.get("unresolved_names") if isinstance(coverage.get("unresolved_names"), list) else []
    if unresolved:
        preview = ", ".join(str(item) for item in unresolved[:10])
        suffix = f" (+{len(unresolved) - 10} more)" if len(unresolved) > 10 else ""
        parts.append(f"unresolved_tdd_names[{len(unresolved)}]={preview}{suffix}")
    skipped_without_reason = (
        coverage.get("skipped_without_reason_nodeids")
        if isinstance(coverage.get("skipped_without_reason_nodeids"), list)
        else []
    )
    if skipped_without_reason:
        preview = ", ".join(str(item) for item in skipped_without_reason[:10])
        suffix = f" (+{len(skipped_without_reason) - 10} more)" if len(skipped_without_reason) > 10 else ""
        parts.append(f"skipped_without_reason[{len(skipped_without_reason)}]={preview}{suffix}")
    return ": ".join(parts)


def _payload_blocked_for_lead_revision(payload: dict[str, Any]) -> bool:
    if payload.get("status") != "blocked":
        return False
    probes = payload.get("probes") if isinstance(payload.get("probes"), dict) else {}
    p4 = probes.get("P4") if isinstance(probes.get("P4"), dict) else {}
    return (
        p4.get("status") == "red"
        and p4.get("reason") == "outcome_critical_review_blocked"
    )


def _cursor_result_payload(result: CursorInvocationResult) -> dict[str, Any]:
    outcome_payload = result.outcome.model_dump() if result.outcome is not None else None
    return redact({
        "schema_version": "independent-reviewer-result/v1",
        "accepted": cursor_accepts(result),
        "probe": asdict(result.probe),
        "outcome": outcome_payload,
        "failure_classification": result.failure_classification,
        "recoverable": result.recoverable,
        "attempts": result.attempts,
        "retry_reasons": list(result.retry_reasons),
        "critical_review": critical_review_from_outcome(
            outcome_payload,
            decision="accept" if cursor_accepts(result) else "revise",
            would_change_if="Claude or Codex provides evidence resolving Cursor's objections.",
        ),
        "agent_id": result.agent_id,
        "run_id": result.run_id,
        "status": result.status,
        "model": result.model,
        "reviewer_runtime": result.reviewer_runtime,
        "reviewer_output_mode": result.reviewer_output_mode,
        "reviewer_assurance": result.reviewer_assurance,
        "fallback_from_runtime": result.fallback_from_runtime,
        "fallback_reason": result.fallback_reason,
        "diagnostics": result.diagnostics,
        "duration_ms": result.duration_ms,
        "transcript_tail": result.transcript[-4000:],
    })


def _independent_reviewer_results_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = payload.get("independent_reviewer_results")
    if isinstance(explicit, list):
        return [item for item in explicit if isinstance(item, dict)]
    cursor_review = payload.get("cursor_review") if isinstance(payload.get("cursor_review"), dict) else payload
    nested = cursor_review.get("independent_reviewer_results") if isinstance(cursor_review, dict) else None
    if isinstance(nested, list):
        return [item for item in nested if isinstance(item, dict)]
    if not isinstance(cursor_review, dict) or not cursor_review:
        return []
    outcome = cursor_review.get("outcome") if isinstance(cursor_review.get("outcome"), dict) else {}
    critical_review = (
        outcome.get("critical_review")
        if isinstance(outcome.get("critical_review"), dict)
        else cursor_review.get("critical_review")
        if isinstance(cursor_review.get("critical_review"), dict)
        else {}
    )
    transcript = str(cursor_review.get("transcript_tail") or "")
    output_json = json.dumps(outcome, sort_keys=True, default=str) if outcome else ""
    runtime = cursor_review.get("reviewer_runtime") or cursor_review.get("reviewer_output_mode")
    return [{
        "schema_version": "independent-reviewer-panel-result/v1",
        "reviewer_id": "independent-reviewer-0",
        "accepted": cursor_review.get("accepted"),
        "decision": _cursor_payload_decision(cursor_review),
        "severity": critical_review.get("severity") or ("none" if cursor_review.get("accepted") else "important"),
        "confidence": outcome.get("confidence"),
        "runtime": runtime,
        "reviewer_runtime": cursor_review.get("reviewer_runtime"),
        "reviewer_output_mode": cursor_review.get("reviewer_output_mode"),
        "model": cursor_review.get("model"),
        "provider_family": _reviewer_provider_family(runtime, cursor_review.get("model")),
        "lineage": [
            item for item in (
                _reviewer_provider_family(runtime, cursor_review.get("model")),
                runtime,
                cursor_review.get("model"),
            )
            if item
        ],
        "tool_access": "codebase_tools" if str(runtime or "").startswith("cursor") else "text_only",
        "assurance_grade": "agentic" if str(runtime or "").startswith("cursor") else "text_only",
        "reviewer_assurance": cursor_review.get("reviewer_assurance"),
        "transcript_refs": cursor_review.get("raw_transcript_refs") or [],
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "output_sha256": hashlib.sha256(output_json.encode("utf-8")).hexdigest() if output_json else None,
        "critical_review": critical_review,
        "failure_classification": cursor_review.get("failure_classification"),
        "recoverable": cursor_review.get("recoverable"),
        "attempts": cursor_review.get("attempts"),
    }]


def _cursor_payload_decision(cursor_review: dict[str, Any]) -> str:
    outcome = cursor_review.get("outcome") if isinstance(cursor_review.get("outcome"), dict) else {}
    decisions = outcome.get("decisions") if isinstance(outcome.get("decisions"), list) else []
    for decision in decisions:
        if decision in {"accept", "revise", "deny"}:
            return str(decision)
    return "accept" if cursor_review.get("accepted") else "revise"


def _reviewer_provider_family(runtime: Any, model: Any) -> str:
    runtime_text = str(runtime or "").lower()
    model_text = str(model or "").lower()
    if "codex" in runtime_text:
        return "openai"
    if "cursor" in runtime_text:
        return "cursor"
    if "gemini" in model_text:
        return "google"
    if "claude" in model_text:
        return "anthropic"
    if "gpt" in model_text:
        return "openai"
    if "litellm" in runtime_text:
        return "openai_compatible"
    return "unknown"


def _cursor_tool_call_fields(result: CursorInvocationResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "agent_id": result.agent_id,
        "run_id": result.run_id,
        "model": result.model,
        "reviewer_runtime": result.reviewer_runtime,
        "reviewer_output_mode": result.reviewer_output_mode,
        "reviewer_assurance": result.reviewer_assurance,
        "fallback_from_runtime": result.fallback_from_runtime,
        "fallback_reason": result.fallback_reason,
        "diagnostics": result.diagnostics,
        "cursor_duration_ms": result.duration_ms,
        "failure_classification": result.failure_classification,
        "recoverable": result.recoverable,
        "attempts": result.attempts,
        "retry_reasons": list(result.retry_reasons),
        "result_summary": {
            "accepted": cursor_accepts(result),
            "probe_status": result.probe.status,
            "probe_reason": result.probe.reason,
            "outcome_present": result.outcome is not None,
            "failure_classification": result.failure_classification,
            "recoverable": result.recoverable,
            "reviewer_runtime": result.reviewer_runtime,
            "reviewer_output_mode": result.reviewer_output_mode,
            "reviewer_assurance": result.reviewer_assurance,
            "fallback_from_runtime": result.fallback_from_runtime,
            "fallback_reason": result.fallback_reason,
        },
    }


def _cursor_review_failure_classification(cursor_review: dict[str, Any] | None) -> str:
    if not isinstance(cursor_review, dict):
        return ""
    classification = str(cursor_review.get("failure_classification") or "").strip()
    if classification:
        return classification
    probe = cursor_review.get("probe") if isinstance(cursor_review.get("probe"), dict) else {}
    reason = str(probe.get("reason") or "").strip()
    if reason in {
        "reviewer_contract_unmet",
        "reviewer_infrastructure_unavailable",
        "reviewer_access_denied",
    }:
        return reason
    return ""


def _cursor_review_recoverable_infrastructure_failure(
    cursor_review: dict[str, Any] | None,
) -> bool:
    classification = _cursor_review_failure_classification(cursor_review)
    return classification in {
        "reviewer_contract_unmet",
        "reviewer_infrastructure_unavailable",
    }


def _panel_recoverable_infrastructure_failures(
    reviewer_results: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for result in reviewer_results:
        if not isinstance(result, dict):
            continue
        classification = str(result.get("failure_classification") or "").strip()
        if classification not in {
            "reviewer_contract_unmet",
            "reviewer_infrastructure_unavailable",
        }:
            continue
        if not bool(result.get("recoverable")):
            continue
        failures.append({
            "reviewer_id": result.get("reviewer_id"),
            "failure_classification": classification,
            "recoverable": True,
            "runtime": result.get("runtime") or result.get("reviewer_runtime"),
            "model": result.get("model"),
        })
    return failures


def _panel_failure_classification(
    recoverable_failures: list[dict[str, Any]],
    cursor_review: dict[str, Any] | None,
) -> str:
    legacy_classification = _cursor_review_failure_classification(cursor_review)
    if legacy_classification:
        return legacy_classification
    classifications = {
        str(item.get("failure_classification") or "")
        for item in recoverable_failures
        if str(item.get("failure_classification") or "").strip()
    }
    if "reviewer_infrastructure_unavailable" in classifications:
        return "reviewer_infrastructure_unavailable"
    if "reviewer_contract_unmet" in classifications:
        return "reviewer_contract_unmet"
    return ""


def _panel_available_reviewers_accept(
    reviewer_results: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> bool:
    for result in reviewer_results:
        if not isinstance(result, dict):
            continue
        decision = str(result.get("decision") or "").strip().lower()
        verdict_present = bool(result.get("verdict_present", decision in {"accept", "revise", "deny"}))
        if not verdict_present:
            continue
        if decision != "accept":
            return False
    return True


def _normalise_receipt_payloads(
    receipts: list[dict[str, Any]],
    *,
    trusted_runtime_receipt_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for receipt in receipts:
        if isinstance(receipt, dict):
            normalised.append(
                sanitize_receipt_provenance(
                    receipt,
                    trusted_runtime_receipt_ids=trusted_runtime_receipt_ids,
                )
            )
    return normalised


def _trusted_runtime_receipt_ids(receipts: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for receipt in receipts:
        receipt_id = str(receipt.get("receipt_id") or receipt.get("id") or "").strip()
        if receipt_id:
            ids.add(receipt_id)
    return ids


def _merge_runtime_receipt_payloads(
    existing_receipts: list[dict[str, Any]],
    runtime_receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    runtime_keys = {_receipt_identity(receipt) for receipt in runtime_receipts}
    return _dedupe_receipt_payloads([
        *[
            receipt for receipt in existing_receipts
            if _receipt_identity(receipt) not in runtime_keys
        ],
        *runtime_receipts,
    ])


def _hydrate_agentic_worker_receipts(
    *,
    state: State,
    cwd: str,
    run_id: str,
    task_id: str,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for row in state.read_dual_agent_gate_events(run_id):
        if row["kind"] != "dual_agent_agentic_worker_production":
            continue
        payload = json.loads(row["payload_json"] or "{}")
        if str(payload.get("task_id") or "") != task_id:
            continue
        for receipt in payload.get("receipts") or []:
            if isinstance(receipt, dict):
                receipts.append({
                    **receipt,
                    "hydrated_from": "ledger_event",
                    "hydrated_event_id": int(row["event_id"]),
                })
    for receipt in discover_agentic_worker_receipts(cwd=cwd, task_id=task_id):
        receipts.append({
            **receipt,
            "hydrated_from": receipt.get("hydrated_from") or "handoff_worker_dir",
        })
    return _dedupe_receipt_payloads(receipts)


def _dedupe_receipt_payloads(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for receipt in receipts:
        key = _receipt_identity(receipt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(receipt)
    return deduped


def _receipt_identity(receipt: dict[str, Any]) -> str:
    for field in ("receipt_id", "id"):
        value = str(receipt.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    worker = str(receipt.get("worker_id") or receipt.get("agent_id") or "").strip()
    role = str(receipt.get("role") or receipt.get("persona_id") or "").strip()
    task = str(receipt.get("task_id") or "").strip()
    if worker or role or task:
        return f"agentic:{task}:{worker}:{role}:{receipt.get('status') or ''}"
    return json.dumps(receipt, sort_keys=True, default=str)


def _reviewer_unavailable_policy_config(
    cfg: Config,
    *,
    reviewer_unavailable_policy: str | None,
) -> str:
    configured = getattr(cfg.supervisor, "reviewer_unavailable_policy", "escalate")
    requested = configured if reviewer_unavailable_policy is None else reviewer_unavailable_policy
    return _canonical_reviewer_unavailable_policy(requested)


def _canonical_reviewer_unavailable_policy(value: str | None) -> str:
    text = str(value or "escalate").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in REVIEWER_UNAVAILABLE_POLICIES else "escalate"


def _reviewer_model_config(
    cfg: Config,
    *,
    reviewer_model: str | None,
    cursor_model: str | None,
    reviewer_output_mode: str,
) -> str:
    if reviewer_output_mode == "cursor_sdk":
        requested = reviewer_model or cursor_model or DEFAULT_CURSOR_SDK_MODEL
        return str(requested)
    requested = reviewer_model or getattr(cfg.supervisor, "reviewer_model", "") or cursor_model
    return str(requested or DEFAULT_STRUCTURED_REVIEWER_MODEL)


def _reviewer_output_mode_config(cfg: Config, *, reviewer_output_mode: str | None) -> str:
    requested = reviewer_output_mode or getattr(cfg.supervisor, "reviewer_output_mode", "")
    text = str(requested or "cursor_sdk").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in {"litellm_structured", "cursor_sdk"} else "cursor_sdk"


def _reviewer_max_tokens_config(cfg: Config, *, reviewer_max_tokens: int | None) -> int:
    requested = reviewer_max_tokens
    if requested is None:
        requested = int(getattr(cfg.supervisor, "reviewer_max_tokens", 4096) or 4096)
    return max(1, int(requested))


def _reviewer_infra_retry_limit_config(
    cfg: Config,
    *,
    reviewer_infra_retry_limit: int | None,
) -> int:
    requested = reviewer_infra_retry_limit
    if requested is None:
        requested = int(getattr(cfg.supervisor, "reviewer_infra_retry_limit", 2) or 0)
    return max(0, int(requested))


def _reviewer_infra_retry_backoff_s_config(
    cfg: Config,
    *,
    reviewer_infra_retry_backoff_s: float | None,
) -> float:
    requested = reviewer_infra_retry_backoff_s
    if requested is None:
        requested = float(getattr(cfg.supervisor, "reviewer_infra_retry_backoff_s", 1.0) or 0.0)
    return max(0.0, float(requested))


def _reviewer_low_confidence_threshold_config(
    cfg: Config,
    *,
    reviewer_low_confidence_threshold: float | None,
) -> float:
    requested = reviewer_low_confidence_threshold
    if requested is None:
        requested = float(getattr(cfg.supervisor, "reviewer_low_confidence_threshold", 0.0) or 0.0)
    return max(0.0, min(1.0, float(requested)))


def _reviewer_panel_calibration_path_config(
    cfg: Config,
    *,
    cwd: str | Path,
    reviewer_panel_calibration_path: str | None,
) -> str | None:
    requested = reviewer_panel_calibration_path
    if requested is None:
        requested = str(getattr(cfg.supervisor, "reviewer_panel_calibration_path", "") or "")
    text = str(requested or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd).expanduser() / candidate
    return str(candidate)


def _artifact_planning_refs(
    artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    cwd: str | Path,
) -> list[PlanningRef]:
    root = Path(cwd).expanduser()
    refs: list[PlanningRef] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        path_text = str(artifact.get("path") or "").strip()
        if not path_text:
            continue
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = root / path
        sha = str(artifact.get("sha256") or "").strip()
        if not sha and path.exists() and path.is_file():
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
        refs.append(
            PlanningRef(
                kind=str(artifact.get("kind") or artifact.get("artifact_kind") or "artifact"),
                path=path_text,
                sha256=sha,
            )
        )
    return refs


def _tdd_names_from_artifacts(
    artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    cwd: str | Path,
) -> list[str]:
    root = Path(cwd).expanduser()
    names: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        kind = str(artifact.get("kind") or artifact.get("artifact_kind") or "").lower()
        path_text = str(artifact.get("path") or "").strip()
        if "tdd" not in kind and not path_text.endswith("tdd.md"):
            continue
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = root / path
        if not path.exists() or not path.is_file():
            continue
        for name in _tdd_test_names(path.read_text(encoding="utf-8", errors="replace")):
            if name not in names:
                names.append(name)
    return names


def _changed_files_from_runtime_receipts(
    receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    cwd: str | Path,
    trusted_runtime_receipt_ids: set[str] | None = None,
) -> list[ChangedFile]:
    root = Path(cwd).expanduser()
    trusted = set(trusted_runtime_receipt_ids or set())
    declared_paths: set[str] = set()
    for receipt in receipts:
        if not _is_review_packet_runtime_diff_receipt(
            receipt,
            trusted_runtime_receipt_ids=trusted,
        ):
            continue
        for path_text in receipt.get("declared_changed_files") or []:
            text = str(path_text or "").strip()
            if text:
                declared_paths.add(text)

    by_path: dict[str, ChangedFile] = {}
    for receipt in receipts:
        if not _is_review_packet_runtime_diff_receipt(
            receipt,
            trusted_runtime_receipt_ids=trusted,
        ):
            continue
        name_status = receipt.get("name_status")
        if isinstance(name_status, list):
            for entry in name_status:
                if not isinstance(entry, dict):
                    continue
                path_text = str(entry.get("path") or "").strip()
                if not _is_review_packet_changed_file_path(
                    path_text,
                    declared_paths=declared_paths,
                ):
                    continue
                by_path[path_text] = ChangedFile(
                    path=path_text,
                    status=str(entry.get("status") or "changed"),
                    sha256=_sha256_relative_file(root, path_text),
                )
            continue
        for path_text in receipt.get("actual_changed_files") or receipt.get("changed_files") or []:
            text = str(path_text or "").strip()
            if (
                text
                and text not in by_path
                and _is_review_packet_changed_file_path(
                    text,
                    declared_paths=declared_paths,
                )
            ):
                by_path[text] = ChangedFile(
                    path=text,
                    status="changed",
                    sha256=_sha256_relative_file(root, text),
                )
    return [by_path[key] for key in sorted(by_path)]


def _is_review_packet_runtime_diff_receipt(
    receipt: Any,
    *,
    trusted_runtime_receipt_ids: set[str],
) -> bool:
    if not isinstance(receipt, dict) or receipt.get("kind") != "git_diff":
        return False
    receipt_id = str(receipt.get("receipt_id") or receipt.get("id") or "").strip()
    if trusted_runtime_receipt_ids:
        return receipt_id in trusted_runtime_receipt_ids
    return (
        receipt.get("_supervisor_runtime_evidence") is True
        and receipt.get("source") == "supervisor"
        and receipt.get("supervisor_runtime_origin") == "collect_runtime_evidence"
    )


def _is_review_packet_changed_file_path(
    path_text: str,
    *,
    declared_paths: set[str],
) -> bool:
    path = str(path_text or "").strip()
    if not path or _is_benign_review_packet_path(path):
        return False
    if path.startswith("docs/"):
        return path in declared_paths
    return True


def _is_benign_review_packet_path(path_text: str) -> bool:
    path = str(path_text or "").strip()
    if not path:
        return True
    if path.startswith((".scratch/", ".handoff/", "reviews/", ".cortex/runtime_workspaces/")):
        return True
    if path == "state.db" or path.endswith((".db-shm", ".db-wal")):
        return True
    if path.startswith("docs/dual-agent/"):
        return True
    if path.startswith("docs/supervisor-") and path.endswith(".md"):
        return True
    return False


def _sha256_relative_file(root: Path, relative: str) -> str | None:
    path = root / relative
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_receipt_refs(
    receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    trusted_runtime_receipt_ids: set[str],
) -> list[ReceiptRef]:
    refs: list[ReceiptRef] = []
    trusted = set(trusted_runtime_receipt_ids)
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        receipt_id = str(receipt.get("receipt_id") or receipt.get("id") or "").strip()
        if not receipt_id or receipt_id not in trusted:
            continue
        refs.append(ReceiptRef(receipt_id=receipt_id, kind=str(receipt.get("kind") or "receipt")))
    return refs


def _git_head(cwd: str | Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(Path(cwd).expanduser()),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _git_branch(cwd: str | Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(Path(cwd).expanduser()),
        capture_output=True,
        text=True,
        check=False,
    )
    branch = completed.stdout.strip()
    if completed.returncode != 0 or not branch:
        return None
    return branch


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _review_worker_terminal_status(result: CursorInvocationResult) -> str:
    if str(result.status or "").strip().lower() == "cancelled":
        return "cancelled"
    if not result.probe.ok:
        return "failed"
    if not cursor_accepts(result):
        return "blocked"
    return "completed"


def _review_worker_event_kind(status: str) -> str:
    if status == "completed":
        return WORKER_COMPLETED
    if status == "failed":
        return WORKER_FAILED
    if status == "blocked":
        return WORKER_BLOCKED
    if status == "cancelled":
        return WORKER_CANCELLED
    raise ValueError(f"unknown review worker status: {status!r}")


def _review_worker_decision(result: CursorInvocationResult) -> str:
    if result.outcome is not None:
        for decision in result.outcome.decisions:
            if decision in {"accept", "revise", "deny"}:
                return decision
    return "accept" if cursor_accepts(result) else "revise"


def _review_worker_terminal_reason(
    result: CursorInvocationResult,
    status: str,
) -> str | None:
    if status == "completed":
        return None
    if status == "blocked":
        return "reviewer_non_accept"
    if status == "cancelled":
        return result.probe.reason or "reviewer_cancelled"
    return str(result.failure_classification or result.probe.reason or "reviewer_failed")


def _build_supervisor_review_packet(
    *,
    task_id: str,
    run_id: str,
    gate: str,
    round_index: int,
    cwd: str | Path,
    gate_artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    runtime_baseline: dict[str, Any] | None,
    receipt_payloads: list[dict[str, Any]],
    trusted_runtime_receipt_ids: set[str],
    reviewer_ids: list[str],
    policy_overlay_hash: str = "",
    lesson_hashes: list[str] | None = None,
):
    runtime_refs = _runtime_receipt_refs(
        receipt_payloads,
        trusted_runtime_receipt_ids=trusted_runtime_receipt_ids,
    )
    changed_files = _changed_files_from_runtime_receipts(
        receipt_payloads,
        cwd=cwd,
        trusted_runtime_receipt_ids=trusted_runtime_receipt_ids,
    )
    tdd_names = _tdd_names_from_artifacts(gate_artifacts, cwd=cwd)
    diff_refs = [
        ref.receipt_id
        for ref in runtime_refs
        if ref.kind == "git_diff"
    ]
    packet = build_review_packet(
        task_id=task_id,
        run_id=run_id,
        gate=gate,
        packet_id=f"review-packet-{gate}-{round_index}",
        base_head=str((runtime_baseline or {}).get("head") or _git_head(cwd) or ""),
        candidate_head=_git_head(cwd),
        patch_hash=hashlib.sha256(
            json.dumps(
                [cf.__dict__ for cf in changed_files],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest() if changed_files else None,
        planning_refs=_artifact_planning_refs(gate_artifacts, cwd=cwd),
        acceptance_items=tdd_names,
        diff_refs=diff_refs,
        name_status_refs=[f"{ref}:name_status" for ref in diff_refs],
        changed_files=changed_files,
        runtime_receipt_ids=runtime_refs,
        declared_tests=tdd_names,
        executed_test_receipt_ids=[
            ref for ref in runtime_refs if ref.kind in {"test", "tdd_test_coverage"}
        ],
        policy_overlay_hash=policy_overlay_hash,
        lesson_hashes=lesson_hashes or [],
        reviewer_ids=reviewer_ids,
    )
    failures = validate_review_packet(
        packet,
        expected_changed_files=[cf.path for cf in changed_files],
        expected_acceptance_items=tdd_names,
        expected_declared_tests=tdd_names,
        supervisor_runtime_receipt_ids=trusted_runtime_receipt_ids,
    )
    payload = {
        "schema_version": "supervisor-review-packet/v1",
        **packet.to_event_payload(),
        "validation": {
            "status": "passed" if not failures else "failed",
            "failures": [
                {"reason": failure.reason, "detail": failure.detail}
                for failure in failures
            ],
        },
    }
    return packet, payload, failures


def _reviewer_roster_preflight(
    *,
    task_id: str,
    run_id: str,
    gate: str,
    reviewers: list[Any],
    cfg: Config,
    cursor_runner: CursorRunner,
    codex_runner: Runner,
) -> RosterPreflight:
    entries: list[RosterEntry] = []
    for reviewer in reviewers:
        spec = reviewer.spec
        available, boot_status, failure_reason = _reviewer_boot_status(
            reviewer=reviewer,
            cfg=cfg,
            cursor_runner=cursor_runner,
            codex_runner=codex_runner,
        )
        entries.append(
            RosterEntry(
                worker_id=spec.reviewer_id,
                purpose="reviewer",
                provider_family=spec.provider_family,
                runtime=spec.runtime,
                model=spec.model or "",
                available=available,
                boot_status=boot_status,
                failure_reason=failure_reason,
            )
        )
    return RosterPreflight(
        task_id=task_id,
        run_id=run_id,
        gate=gate,
        entries=entries,
    )


def _reviewer_boot_status(
    *,
    reviewer: Any,
    cfg: Config,
    cursor_runner: CursorRunner,
    codex_runner: Runner,
) -> tuple[bool, str, str | None]:
    spec = reviewer.spec
    runtime = str(spec.runtime or "").lower()
    if runtime == "codex_cli":
        command = str(getattr(reviewer, "command", "") or "codex")
        if _uses_real_subprocess_runner(codex_runner):
            if shutil.which(command) is None:
                return False, "command_not_found", f"{command} command not found"
            return True, "command_available", None
        return True, "configured_test_runner", None

    if runtime == "cursor_sdk":
        if cursor_runner is invoke_cursor_agent:
            if importlib.util.find_spec("cursor_sdk") is None:
                return False, "module_not_found", "cursor_sdk module not importable"
            return True, "sdk_importable", None
        return True, "configured_test_runner", None

    if runtime == "litellm_structured":
        if cursor_runner is invoke_cursor_agent:
            if importlib.util.find_spec("openai") is None:
                return False, "module_not_found", "openai module not importable"
            api_key = cfg.models.openai_api_key or os.environ.get("OPENAI_API_KEY")
            if not api_key:
                return False, "missing_api_key", "OPENAI_API_KEY unavailable"
            return True, "api_configured", None
        return True, "configured_test_runner", None

    return True, "configured", None


def _uses_real_subprocess_runner(runner: Runner) -> bool:
    return (
        getattr(runner, "__module__", "") == "subprocess"
        and getattr(runner, "__name__", "") == "run"
    )


def _no_mistakes_config(
    cfg: Config,
    *,
    policy: str | None,
    skip_steps: list[str] | None,
    timeout_s: int | None,
) -> NoMistakesConfig:
    configured = cfg.no_mistakes
    requested_policy = (
        configured.policy if policy is None else str(policy)
    )
    normalized_policy = _canonical_no_mistakes_policy(requested_policy)
    requested_skip_steps = (
        list(configured.skip_steps)
        if skip_steps is None
        else [str(step) for step in skip_steps]
    )
    return NoMistakesConfig(
        policy=normalized_policy,  # type: ignore[arg-type]
        binary=str(configured.binary or "no-mistakes"),
        skip_steps=tuple(
            step.strip().lower()
            for step in requested_skip_steps
            if str(step).strip()
        ),
        auto_yes=bool(configured.auto_yes),
        timeout_s=max(1, int(timeout_s if timeout_s is not None else configured.timeout_s)),
        require_clean_committed_branch=bool(configured.require_clean_committed_branch),
        allow_shipping_steps=bool(configured.allow_shipping_steps),
    )


def _canonical_no_mistakes_policy(value: str | None) -> str:
    text = str(value or "off").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in NO_MISTAKES_POLICIES else "off"


def _no_mistakes_blocks_workflow(result: NoMistakesValidationResult) -> bool:
    if result.verdict == "changed_requires_rerun":
        return True
    if result.policy in {"required", "shipping"} and result.verdict != "accepted":
        return True
    return False


def _no_mistakes_command_skip_steps(command: list[str]) -> list[str]:
    for arg in command:
        if arg.startswith("--skip="):
            return [
                step.strip()
                for step in arg.split("=", 1)[1].split(",")
                if step.strip()
            ]
    return []


def _reviewer_unavailable_recovery_plan(
    *,
    state: State,
    run_id: str,
    task_id: str,
    gate: str,
    policy: str,
    classification: str,
    available_reviewers_accept: bool,
    agentic_policy: dict[str, Any],
    required_evidence_grade: str,
    user_facing: bool,
) -> dict[str, Any]:
    canonical_policy = _canonical_reviewer_unavailable_policy(policy)
    safety_reasons: list[str] = []
    if str(agentic_policy.get("agentic_lead_policy") or "off") == "required":
        safety_reasons.append("agentic_lead_policy_required")
    if _canonical_evidence_grade(required_evidence_grade) == "runtime_native":
        safety_reasons.append("required_evidence_grade_runtime_native")
    if user_facing:
        safety_reasons.append("user_facing_or_visual_evidence_required")

    authorization = None
    if canonical_policy == "escalate" or (canonical_policy != "block" and safety_reasons):
        authorization = state.claim_resume_signal(run_id=run_id, task_id=task_id)

    forced_by_safety = bool(safety_reasons)
    if not available_reviewers_accept:
        decision = "block"
        reason = "available_reviewers_not_accepted"
    elif canonical_policy == "block":
        decision = "block"
        reason = "policy_block"
    elif authorization is not None:
        decision = "proceed_degraded"
        reason = "human_authorized_proceed_degraded"
    elif forced_by_safety:
        decision = "escalate"
        reason = "safety_escalation_required"
    elif canonical_policy == "proceed_degraded":
        decision = "proceed_degraded"
        reason = "policy_proceed_degraded"
    else:
        decision = "escalate"
        reason = "policy_escalate"

    return {
        "schema_version": "reviewer-unavailable-recovery/v1",
        "decision": decision,
        "reason": reason,
        "policy": canonical_policy,
        "classification": classification,
        "evidence_grade": "degraded",
        "reviewer_verdict_counted_as_accept": False,
        "available_reviewers_accept": bool(available_reviewers_accept),
        "forced_by_safety": forced_by_safety,
        "safety_reasons": safety_reasons,
        "authorization": authorization,
        "gate": gate,
    }


def _agentic_lead_policy_config(
    cfg: Config,
    *,
    agentic_lead_policy: str | None,
    min_subagents: int | None,
    required_roles: list[str] | None,
    solo_exception_for_artifact_only_gates: bool | None,
    required_evidence_grade: str | None,
) -> dict[str, Any]:
    configured = cfg.agentic_lead
    return {
        "agentic_lead_policy": _canonical_agentic_lead_policy(
            agentic_lead_policy if agentic_lead_policy is not None else configured.policy
        ),
        "min_subagents": max(
            0,
            int(min_subagents if min_subagents is not None else configured.min_subagents),
        ),
        "required_roles": [
            _normalise_agentic_role(role)
            for role in (
                required_roles if required_roles is not None else configured.required_roles
            )
            if _normalise_agentic_role(role)
        ],
        "solo_exception_for_artifact_only_gates": bool(
            configured.solo_exception_for_artifact_only_gates
            if solo_exception_for_artifact_only_gates is None
            else solo_exception_for_artifact_only_gates
        ),
        "required_evidence_grade": _canonical_evidence_grade(
            required_evidence_grade
            if required_evidence_grade is not None
            else configured.required_evidence_grade
        ),
    }


def _workflow_agentic_policy_route(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": policy["agentic_lead_policy"],
        "min_subagents": policy["min_subagents"],
        "required_roles": list(policy["required_roles"]),
        "solo_exception_for_artifact_only_gates": policy["solo_exception_for_artifact_only_gates"],
        "required_evidence_grade": policy["required_evidence_grade"],
    }


def _planning_rubric_threshold_for_gate(
    cfg: Config,
    rubric_thresholds: Mapping[str, Any] | None,
    *,
    gate: str,
) -> float:
    default = float(cfg.planning_rubric.threshold)
    thresholds = rubric_thresholds or {}
    if not isinstance(thresholds, Mapping):
        return default
    for key in (gate, "all", "*", "default"):
        if key in thresholds:
            try:
                value = float(thresholds[key])
            except (TypeError, ValueError):
                return default
            return _clamp_planning_rubric_threshold(value)
    return default


def _clamp_planning_rubric_threshold(value: Any) -> float:
    return max(PLANNING_RUBRIC_MIN_THRESHOLD, min(1.0, float(value)))


def _requires_p13_receipt_validation(
    execution_layer_mode: str | None,
    agentic_lead_policy: str | None,
) -> bool:
    return (
        _is_dynamic_workflow_preview(execution_layer_mode)
        or _is_agentic_lead_policy_active(agentic_lead_policy)
    )


def _is_agentic_lead_policy_active(value: str | None) -> bool:
    return _canonical_agentic_lead_policy(value) in {"allowed", "required"}


def _canonical_agentic_lead_policy(value: str | None) -> str:
    text = str(value or "off").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in {"off", "allowed", "required"} else "off"


def _canonical_evidence_grade(value: str | None) -> str:
    text = str(value or "self_reported").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in {"self_reported", "lead_captured", "runtime_native"} else "self_reported"


def _normalise_agentic_role(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_dynamic_workflow_preview(value: str | None) -> bool:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_") == "dynamic_workflow_preview"


def _canonical_execution_layer_mode(value: str | None) -> str:
    return "dynamic_workflow_preview" if _is_dynamic_workflow_preview(value) else str(value or "lead_direct")


def _canonical_dynamic_workflow_task_class(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text.lower().replace("-", "_").replace(" ", "_")


def _receipt_ids(receipts: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for receipt in receipts:
        receipt_id = receipt.get("receipt_id") or receipt.get("id") or receipt.get("kind")
        if receipt_id:
            ids.append(str(receipt_id))
    return ids


def _interaction_addresses(payload: dict[str, Any]) -> tuple[str, ...]:
    addresses: list[str] = []
    handoff = payload.get("handoff_packet_path")
    if handoff:
        addresses.append(f"handoff:{handoff}")
    result_event_id = payload.get("event_id")
    if result_event_id:
        addresses.append(f"event:{result_event_id}")
    return tuple(addresses)


def _outcome_claims(payload: dict[str, Any]) -> tuple[str, ...]:
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    claims = [
        str(item)
        for item in outcome.get("claims") or []
        if str(item).strip()
    ]
    decisions = [
        f"decision:{item}"
        for item in outcome.get("decisions") or []
        if str(item).strip()
    ]
    return tuple([*claims, *decisions])


def _outcome_objections(payload: dict[str, Any]) -> tuple[str, ...]:
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    return tuple(
        str(item)
        for item in outcome.get("objections") or []
        if str(item).strip()
    )


def _receipt_evidence_refs(
    receipts: list[dict[str, Any]],
    screenshots: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for receipt in receipts:
        receipt_id = receipt.get("receipt_id") or receipt.get("id")
        if not receipt_id:
            continue
        refs.append({
            "kind": str(receipt.get("kind") or receipt.get("type") or "receipt"),
            "ref": f"receipt:{receipt_id}",
            "status": str(receipt.get("status") or receipt.get("result") or ""),
        })
    for index, screenshot in enumerate(screenshots, start=1):
        path = screenshot.get("path") if isinstance(screenshot, dict) else None
        if not path:
            continue
        refs.append({
            "kind": "screenshot",
            "ref": str(path),
            "status": str(
                screenshot.get("validation_status")
                or (
                    screenshot.get("validation", {}).get("status")
                    if isinstance(screenshot.get("validation"), dict)
                    else ""
                )
                or ""
            ),
            "label": str(screenshot.get("label") or f"screenshot-{index}"),
        })
    return tuple(refs)


def _raw_transcript_refs(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    handoff = payload.get("handoff_packet_path")
    if handoff:
        refs.append({
            "kind": "claude_handoff_packet",
            "ref": str(handoff),
        })
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    tests = outcome.get("tests") or []
    if tests:
        refs.append({
            "kind": "claude_reported_tests",
            "ref": "outcome.tests",
            "count": len(tests),
        })
    return tuple(refs)


def _workflow_step_dict(gate: str, status: str, attempts: int) -> dict[str, Any]:
    return {
        "gate": gate,
        "status": status,
        "attempt_count": attempts,
    }


def _workflow_resume_state(
    state: State,
    *,
    run_id: str,
    task_id: str,
    route_gates: tuple[str, ...],
) -> dict[str, Any]:
    rows = state.list_dual_agent_workflow_steps(run_id=run_id, task_id=task_id)
    by_gate = {str(row["gate"]): row for row in rows}
    skipped_steps: list[dict[str, Any]] = []
    skipped_gates: list[str] = []
    pending_gates: list[str] = []
    found_pending = False
    for gate in route_gates:
        row = by_gate.get(gate)
        if not found_pending and row is not None and str(row["status"]) == "accepted":
            skipped_gates.append(gate)
            skipped_steps.append({
                "gate": gate,
                "status": "accepted",
                "attempt_count": int(row["attempt_count"]),
                "latest_event_id": row["latest_event_id"],
            })
            continue
        found_pending = True
        pending_gates.append(gate)
    return {
        "schema_version": "dual-agent-workflow-resume/v1",
        "mode": "resume_from_prior_steps" if skipped_gates else "fresh",
        "skipped_gates": skipped_gates,
        "skipped_steps": skipped_steps,
        "pending_gates": pending_gates,
    }


def _outcome_confidence(payload: dict[str, Any]) -> float:
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    try:
        value = float(outcome.get("confidence"))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _merge_planning_artifacts(
    base: list[dict[str, Any]],
    extra: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in [*base, *extra]:
        key = (str(item.get("kind") or ""), str(item.get("path") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _artifact_preflight(
    *,
    state: State,
    run_id: str,
    task_id: str,
    gate: str,
    planning_artifacts: list[dict[str, Any]],
    artifact_policy: str,
    user_facing: bool,
    screenshots: list[dict[str, Any]],
    required_artifacts: tuple[str, ...] | list[str] | None = None,
    required_prerequisite_gates: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    policy = str(artifact_policy or "strict").strip().lower()
    required = (
        [str(item) for item in required_artifacts]
        if required_artifacts is not None
        else list(STRICT_ARTIFACT_REQUIREMENTS.get(gate, ()))
    )
    required_prerequisites = (
        [str(item) for item in required_prerequisite_gates]
        if required_prerequisite_gates is not None
        else list(GATE_PREREQUISITES.get(gate, ()))
    )
    present, missing_paths = _planning_artifact_roles(planning_artifacts)
    screenshot_paths, missing_screenshot_paths = _valid_screenshot_paths(screenshots)
    visual_validation = _visual_validation_evidence(screenshots, required=user_facing)
    gate_statuses = _latest_gate_statuses(state, run_id=run_id, task_id=task_id)
    accepted_prerequisites = [
        prereq for prereq in required_prerequisites
        if gate_statuses.get(prereq) == "accepted"
    ]
    missing_prerequisites = [
        prereq for prereq in required_prerequisites
        if gate_statuses.get(prereq) != "accepted"
    ]

    if policy != "strict":
        if policy not in RELAXED_ARTIFACT_POLICIES:
            return {
                "status": "blocked",
                "reason": "unsupported_artifact_policy",
                "gate": gate,
                "artifact_policy": artifact_policy,
                "required_artifacts": required,
                "present_artifacts": sorted(present),
                "missing_artifacts": [],
                "missing_artifact_paths": missing_paths,
                "required_prerequisite_gates": required_prerequisites,
                "accepted_prerequisite_gates": accepted_prerequisites,
                "missing_prerequisite_gates": missing_prerequisites,
                "gate_statuses": gate_statuses,
                "user_facing": user_facing,
                "screenshots": screenshot_paths,
                "missing_screenshot_paths": missing_screenshot_paths,
                "visual_validation": visual_validation,
            }
        return {
            "status": "ok",
            "reason": "artifact_policy_relaxed",
            "gate": gate,
            "artifact_policy": policy,
            "required_artifacts": required,
            "present_artifacts": sorted(present),
            "missing_artifacts": [role for role in required if role not in present],
            "missing_artifact_paths": missing_paths,
            "required_prerequisite_gates": required_prerequisites,
            "accepted_prerequisite_gates": accepted_prerequisites,
            "missing_prerequisite_gates": missing_prerequisites,
            "gate_statuses": gate_statuses,
            "user_facing": user_facing,
            "screenshots": screenshot_paths,
            "missing_screenshot_paths": missing_screenshot_paths,
            "visual_validation": visual_validation,
        }

    missing = [role for role in required if role not in present]
    if user_facing and not screenshot_paths:
        missing.append("screenshots")
    elif missing_screenshot_paths:
        missing.append("screenshots")
    if user_facing and screenshot_paths and visual_validation["status"] != "ok":
        missing.append("visual_validation")

    blocked = bool(missing or missing_prerequisites)
    if missing_prerequisites:
        reason = "gate_prerequisites_missing"
    elif missing:
        reason = "required_artifacts_missing"
    else:
        reason = "required_artifacts_present"

    return {
        "status": "blocked" if blocked else "ok",
        "reason": reason,
        "gate": gate,
        "artifact_policy": policy,
        "required_artifacts": required,
        "present_artifacts": sorted(present),
        "missing_artifacts": missing,
        "missing_artifact_paths": missing_paths,
        "required_prerequisite_gates": required_prerequisites,
        "accepted_prerequisite_gates": accepted_prerequisites,
        "missing_prerequisite_gates": missing_prerequisites,
        "gate_statuses": gate_statuses,
        "user_facing": user_facing,
        "screenshots": screenshot_paths,
        "missing_screenshot_paths": missing_screenshot_paths,
        "visual_validation": visual_validation,
    }


def _latest_gate_statuses(
    state: State,
    *,
    run_id: str,
    task_id: str,
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for row in state.read_dual_agent_gate_events(run_id):
        if row["kind"] != "dual_agent_gate_result":
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or str(payload.get("task_id") or "") != task_id:
            continue
        gate = str(payload.get("gate") or "")
        status = str(payload.get("status") or "")
        if gate and status:
            statuses[gate] = status
    return statuses


def _planning_artifact_roles(
    planning_artifacts: list[dict[str, Any]],
) -> tuple[set[str], list[str]]:
    roles: set[str] = set()
    missing_paths: list[str] = []
    for artifact in planning_artifacts:
        path_value = artifact.get("path")
        role = _planning_artifact_role(artifact)
        if role is None:
            continue
        if not path_value:
            missing_paths.append(f"{role}:<missing-path>")
            continue
        path = Path(str(path_value)).expanduser()
        if not path.exists() or not path.is_file():
            missing_paths.append(str(path))
            continue
        roles.add(role)
    return roles, missing_paths


def _planning_artifact_role(artifact: dict[str, Any]) -> str | None:
    kind = _normalise_artifact_kind(artifact.get("kind"))
    path = str(artifact.get("path") or "").lower()
    if kind in {
        "decision_brief",
        "prd",
        "tdd_plan",
        "grill_findings",
        "issues",
        "implementation_plan",
        "outcome",
    }:
        return kind
    if "grill" in path:
        return "grill_findings"
    if "issue" in path:
        return "issues"
    if "implementation-plan" in path or "implementation_plan" in path:
        return "implementation_plan"
    if "tdd" in path:
        return "tdd_plan"
    if "prd" in path:
        return "prd"
    return None


def _normalise_artifact_kind(value: Any) -> str:
    kind = str(value or "").strip().lower().replace("-", "_")
    if re.fullmatch(r"grill.*findings.*tdd", kind):
        return "grill_findings"
    return kind


def _valid_screenshot_paths(
    screenshots: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    missing: list[str] = []
    for screenshot in screenshots:
        path_value = screenshot.get("path")
        if not path_value:
            missing.append("<missing-path>")
            continue
        path = Path(str(path_value)).expanduser()
        if not path.exists() or not path.is_file():
            missing.append(str(path))
            continue
        if _image_format(path) is None:
            missing.append(f"{path}:invalid-image")
            continue
        valid.append(str(path))
    return valid, missing


def _visual_validation_evidence(
    screenshots: list[dict[str, Any]],
    *,
    required: bool,
) -> dict[str, Any]:
    if not screenshots:
        return {
            "status": "blocked" if required else "not_required",
            "reason": "no_visual_evidence" if required else "no_screenshots_supplied",
            "evidence": [],
            "failures": [],
            "allowed_sources": sorted(VISUAL_VALIDATION_SOURCES),
        }

    evidence: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, screenshot in enumerate(screenshots, start=1):
        path_value = screenshot.get("path")
        path = Path(str(path_value)).expanduser() if path_value else None
        source = _normalise_visual_source(
            screenshot.get("source")
            or screenshot.get("captured_by")
            or screenshot.get("tool")
        )
        validation = screenshot.get("validation")
        validation_payload = validation if isinstance(validation, dict) else {}
        validation_status = _normalise_validation_status(
            screenshot.get("validation_status")
            or validation_payload.get("status")
            or validation_payload.get("result")
        )
        validation_notes = str(
            screenshot.get("validation_notes")
            or validation_payload.get("notes")
            or screenshot.get("note")
            or ""
        ).strip()
        image_format = _image_format(path) if path is not None and path.exists() and path.is_file() else None
        item = {
            "index": index,
            "path": str(path) if path is not None else "<missing-path>",
            "source": source,
            "validation_status": validation_status,
            "image_format": image_format,
            "validation_notes_present": bool(validation_notes),
        }
        if image_format is None:
            failures.append({**item, "reason": "invalid_or_missing_image"})
        if source not in VISUAL_VALIDATION_SOURCES:
            failures.append({**item, "reason": "missing_or_unsupported_capture_source"})
        if validation_status not in VISUAL_VALIDATION_PASSED:
            failures.append({**item, "reason": "visual_review_not_passed"})
        evidence.append(item)

    return {
        "status": "blocked" if failures else "ok",
        "reason": "visual_validation_failed" if failures else "visual_validation_ok",
        "evidence": evidence,
        "failures": failures,
        "allowed_sources": sorted(VISUAL_VALIDATION_SOURCES),
    }


def _normalise_visual_source(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _normalise_validation_status(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _image_format(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        header = path.read_bytes()[:32]
    except OSError:
        return None
    if header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR":
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    return None


def _maybe_artifact(payload: dict[str, Any]) -> PlanningArtifact | None:
    path = payload.get("path")
    kind = payload.get("kind")
    if not path or not kind:
        return None
    return PlanningArtifact(
        path=Path(str(path)).expanduser(),
        kind=_normalise_artifact_kind(kind),  # type: ignore[arg-type]
        mutable_by_worker=bool(payload.get("mutable_by_worker", False)),
    )


def _maybe_screenshot(payload: dict[str, Any]) -> ScreenshotArtifact | None:
    path = payload.get("path")
    if not path:
        return None
    return ScreenshotArtifact(
        path=Path(str(path)).expanduser(),
        label=str(payload.get("label") or Path(str(path)).stem),
        note=str(payload.get("note") or ""),
        source=_normalise_visual_source(
            payload.get("source")
            or payload.get("captured_by")
            or payload.get("tool")
        ),
        validation_status=_normalise_validation_status(
            payload.get("validation_status")
            or (
                payload.get("validation", {}).get("status")
                if isinstance(payload.get("validation"), dict)
                else ""
            )
        ),
        validation_notes=str(
            payload.get("validation_notes")
            or (
                payload.get("validation", {}).get("notes")
                if isinstance(payload.get("validation"), dict)
                else ""
            )
            or ""
        ),
    )


def _display_path(path: Path, cwd: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(cwd))
    except ValueError:
        return str(resolved)


def _redacted_prompt_argv(argv: list[str]) -> list[str]:
    if not argv:
        return []
    return [*argv[:-1], "[PROMPT_REDACTED]"]


if __name__ == "__main__":
    main()

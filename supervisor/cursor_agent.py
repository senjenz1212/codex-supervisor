"""Cursor SDK invocation boundary for tri-agent workflow review.

The supervisor treats Cursor as an optional independent reviewer. Cursor must
return the same typed outcome block as Claude so the existing validators remain
the acceptance boundary.
"""
from __future__ import annotations

import os
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal

from .dual_agent import Outcome, ProbeResult, evaluate_outcome_fidelity, outcome_accepts
from .dual_agent_lead import GateName, ModelQuality, PlanningArtifact
from .agent_mailbox import critical_review_prompt

CursorFailureClassification = Literal[
    "reviewer_contract_unmet",
    "reviewer_infrastructure_unavailable",
]


@dataclass(frozen=True)
class CursorInvocationRequest:
    task_id: str
    gate: GateName
    instruction: str
    cwd: str | Path
    claude_outcome: dict[str, Any] | None = None
    planning_artifacts: tuple[PlanningArtifact, ...] = ()
    quality: ModelQuality = "best"
    model: str | None = None
    timeout_s: int = 600
    api_key: str | None = None
    mode: str = "plan"
    tool_receipts: tuple[dict[str, Any], ...] = ()
    expected_specialists: tuple[str, ...] = ("Cursor Reviewer",)
    expected_decisions: tuple[str, ...] = ()
    expected_objections: tuple[str, ...] = ()
    contract_retry_limit: int = 3


@dataclass(frozen=True)
class CursorInvocationResult:
    probe: ProbeResult
    outcome: Outcome | None
    transcript: str
    agent_id: str | None = None
    run_id: str | None = None
    status: str | None = None
    model: str | None = None
    duration_ms: int | None = None
    failure_classification: CursorFailureClassification | None = None
    recoverable: bool = False
    attempts: int = 1
    retry_reasons: tuple[str, ...] = ()


CursorRunner = Callable[[CursorInvocationRequest], CursorInvocationResult]
StatusRunner = Callable[..., subprocess.CompletedProcess[str]]


def select_cursor_model(
    *,
    quality: ModelQuality,
    explicit_model: str | None = None,
) -> str:
    if explicit_model:
        return explicit_model
    # Cursor's current documented SDK examples use composer-2.5 for both local
    # and cloud agents. Keep one default until model discovery is wired.
    return "composer-2.5"


def build_cursor_prompt(request: CursorInvocationRequest) -> str:
    artifact_lines = [
        f"- {artifact.kind}: {Path(artifact.path)}"
        for artifact in request.planning_artifacts
    ]
    artifacts = "\n".join(artifact_lines) if artifact_lines else "- none"
    receipt_lines = [
        f"- {receipt.get('receipt_id') or receipt.get('id') or 'receipt'}: "
        f"{_receipt_prompt_payload(receipt)}"
        for receipt in request.tool_receipts
    ]
    receipts = "\n".join(receipt_lines) if receipt_lines else "- none"
    claude_outcome = request.claude_outcome or {}
    return "\n".join([
        f"Tri-agent review gate: {request.gate}.",
        f"Task id: {request.task_id}.",
        "",
        "Role: Cursor is an independent reviewer/challenger, not the implementer.",
        "Do not edit files. Inspect the worktree and artifacts, then judge the gate.",
        critical_review_prompt("Claude outcome and gate evidence"),
        "",
        "Instruction:",
        request.instruction.strip(),
        "",
        "Planning artifacts:",
        artifacts,
        "",
        "Evidence receipts:",
        receipts,
        "",
        "Claude outcome JSON:",
        str(claude_outcome),
        "",
        "Return a specialist named Cursor Reviewer. Use decision accept only if the gate should advance.",
        _outcome_block_contract(),
    ])


def cursor_accepts(result: CursorInvocationResult | None) -> bool:
    if result is None or not result.probe.ok or result.outcome is None:
        return False
    return outcome_accepts(result.outcome)


def invoke_cursor_agent(
    request: CursorInvocationRequest,
    *,
    status_runner: StatusRunner = subprocess.run,
) -> CursorInvocationResult:
    before_status = _git_status(request.cwd, status_runner=status_runner)
    max_attempts = 1 + max(0, int(request.contract_retry_limit))
    attempt_transcripts: list[str] = []
    retry_reasons: list[str] = []
    last_probe: ProbeResult | None = None
    last_outcome: Outcome | None = None
    last_metadata: dict[str, Any] = {}

    for attempt in range(1, max_attempts + 1):
        attempt_request = request
        if attempt > 1:
            previous_reason = last_probe.reason if last_probe is not None else "unknown"
            attempt_request = replace(
                request,
                instruction=_contract_corrective_instruction(
                    request.instruction,
                    previous_reason=previous_reason,
                    attempt=attempt,
                    max_attempts=max_attempts,
                ),
            )
        try:
            transcript, metadata = _run_cursor_sdk(attempt_request)
        except ModuleNotFoundError as e:
            if e.name == "cursor_sdk":
                return _cursor_infrastructure_result(
                    reason="cursor_sdk_missing",
                    attempts=attempt,
                    retry_reasons=tuple(retry_reasons),
                )
            raise
        except Exception as e:  # pragma: no cover - exact SDK exception classes vary.
            return _cursor_infrastructure_result(
                reason="cursor_invocation_failed",
                attempts=attempt,
                retry_reasons=tuple(retry_reasons),
                details={"error": str(e)},
            )

        attempt_transcripts.append(
            f"[cursor attempt {attempt}/{max_attempts}]\n{transcript}"
        )
        last_metadata = metadata
        probe, outcome = evaluate_outcome_fidelity(
            transcript,
            expected_specialists=request.expected_specialists,
            expected_decisions=request.expected_decisions,
            expected_objections=request.expected_objections,
        )
        if probe.ok and outcome is not None:
            probe = _evaluate_cursor_contract_completeness(outcome)
        last_probe = probe
        last_outcome = outcome
        if _should_retry_cursor_outcome(probe):
            retry_reasons.append(probe.reason)
            if attempt < max_attempts:
                continue

        after_status = _git_status(request.cwd, status_runner=status_runner)
        if before_status is not None and after_status is not None and before_status != after_status:
            last_probe = ProbeResult(
                "CURSOR",
                "red",
                "cursor_modified_worktree",
                {"before": before_status, "after": after_status},
            )
            last_outcome = None
        elif last_probe.ok:
            last_probe = ProbeResult("CURSOR", "green", "cursor_review_ok", last_probe.details)

        break

    transcript = "\n\n".join(attempt_transcripts)
    assert last_probe is not None
    if _should_retry_cursor_outcome(last_probe):
        original_reason = last_probe.reason
        last_probe = ProbeResult(
            "CURSOR",
            "red",
            "reviewer_contract_unmet",
            {
                **last_probe.details,
                "original_reason": original_reason,
                "attempts": len(attempt_transcripts),
                "retry_reasons": list(retry_reasons),
                "recoverable": True,
            },
        )
        return CursorInvocationResult(
            probe=last_probe,
            outcome=None,
            transcript=transcript,
            agent_id=last_metadata.get("agent_id"),
            run_id=last_metadata.get("run_id"),
            status=last_metadata.get("status"),
            model=last_metadata.get("model"),
            duration_ms=last_metadata.get("duration_ms"),
            failure_classification="reviewer_contract_unmet",
            recoverable=True,
            attempts=len(attempt_transcripts),
            retry_reasons=tuple(retry_reasons),
        )

    return CursorInvocationResult(
        probe=last_probe,
        outcome=last_outcome,
        transcript=transcript,
        agent_id=last_metadata.get("agent_id"),
        run_id=last_metadata.get("run_id"),
        status=last_metadata.get("status"),
        model=last_metadata.get("model"),
        duration_ms=last_metadata.get("duration_ms"),
        attempts=max(1, len(attempt_transcripts)),
        retry_reasons=tuple(retry_reasons),
    )


def _run_cursor_sdk(request: CursorInvocationRequest) -> tuple[str, dict[str, Any]]:
    from cursor_sdk import Agent, LocalAgentOptions

    api_key = request.api_key or os.environ.get("CURSOR_API_KEY")
    kwargs: dict[str, Any] = {
        "model": select_cursor_model(quality=request.quality, explicit_model=request.model),
        "local": LocalAgentOptions(cwd=str(Path(request.cwd).expanduser())),
    }
    if api_key:
        kwargs["api_key"] = api_key

    with Agent.create(**kwargs) as agent:
        run = agent.send(build_cursor_prompt(request), {"mode": request.mode})
        transcript = run.text()
        metadata = {
            "agent_id": getattr(agent, "agent_id", None),
            "run_id": getattr(run, "id", None),
            "status": getattr(run, "status", None),
            "model": _model_id(getattr(run, "model", None) or getattr(agent, "model", None)),
            "duration_ms": getattr(run, "duration_ms", None),
        }
    return transcript, metadata


def _git_status(cwd: str | Path, *, status_runner: StatusRunner) -> str | None:
    try:
        completed = status_runner(
            ["git", "status", "--porcelain=v1"],
            cwd=str(Path(cwd).expanduser()),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or ""


def _model_id(model: Any) -> str | None:
    if model is None:
        return None
    if isinstance(model, str):
        return model
    value = getattr(model, "id", None)
    return str(value) if value is not None else str(model)


def _should_retry_cursor_outcome(probe: ProbeResult) -> bool:
    return (
        probe.reason == "missing dual_agent_outcome block"
        or probe.reason.startswith("invalid dual_agent_outcome block")
        or probe.reason == "outcome_missing_required_fields"
        or probe.reason == "outcome_signal_loss"
    )


def _evaluate_cursor_contract_completeness(outcome: Outcome) -> ProbeResult:
    missing_fields: list[str] = []
    if not str(outcome.confidence_rationale or "").strip():
        missing_fields.append("confidence_rationale")
    if not outcome.confidence_criteria:
        missing_fields.append("confidence_criteria")
    critical_review = outcome.critical_review if isinstance(outcome.critical_review, dict) else {}
    for field_name in ("missing_evidence", "contradictions_checked", "assumptions_to_verify"):
        if field_name not in critical_review or not isinstance(critical_review.get(field_name), list):
            missing_fields.append(f"critical_review.{field_name}")
    for field_name in (
        "strongest_objection",
        "what_would_change_my_mind",
        "decision",
        "severity",
    ):
        if not str(critical_review.get(field_name) or "").strip():
            missing_fields.append(f"critical_review.{field_name}")
    if missing_fields:
        return ProbeResult(
            "CURSOR",
            "red",
            "outcome_missing_required_fields",
            {"fields": missing_fields},
        )
    return ProbeResult("CURSOR", "green", "cursor_review_ok")


def _contract_corrective_instruction(
    instruction: str,
    *,
    previous_reason: str,
    attempt: int,
    max_attempts: int,
) -> str:
    return "\n\n".join([
        instruction.strip(),
        (
            f"Corrective retry {attempt}/{max_attempts}: the previous Cursor review "
            f"failed the typed outcome contract: {previous_reason}. Return a valid "
            "<dual_agent_outcome>{...compact JSON...}</dual_agent_outcome> block. "
            "Do not omit the block. Do not use null values for specialist decisions. "
            "Repeat required decisions in the top-level decisions array."
        ),
        _outcome_block_contract(),
    ])


def _cursor_infrastructure_result(
    *,
    reason: str,
    attempts: int,
    retry_reasons: tuple[str, ...],
    details: dict[str, Any] | None = None,
) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult(
            "CURSOR",
            "red",
            "reviewer_infrastructure_unavailable",
            {
                "original_reason": reason,
                "attempts": attempts,
                "retry_reasons": list(retry_reasons),
                "recoverable": True,
                **(details or {}),
            },
        ),
        outcome=None,
        transcript="",
        failure_classification="reviewer_infrastructure_unavailable",
        recoverable=True,
        attempts=attempts,
        retry_reasons=retry_reasons,
    )


def _outcome_block_contract() -> str:
    return (
        "Always end with <dual_agent_outcome>{...valid compact JSON...}</dual_agent_outcome>. "
        "The JSON must include: task_id string, summary string, specialists array, "
        "decisions array, objections array, changed_files array, tests array, "
        "test_status string, confidence number from 0 to 1, confidence_rationale string, "
        "confidence_criteria array, claims array, and critical_review object. "
        "critical_review must include strongest_objection string, missing_evidence array, "
        "contradictions_checked array, assumptions_to_verify array, "
        "what_would_change_my_mind string, decision string, and severity string. "
        "Every specialist object must include a string name and a string decision."
    )


def _receipt_prompt_payload(receipt: dict[str, Any]) -> str:
    allowed = {
        key: receipt.get(key)
        for key in (
            "receipt_id",
            "id",
            "kind",
            "type",
            "status",
            "result",
            "claims",
            "command",
            "changed_files",
            "remote",
            "branch",
            "commit",
            "path",
            "label",
        )
        if receipt.get(key) not in (None, "", [], {})
    }
    return json.dumps(allowed or receipt, sort_keys=True, default=str)

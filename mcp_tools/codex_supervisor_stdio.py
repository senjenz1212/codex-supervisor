"""Codex-facing stdio MCP server for supervisor control.

This module is intentionally independent of the Claude Agent SDK wrappers in
`mcp_tools.codex_tools` and `mcp_tools.supervisor_tools`. Codex loads this
server through its external MCP configuration and receives ordinary MCP tools.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from supervisor.config import Config
from supervisor.agent_mailbox import (
    AgentMailboxMessage,
    codex_confidence_report,
    outcome_confidence_report,
    planning_artifact_refs,
)
from supervisor.cursor_agent import (
    CursorInvocationRequest,
    CursorInvocationResult,
    CursorRunner,
    cursor_accepts,
    invoke_cursor_agent,
)
from supervisor.dual_agent import GateRound, evaluate_deadlock_budget
from supervisor.dual_agent_artifacts import (
    ScreenshotArtifact,
    default_dual_agent_artifact_dir,
    export_dual_agent_run_artifacts,
)
from supervisor.dual_agent_workflow import (
    WORKFLOW_GATES,
    claude_accepts,
    ensure_workflow_source_artifacts,
    mandatory_artifact_status,
    verify_workflow_claims,
    workflow_visual_evidence_policy,
    workflow_milestone_text,
    workflow_resume_prompt,
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


class CodexSupervisorMcpAPI:
    def __init__(
        self,
        cfg: Config,
        state: State,
        *,
        runner: Runner = subprocess.run,
        codex_runner: Runner = subprocess.run,
        cursor_runner: CursorRunner | None = None,
        notifier: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.runner = runner
        self.codex_runner = codex_runner
        self.cursor_runner = cursor_runner or invoke_cursor_agent
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
        planning_artifacts: list[dict[str, Any]] | None = None,
        artifact_policy: str = "strict",
        user_facing: bool = False,
        screenshots: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
            planning_artifacts=planning_artifacts,
        )
        notifier = self._notifier()
        if notifier is not None:
            result = await run_dual_agent_gate_with_escalation(
                spec,
                state=self.state,
                notifier=notifier,
                runner=self.runner,
            )
        else:
            result = run_dual_agent_gate(spec, runner=self.runner, state=self.state)
        payload = _gate_result_payload(result)
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
        planning_artifacts: list[dict[str, Any]] | None = None,
        artifact_policy: str = "strict",
        user_facing: bool = False,
        screenshots: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
        max_rounds_per_gate: int = 5,
        quality: str = "best",
        timeout_s: int = 900,
        planning_artifacts: list[dict[str, Any]] | None = None,
        screenshots: list[dict[str, Any]] | None = None,
        verified_claims: list[str] | None = None,
        cursor_review: bool = False,
        cursor_model: str | None = None,
    ) -> dict[str, Any]:
        max_rounds = max(1, int(max_rounds_per_gate))
        screenshot_payloads = screenshots or []
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
        )
        effective_user_facing = bool(visual_policy["required"])
        self.state.upsert_dual_agent_workflow(
            run_id=run_id,
            task_id=task_id,
            cwd=str(Path(cwd).expanduser()),
            intent=intent,
            current_gate=WORKFLOW_GATES[0],
            status="running",
            max_rounds_per_gate=max_rounds,
            user_facing=effective_user_facing,
        )
        notifier = self._notifier()
        await self._emit_workflow_milestone(
            notifier=notifier,
            run_id=run_id,
            task_id=task_id,
            milestone="started",
        )

        steps: list[dict[str, Any]] = []
        final_payload: dict[str, Any] | None = None
        for gate in WORKFLOW_GATES:
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
                payload = await self.start_dual_agent_gate(
                    task_id=task_id,
                    run_id=run_id,
                    gate=gate,  # type: ignore[arg-type]
                    instruction=_workflow_gate_instruction(
                        gate=gate,
                        intent=intent,
                        corrective_context=corrective_context,
                    ),
                    cwd=cwd,
                    expected_specialists=[],
                    expected_decisions=[],
                    expected_objections=[],
                    quality=quality,
                    timeout_s=timeout_s,
                    planning_artifacts=gate_artifacts,
                    artifact_policy="strict",
                    user_facing=effective_user_facing and gate == "outcome_review",
                    screenshots=screenshot_payloads,
                )
                final_payload = payload
                claim_probe = None
                cursor_result: CursorInvocationResult | None = None
                if gate == "outcome_review" and payload.get("status") == "accepted":
                    claim_probe = verify_workflow_claims(
                        outcome_payload=payload.get("outcome"),
                        user_facing=effective_user_facing,
                        screenshots=screenshot_payloads,
                        verified_claims=verified_claims,
                    )
                    payload["claim_verification"] = asdict(claim_probe)
                if cursor_review and payload.get("status") == "accepted":
                    self._write_interaction_message(
                        run_id=run_id,
                        message=AgentMailboxMessage(
                            task_id=task_id,
                            gate=gate,
                            round_index=round_index,
                            sender="codex",
                            recipient="cursor",
                            message_type="review_request",
                            content=_cursor_review_instruction(
                                gate=gate,
                                intent=intent,
                                corrective_context=corrective_context,
                            ),
                            artifacts=planning_artifact_refs(gate_artifacts),
                            metadata={
                                "claude_outcome": payload.get("outcome"),
                                "review_policy": "Cursor reviews only after Claude gate acceptance.",
                            },
                        ),
                    )
                    cursor_result = self.cursor_runner(
                        CursorInvocationRequest(
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
                            timeout_s=timeout_s,
                        )
                    )
                    cursor_payload = _cursor_result_payload(cursor_result)
                    payload["cursor_review"] = cursor_payload
                    self._write_interaction_message(
                        run_id=run_id,
                        message=AgentMailboxMessage(
                            task_id=task_id,
                            gate=gate,
                            round_index=round_index,
                            sender="cursor",
                            recipient="codex",
                            message_type="review_response",
                            content=(
                                cursor_result.outcome.summary
                                if cursor_result.outcome is not None
                                else cursor_result.probe.reason
                            ),
                            confidence=outcome_confidence_report(
                                cursor_result.outcome.model_dump()
                                if cursor_result.outcome is not None else None,
                                source="cursor",
                            ),
                            artifacts=planning_artifact_refs(gate_artifacts),
                            metadata={
                                "cursor_review": cursor_payload,
                            },
                        ),
                    )
                    self.state.write_event(
                        run_id=run_id,
                        source="dual_agent",
                        kind="tri_agent_cursor_review",
                        payload={
                            "task_id": task_id,
                            "gate": gate,
                            "cursor_review": cursor_payload,
                        },
                    )

                claude_decision = (
                    "accept"
                    if payload.get("status") == "accepted" and claude_accepts(payload.get("outcome"))
                    else "revise"
                )
                cursor_decision = (
                    "accept"
                    if not cursor_review or cursor_accepts(cursor_result)
                    else "revise"
                )
                codex_decision = (
                    "accept"
                    if (
                        payload.get("status") == "accepted"
                        and (claim_probe is None or claim_probe.ok)
                        and cursor_decision == "accept"
                    )
                    else "deny" if payload.get("status") == "blocked"
                    else "revise"
                )
                objection = (
                    "both agents accepted"
                    if (
                        codex_decision == "accept"
                        and claude_decision == "accept"
                        and cursor_decision == "accept"
                    )
                    else _workflow_round_objection(
                        payload=payload,
                        claim_probe=asdict(claim_probe) if claim_probe is not None else None,
                        cursor_review=_cursor_result_payload(cursor_result)
                        if cursor_result is not None else None,
                        round_index=round_index,
                        max_rounds=max_rounds,
                    )
                )
                round_result = self.record_gate_round(
                    run_id=run_id,
                    task_id=task_id,
                    gate=gate,  # type: ignore[arg-type]
                    round_index=round_index,
                    codex_decision=codex_decision,
                    claude_decision=claude_decision,
                    codex_confidence=0.95 if codex_decision == "accept" else 0.7,
                    claude_confidence=_outcome_confidence(payload),
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
                        content=objection,
                        confidence=codex_confidence_report(
                            decision=codex_decision,
                            gate_status=str(payload.get("status") or ""),
                            probe_statuses={
                                probe_id: str(probe.get("status") or "")
                                for probe_id, probe in (payload.get("probes") or {}).items()
                                if isinstance(probe, dict)
                            },
                            claim_verification=asdict(claim_probe) if claim_probe is not None else None,
                            cursor_review=_cursor_result_payload(cursor_result)
                            if cursor_result is not None else None,
                        ),
                        artifacts=planning_artifact_refs(gate_artifacts),
                        metadata={
                            "codex_decision": codex_decision,
                            "claude_decision": claude_decision,
                            "cursor_decision": cursor_decision,
                            "round_event_id": round_result["event_id"],
                        },
                    ),
                )
                gate_rounds.append(_gate_round_from_payload(round_result["round"]))

                if payload.get("status") == "blocked":
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
                    reason="claim_verification_failed"
                    if claim_probe is not None and not claim_probe.ok
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
                )

        artifact_status = mandatory_artifact_status(cwd=cwd, task_id=task_id)
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
        )

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
        event_id = self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_round",
            payload={
                "task_id": task_id,
                "gate": gate,
                "round": round_payload,
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
                   'dual_agent_interaction_message',
                   'tri_agent_cursor_review'
                 )
               ORDER BY event_id ASC""",
            (run_id,),
        ).fetchall()
        rounds: list[dict[str, Any]] = []
        planning_validations: list[dict[str, Any]] = []
        interactions: list[dict[str, Any]] = []
        cursor_reviews: list[dict[str, Any]] = []
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
                }))
            elif row["kind"] == "dual_agent_gate_result":
                latest_result = redact(payload)
                latest_result_event_id = int(row["event_id"])

        if not rounds and not planning_validations and latest_result is None:
            return {
                "status": "not_found",
                "run_id": run_id,
                "task_id": task_id,
                "rounds": [],
                "planning_validations": [],
                "interactions": [],
                "cursor_reviews": [],
                "result": None,
                "handoff_packet_path": None,
            }
        return {
            "status": "ok",
            "run_id": run_id,
            "task_id": task_id,
            "rounds": rounds,
            "planning_validations": planning_validations,
            "interactions": interactions,
            "cursor_reviews": cursor_reviews,
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
            "claim_verification": claim_probe,
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
        mandatory_artifacts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_export = self.export_gate_artifacts(
            run_id=run_id,
            task_id=task_id,
            cwd=cwd,
            screenshots=screenshots,
        )
        mandatory = mandatory_artifacts or mandatory_artifact_status(cwd=cwd, task_id=task_id)
        return redact({
            "status": status,
            "run_id": run_id,
            "task_id": task_id,
            "current_gate": current_gate,
            "steps": steps,
            "final_gate_result": final_gate_result,
            "visual_evidence_policy": visual_evidence_policy,
            "artifact_export": artifact_export,
            "mandatory_artifacts": mandatory,
            "resume": workflow_resume_prompt(self.state, run_id=run_id, task_id=task_id),
        })

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
        planning_artifacts: list[dict[str, Any]] | None,
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
            planning_artifacts=tuple(
                artifact
                for artifact in (
                    _maybe_artifact(item)
                    for item in (planning_artifacts or [])
                )
                if artifact is not None
            ),
        )

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
        notifier=notifier,
    )
    mcp = mcp_cls("codex_supervisor")

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
        planning_artifacts: list[dict[str, Any]] | None = None,
        artifact_policy: str = "strict",
        user_facing: bool = False,
        screenshots: list[dict[str, Any]] | None = None,
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
            planning_artifacts=planning_artifacts,
            artifact_policy=artifact_policy,
            user_facing=user_facing,
            screenshots=screenshots,
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
        planning_artifacts: list[dict[str, Any]] | None = None,
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
            planning_artifacts=planning_artifacts,
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
    async def run_dual_agent_workflow(
        cwd: str,
        task_id: str,
        run_id: str,
        intent: str,
        user_facing: bool = False,
        max_rounds_per_gate: int = 5,
        quality: str = "best",
        timeout_s: int = 900,
        planning_artifacts: list[dict[str, Any]] | None = None,
        screenshots: list[dict[str, Any]] | None = None,
        verified_claims: list[str] | None = None,
        cursor_review: bool = False,
        cursor_model: str | None = None,
    ) -> dict[str, Any]:
        return await tool_api.run_dual_agent_workflow(
            cwd=cwd,
            task_id=task_id,
            run_id=run_id,
            intent=intent,
            user_facing=user_facing,
            max_rounds_per_gate=max_rounds_per_gate,
            quality=quality,
            timeout_s=timeout_s,
            planning_artifacts=planning_artifacts,
            screenshots=screenshots,
            verified_claims=verified_claims,
            cursor_review=cursor_review,
            cursor_model=cursor_model,
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


def _gate_result_payload(result: DualAgentGateResult) -> dict[str, Any]:
    payload = {
        "task_id": result.task_id,
        "gate": result.gate,
        "status": result.status,
        "attempts": result.attempts,
        "handoff_packet_path": str(result.handoff_packet_path),
        "probes": {
            key: asdict(value)
            for key, value in result.probes.items()
        },
        "outcome": result.outcome.model_dump() if result.outcome is not None else None,
        "escalation": asdict(result.escalation) if result.escalation is not None else None,
    }
    return redact(payload)


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
) -> str:
    lines = [
        f"Supervisor-owned workflow gate: {gate}.",
        "",
        "Intent:",
        intent.strip(),
        "",
        "Review this gate against the current source artifacts and return a typed dual_agent_outcome.",
        "Use decisions/objections to say whether the gate should accept, revise, or deny.",
    ]
    if corrective_context:
        lines.extend([
            "",
            "Corrective context from the previous round:",
            corrective_context.strip(),
        ])
    return "\n".join(lines)


def _cursor_review_instruction(
    *,
    gate: str,
    intent: str,
    corrective_context: str,
) -> str:
    lines = [
        f"Independently review the {gate} gate for this tri-agent workflow.",
        "Accept only if the gate should advance after reading the artifacts and Claude outcome.",
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
    claim_probe: dict[str, Any] | None,
    cursor_review: dict[str, Any] | None,
    round_index: int,
    max_rounds: int,
) -> str:
    if payload.get("status") == "blocked":
        escalation = payload.get("escalation") if isinstance(payload.get("escalation"), dict) else {}
        return str(escalation.get("reason") or "gate blocked")
    if claim_probe is not None and claim_probe.get("status") != "green":
        return str(claim_probe.get("reason") or "claim verification failed")
    if cursor_review is not None and not cursor_review.get("accepted"):
        probe = cursor_review.get("probe") if isinstance(cursor_review.get("probe"), dict) else {}
        reason = probe.get("reason") or "cursor reviewer did not accept"
        return f"cursor_review_failed: {reason}"
    if round_index >= max_rounds:
        return "max_rounds_per_gate exhausted without both agents accepting"
    return "agents have not both accepted yet; revise and continue"


def _cursor_result_payload(result: CursorInvocationResult) -> dict[str, Any]:
    return redact({
        "accepted": cursor_accepts(result),
        "probe": asdict(result.probe),
        "outcome": result.outcome.model_dump() if result.outcome is not None else None,
        "agent_id": result.agent_id,
        "run_id": result.run_id,
        "status": result.status,
        "model": result.model,
        "duration_ms": result.duration_ms,
        "transcript_tail": result.transcript[-4000:],
    })


def _workflow_step_dict(gate: str, status: str, attempts: int) -> dict[str, Any]:
    return {
        "gate": gate,
        "status": status,
        "attempt_count": attempts,
    }


def _outcome_confidence(payload: dict[str, Any]) -> float:
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    try:
        value = float(outcome.get("confidence"))
    except (TypeError, ValueError):
        return 0.7
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
) -> dict[str, Any]:
    policy = str(artifact_policy or "strict").strip().lower()
    required = list(STRICT_ARTIFACT_REQUIREMENTS.get(gate, ()))
    required_prerequisites = list(GATE_PREREQUISITES.get(gate, ()))
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
    return str(value or "").strip().lower().replace("-", "_")


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
        kind=str(kind),  # type: ignore[arg-type]
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

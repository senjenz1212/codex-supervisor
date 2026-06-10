"""Production-facing dual-agent gate runner primitives.

This is still deliberately small: the runner creates the handoff packet,
invokes Claude Code `/lead`, validates the output through Slice 0 probes, and
stops on the first blocked gate. Live process calls remain injectable.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .dual_agent import (
    GateRound,
    Outcome,
    ProbeResult,
    WorkerInvocationProbeInput,
    evaluate_deadlock_budget,
    evaluate_outcome_gate_decision,
    evaluate_worker_invocation,
)
from .agent_mailbox import (
    AgentMailboxMessage,
    critical_review_from_outcome,
    outcome_confidence_report,
    planning_artifact_refs,
)
from .dual_agent_lead import (
    DynamicWorkflowTaskClass,
    AgenticLeadPolicyMode,
    EvidenceGrade,
    ExecutionLayerMode,
    GateName,
    LeadInvocationRequest,
    LeadInvocationResult,
    ModelQuality,
    OutcomeValidationPolicy,
    PlanningArtifact,
    Runner,
    compute_file_sha256,
    handoff_packet_path,
    invoke_claude_lead,
    select_lead_model,
    verify_planning_artifact_boundaries,
    write_handoff_packet,
)
from .planning_validator import (
    planning_validation_probe,
    required_planning_kinds_for_gate,
    validate_planning_artifacts,
)
from .state import State
from .trace_envelope import ensure_tool_call_timing, timed_tool_call


HANDOFF_LOCK_RECLAIM_GRACE_S = 60


@dataclass(frozen=True)
class ReplayFixture:
    token_size: int
    stdout_path: Path
    sha256: str


@dataclass(frozen=True)
class DeadlockEscalation:
    status: str
    action_id: int | None = None
    ask_id: int | None = None
    nonce: str | None = None
    probe: ProbeResult | None = None


@dataclass(frozen=True)
class ValidationEscalation:
    status: str
    action_id: int | None = None
    ask_id: int | None = None
    nonce: str | None = None
    policy_field: str | None = None
    probe: ProbeResult | None = None


@dataclass(frozen=True)
class DualAgentGateSpec:
    task_id: str
    run_id: str
    gate: GateName
    instruction: str
    cwd: str | Path
    planning_artifacts: tuple[PlanningArtifact, ...] = ()
    expected_specialists: tuple[str, ...] = ()
    expected_decisions: tuple[str, ...] = ()
    expected_objections: tuple[str, ...] = ()
    quality: ModelQuality = "best"
    model: str | None = None
    budget_usd: float = 5.0
    timeout_s: int = 600
    execution_layer_mode: ExecutionLayerMode = "lead_direct"
    dynamic_workflow_task_class: DynamicWorkflowTaskClass | None = None
    agentic_lead_policy: AgenticLeadPolicyMode = "off"
    min_subagents: int = 0
    required_roles: tuple[str, ...] = ()
    solo_exception_for_artifact_only_gates: bool = False
    required_evidence_grade: EvidenceGrade = "self_reported"
    lead_skill_path: str | Path | None = None
    outcome_validation_policy: OutcomeValidationPolicy = field(default_factory=OutcomeValidationPolicy)
    required_planning_kinds: tuple[str, ...] | None = None
    injected_lesson_block: str = ""
    injected_lesson_block_sha256: str = ""
    injected_lesson_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DualAgentGateResult:
    task_id: str
    gate: GateName
    status: str
    probes: dict[str, ProbeResult]
    handoff_packet_path: Path
    lead_result: LeadInvocationResult | None = None
    outcome: Outcome | None = None
    attempts: int = 0
    escalation: DeadlockEscalation | ValidationEscalation | None = None


def build_lead_replay_stdout(
    transcript: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    cost_usd: float = 0.0,
) -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "result": transcript,
        "model": model,
        "total_cost_usd": cost_usd,
    })


def write_replay_fixture_family(
    directory: str | Path,
    *,
    seed_transcript: str,
    token_sizes: tuple[int, ...],
    model: str,
) -> list[ReplayFixture]:
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures: list[ReplayFixture] = []
    for token_size in token_sizes:
        transcript = _expand_transcript(seed_transcript, token_size)
        stdout = build_lead_replay_stdout(transcript, model=model)
        path = out_dir / f"lead-{token_size}.stdout.json"
        path.write_text(stdout)
        fixtures.append(ReplayFixture(
            token_size=token_size,
            stdout_path=path,
            sha256=compute_file_sha256(path),
        ))
    return fixtures


def make_replay_runner(stdout_path: str | Path) -> Runner:
    text = Path(stdout_path).read_text()

    def _runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")

    return _runner


def run_dual_agent_gate(
    spec: DualAgentGateSpec,
    *,
    runner: Runner = subprocess.run,
    state: State | None = None,
) -> DualAgentGateResult:
    packet_path = handoff_packet_path(spec.cwd, spec.task_id)
    lock_path = _handoff_lock_path(spec)
    lock_probe = _acquire_handoff_lock(lock_path, spec=spec, packet_path=packet_path)
    if lock_probe is not None:
        return DualAgentGateResult(
            task_id=spec.task_id,
            gate=spec.gate,
            status="blocked",
            probes={"P1": lock_probe},
            handoff_packet_path=packet_path,
            attempts=0,
        )
    try:
        with timed_tool_call(
            "validate_planning_artifacts",
            args={
                "task_id": spec.task_id,
                "gate": spec.gate,
                "artifact_count": len(spec.planning_artifacts),
                "required_kinds": sorted(_required_planning_kinds(spec)),
            },
        ) as planning_tool_call:
            planning_result = validate_planning_artifacts(
                spec.planning_artifacts,
                required_kinds=_required_planning_kinds(spec),
                gate=spec.gate,
            )
        planning_probe = planning_validation_probe(planning_result, task_id=spec.task_id)
        planning_event_id: int | None = None
        if state is not None:
            planning_payload = planning_result.to_event_payload(
                task_id=spec.task_id,
                gate=spec.gate,
            )
            planning_payload["tool_calls"] = [
                _probe_tool_call(
                    name="validate_planning_artifacts",
                    probe=planning_probe,
                    base=planning_tool_call,
                ),
            ]
            planning_event_id = state.write_event(
                run_id=spec.run_id,
                source="dual_agent",
                kind="dual_agent_planning_validation",
                payload=planning_payload,
            )
        if not planning_result.ok:
            if state is not None:
                state.write_event(
                    run_id=spec.run_id,
                    source="dual_agent",
                    kind="dual_agent_interaction_message",
                    payload=AgentMailboxMessage(
                        task_id=spec.task_id,
                        gate=spec.gate,
                        sender="supervisor",
                        recipient="codex",
                        message_type="gate_blocked_before_worker",
                        persona_id="supervisor.planning_validator",
                        content="Planning validation blocked the gate before Claude Code /lead was invoked.",
                        addresses=_addresses(
                            f"event:{planning_event_id}" if planning_event_id is not None else "",
                        ),
                        confidence=None,
                        artifacts=planning_artifact_refs(spec.planning_artifacts),
                        metadata={
                            "planning_validation": planning_result.to_event_payload(
                                task_id=spec.task_id,
                                gate=spec.gate,
                            ),
                            "tool_calls": [
                                _probe_tool_call(
                                    name="validate_planning_artifacts",
                                    probe=planning_probe,
                                    event_id=planning_event_id,
                                    base=planning_tool_call,
                                ),
                            ],
                        },
                    ).to_event_payload(),
                )
            return DualAgentGateResult(
                task_id=spec.task_id,
                gate=spec.gate,
                status="blocked",
                probes={"P_planning": planning_probe},
                handoff_packet_path=packet_path,
                attempts=0,
            )

        request = _lead_request(spec)
        with timed_tool_call(
            "write_handoff_packet",
            args={
                "task_id": spec.task_id,
                "gate": spec.gate,
                "artifact_count": len(spec.planning_artifacts),
            },
        ) as handoff_tool_call:
            packet_path = write_handoff_packet(
                request,
                planning_artifacts=spec.planning_artifacts,
                lead_skill_path=spec.lead_skill_path,
                outcome_validation_policy=spec.outcome_validation_policy,
            )
        handoff_tool_call.update({
            "status": "completed",
            "path": str(packet_path),
            "result_summary": {
                "handoff_packet_path": str(packet_path),
                "artifact_count": len(spec.planning_artifacts),
            },
        })
        request = _lead_request(spec, packet_path=packet_path)
        request_event_id: int | None = None
        if state is not None:
            request_event_id = state.write_event(
                run_id=spec.run_id,
                source="dual_agent",
                kind="dual_agent_interaction_message",
                payload=AgentMailboxMessage(
                    task_id=spec.task_id,
                    gate=spec.gate,
                    sender="codex",
                    recipient="claude_code",
                    message_type="gate_request",
                    persona_id="codex.lifecycle_reviewer",
                    content=spec.instruction,
                    addresses=_addresses(
                        f"event:{planning_event_id}" if planning_event_id is not None else "",
                        f"handoff:{packet_path}",
                    ),
                    artifacts=planning_artifact_refs(spec.planning_artifacts),
                    metadata={
                        "expected_specialists": list(spec.expected_specialists),
                        "expected_decisions": list(spec.expected_decisions),
                        "expected_objections": list(spec.expected_objections),
                        "lesson_injection": {
                            "block_sha256": spec.injected_lesson_block_sha256,
                            "lesson_ids": list(spec.injected_lesson_ids),
                            "advisory_only": True,
                        },
                        "tool_calls": [
                            _probe_tool_call(
                                name="validate_planning_artifacts",
                                probe=planning_probe,
                                event_id=planning_event_id,
                                base=planning_tool_call,
                            ),
                            ensure_tool_call_timing(handoff_tool_call),
                        ],
                    },
                ).to_event_payload(),
            )
        lead_tool_calls: list[dict[str, Any]] = []
        with timed_tool_call(
            "invoke_claude_lead",
            args=_lead_invocation_args(spec, attempts=1),
        ) as lead_tool_call:
            lead_result = invoke_claude_lead(request, runner=runner)
        lead_tool_call.update(_lead_invocation_tool_fields(lead_result, attempts=1))
        lead_tool_calls.append(ensure_tool_call_timing(lead_tool_call))
        attempts = 1
        if _should_retry_outcome(lead_result.probe, spec.outcome_validation_policy):
            corrective = _lead_request(
                spec,
                packet_path=packet_path,
                instruction=(
                    spec.instruction
                    + "\n\nCorrective retry: the previous response did not contain "
                      "a valid <dual_agent_outcome> block. Return the required block only after your summary. "
                      "Every specialist must have a string name and string decision; do not use null. "
                      "Repeat required decisions in the top-level decisions array."
                ),
            )
            with timed_tool_call(
                "invoke_claude_lead",
                args=_lead_invocation_args(spec, attempts=2, corrective_retry=True),
            ) as retry_tool_call:
                lead_result = invoke_claude_lead(corrective, runner=runner)
            attempts = 2
            retry_tool_call.update(_lead_invocation_tool_fields(lead_result, attempts=2))
            lead_tool_calls.append(ensure_tool_call_timing(retry_tool_call))
        lead_parent_tool_call_id = lead_tool_calls[-1].get("tool_call_id") if lead_tool_calls else None

        probes: dict[str, ProbeResult] = {}
        with timed_tool_call(
            "evaluate_worker_invocation",
            args={"task_id": spec.task_id, "gate": spec.gate, "probe_id": "P2"},
            parent_tool_call_id=lead_parent_tool_call_id,
        ) as p2_tool_call:
            probes["P2"] = _p2_from_lead_result(lead_result)
        p2_tool_call = _probe_tool_call(
            name="evaluate_worker_invocation",
            probe=probes["P2"],
            base=p2_tool_call,
        )
        with timed_tool_call(
            "evaluate_outcome_fidelity",
            args={"task_id": spec.task_id, "gate": spec.gate, "probe_id": "P3"},
            parent_tool_call_id=lead_parent_tool_call_id,
        ) as p3_tool_call:
            probes["P3"] = lead_result.probe
        p3_tool_call = _probe_tool_call(
            name="evaluate_outcome_fidelity",
            probe=probes["P3"],
            base=p3_tool_call,
        )
        with timed_tool_call(
            "verify_planning_artifact_boundaries",
            args={
                "task_id": spec.task_id,
                "gate": spec.gate,
                "probe_id": "P1",
                "handoff_packet_path": str(packet_path),
            },
            parent_tool_call_id=lead_parent_tool_call_id,
        ) as p1_tool_call:
            probes["P1"] = verify_planning_artifact_boundaries(packet_path)
        p1_tool_call = _probe_tool_call(
            name="verify_planning_artifact_boundaries",
            probe=probes["P1"],
            base=p1_tool_call,
        )
        with timed_tool_call(
            "evaluate_outcome_gate_decision",
            args={"task_id": spec.task_id, "gate": spec.gate, "probe_id": "P4"},
            parent_tool_call_id=lead_parent_tool_call_id,
        ) as p4_tool_call:
            probes["P4"] = evaluate_outcome_gate_decision(lead_result.outcome)
        p4_tool_call = _probe_tool_call(
            name="evaluate_outcome_gate_decision",
            probe=probes["P4"],
            base=p4_tool_call,
        )
        probes["P_planning"] = planning_probe
        response_tool_calls = [
            *lead_tool_calls,
            p2_tool_call,
            p3_tool_call,
            p1_tool_call,
            p4_tool_call,
        ]

        if state is not None:
            outcome_payload = (
                lead_result.outcome.model_dump()
                if lead_result.outcome is not None else None
            )
            state.write_event(
                run_id=spec.run_id,
                source="dual_agent",
                kind="dual_agent_interaction_message",
                payload=AgentMailboxMessage(
                    task_id=spec.task_id,
                    gate=spec.gate,
                    sender="claude_code",
                    recipient="codex",
                    message_type="gate_response",
                    persona_id="claude_code.lead_worker",
                    content=(
                        lead_result.outcome.summary
                        if lead_result.outcome is not None
                        else lead_result.probe.reason
                    ),
                    addresses=_addresses(
                        f"event:{request_event_id}" if request_event_id is not None else "",
                        f"handoff:{packet_path}",
                    ),
                    confidence=outcome_confidence_report(
                        outcome_payload,
                        source="claude_code",
                    ),
                    claims=tuple(
                        str(item)
                        for item in (
                            outcome_payload.get("claims") if isinstance(outcome_payload, dict) else []
                        ) or []
                    ),
                    objections=tuple(
                        str(item)
                        for item in (
                            outcome_payload.get("objections") if isinstance(outcome_payload, dict) else []
                        ) or []
                    ),
                    evidence_refs=tuple([
                        *(
                            {
                                "kind": "reported_test",
                                "ref": str(item),
                                "status": (
                                    str(outcome_payload.get("test_status"))
                                    if isinstance(outcome_payload, dict)
                                    else ""
                                ),
                            }
                            for item in (
                                outcome_payload.get("tests") if isinstance(outcome_payload, dict) else []
                            ) or []
                        ),
                        *(
                            {
                                "kind": "reported_changed_file",
                                "ref": str(item),
                            }
                            for item in (
                                outcome_payload.get("changed_files") if isinstance(outcome_payload, dict) else []
                            ) or []
                        ),
                    ]),
                    raw_transcript_refs=(
                        {
                            "kind": "claude_stdout",
                            "ref": "lead_result.stdout",
                            "bytes": lead_result.stdout_bytes,
                        },
                        {
                            "kind": "claude_handoff_packet",
                            "ref": str(packet_path),
                        },
                    ),
                    would_change_if=(
                        "A subsequent gate response changes the typed outcome, "
                        "or supervisor probes reject this response."
                    ),
                    critical_review=critical_review_from_outcome(
                        outcome_payload,
                        would_change_if=(
                            "A subsequent gate response changes the typed outcome, "
                            "or supervisor probes reject this response."
                        ),
                    ),
                    artifacts=planning_artifact_refs(spec.planning_artifacts),
                    metadata={
                        "probe": lead_result.probe.__dict__,
                        "attempts": attempts,
                        "model": lead_result.model,
                        "cost_usd": lead_result.cost_usd,
                        "outcome": outcome_payload,
                        "tool_calls": _lead_response_tool_calls(
                            lead_result=lead_result,
                            attempts=attempts,
                            probes=probes,
                            tool_calls=response_tool_calls,
                        ),
                    },
                ).to_event_payload(),
            )

        status = "accepted" if all(p.ok for p in probes.values()) else "blocked"
        return DualAgentGateResult(
            task_id=spec.task_id,
            gate=spec.gate,
            status=status,
            probes=probes,
            handoff_packet_path=packet_path,
            lead_result=lead_result,
            outcome=lead_result.outcome,
            attempts=attempts,
        )
    finally:
        _release_handoff_lock(lock_path)


def run_dual_agent_gates(
    specs: list[DualAgentGateSpec],
    *,
    runner: Runner = subprocess.run,
) -> list[DualAgentGateResult]:
    results: list[DualAgentGateResult] = []
    for spec in specs:
        result = run_dual_agent_gate(spec, runner=runner)
        results.append(result)
        if result.status != "accepted":
            break
    return results


def _required_planning_kinds(spec: DualAgentGateSpec) -> tuple[str, ...]:
    if spec.required_planning_kinds is not None:
        return spec.required_planning_kinds
    return required_planning_kinds_for_gate(spec.gate)


async def run_dual_agent_gate_with_escalation(
    spec: DualAgentGateSpec,
    *,
    state: State,
    notifier: Any,
    runner: Runner = subprocess.run,
) -> DualAgentGateResult:
    result = run_dual_agent_gate(spec, runner=runner, state=state)
    if result.status == "accepted":
        return result
    escalation = await _maybe_request_validation_escalation(
        result,
        spec=spec,
        state=state,
        notifier=notifier,
    )
    return DualAgentGateResult(
        task_id=result.task_id,
        gate=result.gate,
        status=result.status,
        probes=result.probes,
        handoff_packet_path=result.handoff_packet_path,
        lead_result=result.lead_result,
        outcome=result.outcome,
        attempts=result.attempts,
        escalation=escalation,
    )


def resume_pending_gates(
    specs: list[DualAgentGateSpec],
    *,
    state: State,
    runner: Runner = subprocess.run,
) -> list[DualAgentGateResult]:
    results: list[DualAgentGateResult] = []
    for spec in specs:
        signal = claim_resume_signal(
            state,
            run_id=spec.run_id,
            task_id=spec.task_id,
        )
        if signal is None:
            signal = claim_retry_signal(
                state,
                run_id=spec.run_id,
                task_id=spec.task_id,
            )
        if signal is None:
            continue
        results.append(run_dual_agent_gate(spec, runner=runner, state=state))
    return results


async def request_deadlock_escalation(
    *,
    state: State,
    notifier: Any,
    run_id: str,
    task_id: str,
    gate: GateName,
    rounds: list[GateRound],
    per_gate_cap: int,
    task_budget: int,
    now: Callable[[], int] | None = None,
) -> DeadlockEscalation:
    probe = evaluate_deadlock_budget(
        rounds,
        per_gate_cap=per_gate_cap,
        task_budget=task_budget,
    )
    if probe.reason != "paused_for_human":
        return DeadlockEscalation(status="not_deadlocked", probe=probe)

    current = int(now() if now is not None else time.time())
    nonce = secrets.token_hex(8)
    options = ["Pause", "Kill", "Continue"]
    question = (
        f"[{task_id}] Dual-agent gate deadlock.\n"
        f"gate={gate}\n"
        "Codex and Claude did not converge before budget exhaustion. Choose next action."
    )
    ask_id = state.create_ask(
        run_id,
        question,
        options,
        nonce=nonce,
        expires_at=current + 3600,
    )
    action_id = state.record_action(
        run_id=run_id,
        action_type="dual_agent_gate_deadlock",
        requested_by="dual_agent_gate",
        payload={
            "task_id": task_id,
            "gate": gate,
            "ask_id": ask_id,
            "nonce": nonce,
            "options": options,
            "rounds": [r.__dict__ for r in rounds],
            "escalation_type": "kill_or_pause",
            "reason": "budget_exhausted",
        },
        status="paused_for_human",
    )
    await notifier.send_approval_prompt(
        ask_id=ask_id,
        question=question,
        options=options,
        nonce=nonce,
    )
    return DeadlockEscalation(
        status="paused_for_human",
        action_id=action_id,
        ask_id=ask_id,
        nonce=nonce,
        probe=probe,
    )


async def request_validation_escalation(
    *,
    state: State,
    notifier: Any,
    run_id: str,
    task_id: str,
    gate: GateName,
    policy_field: str,
    probe: ProbeResult,
    now: Callable[[], int] | None = None,
) -> ValidationEscalation:
    current = int(now() if now is not None else time.time())
    nonce = secrets.token_hex(8)
    options = ["Pause", "Retry", "Cancel"]
    question = (
        f"[{task_id}] Dual-agent validation failure.\n"
        f"gate={gate}\n"
        f"policy={policy_field}\n"
        f"reason={probe.reason}\n"
        "Choose next action."
    )
    ask_id = state.create_ask(
        run_id,
        question,
        options,
        nonce=nonce,
        expires_at=current + 3600,
    )
    action_id = state.record_action(
        run_id=run_id,
        action_type="dual_agent_validation_failure",
        requested_by="dual_agent_gate",
        payload={
            "task_id": task_id,
            "gate": gate,
            "ask_id": ask_id,
            "nonce": nonce,
            "options": options,
            "policy_field": policy_field,
            "probe_id": probe.probe_id,
            "probe_reason": probe.reason,
            "probe_details": probe.details,
            "escalation_type": "validation_failure",
        },
        status="paused_for_human",
    )
    await notifier.send_approval_prompt(
        ask_id=ask_id,
        question=question,
        options=options,
        nonce=nonce,
    )
    return ValidationEscalation(
        status="validation_failure",
        action_id=action_id,
        ask_id=ask_id,
        nonce=nonce,
        policy_field=policy_field,
        probe=probe,
    )


def pending_deadlock_action_for_ask(state: State, ask_id: int):
    rows = state._conn.execute(
        """SELECT * FROM actions
           WHERE action_type='dual_agent_gate_deadlock'
             AND status='paused_for_human'
           ORDER BY id DESC"""
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if int(payload.get("ask_id") or 0) == int(ask_id):
            return row
    return None


def pending_validation_action_for_ask(state: State, ask_id: int):
    rows = state._conn.execute(
        """SELECT * FROM actions
           WHERE action_type='dual_agent_validation_failure'
             AND status='paused_for_human'
           ORDER BY id DESC"""
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if int(payload.get("ask_id") or 0) == int(ask_id):
            return row
    return None


def resolve_deadlock_escalation(
    *,
    state: State,
    ask_id: int,
    answer: str,
    nonce: str | None,
    action_row: Any,
) -> dict[str, Any]:
    ok = state.answer_ask(ask_id, answer, nonce=nonce)
    if not ok:
        refreshed = state.get_ask(ask_id)
        if refreshed is not None and refreshed["status"] == "expired":
            state.complete_action(action_row["id"], "approval_expired", {"answer": answer})
        return {"status": "rejected", "reason": "invalid_or_expired_approval"}

    normalized = answer.strip().lower()
    status_by_answer = {
        "pause": "paused",
        "kill": "kill_requested",
        "continue": "continue_requested",
    }
    status = status_by_answer.get(normalized)
    if status is None:
        state.complete_action(action_row["id"], "failed", {
            "answer": answer,
            "reason": "unknown_deadlock_answer",
        })
        return {"status": "failed", "reason": "unknown_deadlock_answer"}
    if status == "continue_requested":
        state.mark_action_resume_requested(action_row["id"], payload_update={"answer": answer})
    else:
        state.complete_action(action_row["id"], status, {"answer": answer})
    return {"status": status}


def resolve_validation_escalation(
    *,
    state: State,
    ask_id: int,
    answer: str,
    nonce: str | None,
    action_row: Any,
) -> dict[str, Any]:
    ok = state.answer_ask(ask_id, answer, nonce=nonce)
    if not ok:
        refreshed = state.get_ask(ask_id)
        if refreshed is not None and refreshed["status"] == "expired":
            state.complete_action(action_row["id"], "approval_expired", {"answer": answer})
        return {"status": "rejected", "reason": "invalid_or_expired_approval"}

    normalized = answer.strip().lower()
    status_by_answer = {
        "pause": "paused",
        "retry": "retry_requested",
        "cancel": "cancelled",
    }
    status = status_by_answer.get(normalized)
    if status is None:
        state.complete_action(action_row["id"], "failed", {
            "answer": answer,
            "reason": "unknown_validation_answer",
        })
        return {"status": "failed", "reason": "unknown_validation_answer"}
    state.complete_action(action_row["id"], status, {"answer": answer})
    return {"status": status}


def claim_resume_signal(
    state: State,
    *,
    run_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    return state.claim_resume_signal(run_id=run_id, task_id=task_id)


def claim_retry_signal(
    state: State,
    *,
    run_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    return state.claim_retry_signal(run_id=run_id, task_id=task_id)


def _handoff_lock_path(spec: DualAgentGateSpec) -> Path:
    return Path(spec.cwd).resolve() / ".handoff" / ".dual-agent.lock"


def _addresses(*values: str) -> tuple[str, ...]:
    return tuple(value for value in values if value)


def _probe_tool_call(
    *,
    name: str,
    probe: ProbeResult,
    event_id: int | None = None,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(base or {})
    payload.update({
        "name": name,
        "status": probe.status,
        "probe_id": probe.probe_id,
        "reason": probe.reason,
    })
    payload.setdefault("args", {"probe_id": probe.probe_id})
    payload.setdefault("result_summary", {
        "probe_id": probe.probe_id,
        "status": probe.status,
        "reason": probe.reason,
    })
    if event_id is not None:
        payload["event_id"] = event_id
    return ensure_tool_call_timing(payload)


def _lead_invocation_args(
    spec: DualAgentGateSpec,
    *,
    attempts: int,
    corrective_retry: bool = False,
) -> dict[str, Any]:
    requested_model = select_lead_model(
        spec.gate,
        quality=spec.quality,
        explicit_model=spec.model,
    )
    return {
        "task_id": spec.task_id,
        "gate": spec.gate,
        "quality": spec.quality,
        "model": requested_model,
        "requested_model": requested_model,
        "model_source": "explicit" if spec.model else f"quality_default:{spec.quality}",
        "explicit_model": spec.model,
        "budget_usd": spec.budget_usd,
        "timeout_s": spec.timeout_s,
        "execution_layer_mode": spec.execution_layer_mode,
        "dynamic_workflow_task_class": spec.dynamic_workflow_task_class,
        "attempt": attempts,
        "corrective_retry": corrective_retry,
        "expected_specialists": list(spec.expected_specialists),
        "expected_decisions": list(spec.expected_decisions),
        "expected_objections": list(spec.expected_objections),
    }


def _lead_invocation_tool_fields(
    lead_result: LeadInvocationResult,
    *,
    attempts: int,
) -> dict[str, Any]:
    return {
        "status": "completed" if lead_result.probe.ok else "failed",
        "attempts": attempts,
        "model": lead_result.model,
        "cost_usd": lead_result.cost_usd,
        "tokens_in": lead_result.tokens_in,
        "tokens_out": lead_result.tokens_out,
        "token_usage": lead_result.token_usage,
        "stdout_bytes": lead_result.stdout_bytes,
        "stderr_bytes": lead_result.stderr_bytes,
        "result_summary": {
            "probe_id": lead_result.probe.probe_id,
            "probe_status": lead_result.probe.status,
            "probe_reason": lead_result.probe.reason,
            "outcome_present": lead_result.outcome is not None,
            "model": lead_result.model,
            "cost_usd": lead_result.cost_usd,
            "tokens_in": lead_result.tokens_in,
            "tokens_out": lead_result.tokens_out,
            "stdout_bytes": lead_result.stdout_bytes,
            "stderr_bytes": lead_result.stderr_bytes,
        },
    }


def _lead_response_tool_calls(
    *,
    lead_result: LeadInvocationResult,
    attempts: int,
    probes: dict[str, ProbeResult],
    tool_calls: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if tool_calls is not None:
        return [ensure_tool_call_timing(call) for call in tool_calls]
    lead_call = ensure_tool_call_timing({
            "name": "invoke_claude_lead",
            "status": "completed" if probes["P2"].ok else "failed",
            "attempts": attempts,
            "model": lead_result.model,
            "cost_usd": lead_result.cost_usd,
            "tokens_in": lead_result.tokens_in,
            "tokens_out": lead_result.tokens_out,
            "token_usage": lead_result.token_usage,
            "stdout_bytes": lead_result.stdout_bytes,
            "stderr_bytes": lead_result.stderr_bytes,
        })
    parent = lead_call.get("tool_call_id")
    return [
        lead_call,
        _probe_tool_call(
            name="evaluate_worker_invocation",
            probe=probes["P2"],
            base={"parent_tool_call_id": parent},
        ),
        _probe_tool_call(
            name="evaluate_outcome_fidelity",
            probe=probes["P3"],
            base={"parent_tool_call_id": parent},
        ),
        _probe_tool_call(
            name="verify_planning_artifact_boundaries",
            probe=probes["P1"],
            base={"parent_tool_call_id": parent},
        ),
    ]


def _acquire_handoff_lock(
    lock_path: Path,
    *,
    spec: DualAgentGateSpec,
    packet_path: Path,
) -> ProbeResult | None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "run_id": spec.run_id,
        "task_id": spec.task_id,
        "gate": spec.gate,
        "pid": os.getpid(),
        "created_at": int(time.time()),
        "handoff_packet_path": str(packet_path),
    }
    for attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            owner = _read_handoff_lock(lock_path)
            reclaim_reason = _handoff_lock_reclaim_reason(owner, spec=spec)
            if reclaim_reason and attempt == 0:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    continue
                except OSError as e:
                    return ProbeResult(
                        "P1",
                        "red",
                        "handoff_lock_error",
                        {
                            "lock_path": str(lock_path),
                            "handoff_packet_path": str(packet_path),
                            "error": str(e),
                            "lock_owner": owner,
                        },
                    )
                continue
            return ProbeResult(
                "P1",
                "red",
                "handoff_lock_held",
                {
                    "lock_path": str(lock_path),
                    "handoff_packet_path": str(packet_path),
                    "lock_owner": owner,
                    "age_s": _handoff_lock_age_s(owner),
                    "reclaimed": False,
                },
            )
        except OSError as e:
            return ProbeResult(
                "P1",
                "red",
                "handoff_lock_error",
                {
                    "lock_path": str(lock_path),
                    "handoff_packet_path": str(packet_path),
                    "error": str(e),
                },
            )
        break
    else:
        return ProbeResult(
            "P1",
            "red",
            "handoff_lock_held",
            {
                "lock_path": str(lock_path),
                "handoff_packet_path": str(packet_path),
                "reclaimed": False,
            },
        )
    try:
        os.write(fd, (json.dumps(metadata, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)
    return None


def _release_handoff_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def _read_handoff_lock(lock_path: Path) -> dict[str, Any]:
    try:
        text = lock_path.read_text(encoding="utf-8")
    except OSError as e:
        return {"read_error": str(e)}
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError as e:
        return {"parse_error": str(e), "raw": text[:500]}
    return payload if isinstance(payload, dict) else {"parse_error": "lock_not_object"}


def _handoff_lock_reclaim_reason(owner: dict[str, Any], *, spec: DualAgentGateSpec) -> str:
    pid = _int_or_none(owner.get("pid"))
    if pid is not None and pid > 0:
        return "" if _pid_alive(pid) else "dead_pid"
    age_s = _handoff_lock_age_s(owner)
    if age_s is not None and age_s > int(spec.timeout_s) + HANDOFF_LOCK_RECLAIM_GRACE_S:
        return "stale_lock_ttl"
    return ""


def _handoff_lock_age_s(owner: dict[str, Any]) -> int | None:
    created_at = _int_or_none(owner.get("created_at"))
    if created_at is None:
        return None
    return max(0, int(time.time()) - created_at)


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def send_stale_paused_digests(
    *,
    state: State,
    notifier: Any,
    stale_after_s: int = 3600,
    now: Callable[[], int] | None = None,
    client: Any | None = None,
    limit: int = 20,
) -> list[int]:
    current = int(now() if now is not None else time.time())
    sent_action_ids: list[int] = []
    rows = state.stale_paused_dual_agent_actions(
        older_than_s=stale_after_s,
        now=current,
        limit=limit,
    )
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        text = _paused_digest_text(row, payload, current)
        kwargs = {"client": client} if client is not None else {}
        await notifier.send_message(text, **kwargs)
        state.mark_paused_digest_sent(row["id"], sent_at=current)
        sent_action_ids.append(int(row["id"]))
    return sent_action_ids


def _lead_request(
    spec: DualAgentGateSpec,
    *,
    packet_path: Path | None = None,
    instruction: str | None = None,
) -> LeadInvocationRequest:
    return LeadInvocationRequest(
        task_id=spec.task_id,
        gate=spec.gate,
        instruction=instruction or spec.instruction,
        cwd=spec.cwd,
        expected_specialists=spec.expected_specialists,
        expected_decisions=spec.expected_decisions,
        expected_objections=spec.expected_objections,
        quality=spec.quality,
        model=spec.model,
        budget_usd=spec.budget_usd,
        timeout_s=spec.timeout_s,
        execution_layer_mode=spec.execution_layer_mode,
        dynamic_workflow_task_class=spec.dynamic_workflow_task_class,
        agentic_lead_policy=spec.agentic_lead_policy,
        min_subagents=spec.min_subagents,
        required_roles=spec.required_roles,
        solo_exception_for_artifact_only_gates=spec.solo_exception_for_artifact_only_gates,
        required_evidence_grade=spec.required_evidence_grade,
        handoff_packet_path=packet_path,
        injected_lesson_block=spec.injected_lesson_block,
        injected_lesson_block_sha256=spec.injected_lesson_block_sha256,
        injected_lesson_ids=spec.injected_lesson_ids,
    )


def _p2_from_lead_result(result: LeadInvocationResult) -> ProbeResult:
    if result.probe.probe_id == "P2":
        return result.probe
    return evaluate_worker_invocation(WorkerInvocationProbeInput(
        exit_code=0,
        output_text=result.transcript,
        captured_bytes=result.stdout_bytes,
        expected_bytes=result.stdout_bytes,
        specialist_names=tuple(s.name for s in result.outcome.specialists) if result.outcome else (),
        decisions=tuple(result.outcome.decisions) if result.outcome else (),
        objections=tuple(result.outcome.objections) if result.outcome else (),
        spawned_by_codex=True,
        orchestration_surface="/lead",
        captured_token_estimate=max(1, len(result.stdout) // 4),
        expected_token_estimate=0,
    ))


async def _maybe_request_validation_escalation(
    result: DualAgentGateResult,
    *,
    spec: DualAgentGateSpec,
    state: State,
    notifier: Any,
) -> ValidationEscalation | None:
    failure = _validation_failure_policy(result.probes)
    if failure is None:
        return None
    policy_field, probe = failure
    if getattr(spec.outcome_validation_policy, policy_field) != "abort_to_operator":
        return None
    return await request_validation_escalation(
        state=state,
        notifier=notifier,
        run_id=spec.run_id,
        task_id=spec.task_id,
        gate=spec.gate,
        policy_field=policy_field,
        probe=probe,
    )


def _validation_failure_policy(
    probes: dict[str, ProbeResult],
) -> tuple[str, ProbeResult] | None:
    p2 = probes.get("P2")
    p3 = probes.get("P3")
    if p2 is not None and not p2.ok:
        if p2.reason == "lead_invocation_timeout":
            return "timeout", p2
        if p2.reason in {"lead_invocation_failed", "claude_json_schema_drift"}:
            return "subprocess_failure", p2
    if p3 is not None and not p3.ok:
        if p3.reason == "outcome_signal_loss":
            return "fidelity_failure", p3
    return None


def _paused_digest_text(row: Any, payload: dict[str, Any], current: int) -> str:
    paused_at = int(row["completed_at"] or row["created_at"] or current)
    age_s = max(0, current - paused_at)
    age_min = age_s // 60
    task_id = payload.get("task_id") or "unknown-task"
    gate = payload.get("gate") or "unknown-gate"
    reason = payload.get("reason") or payload.get("probe_reason") or "paused"
    return (
        f"[{task_id}] Dual-agent gate still paused.\n"
        f"gate={gate}\n"
        f"action={row['action_type']}\n"
        f"age_min={age_min}\n"
        f"reason={reason}"
    )


def _should_retry_outcome(
    probe: ProbeResult,
    policy: OutcomeValidationPolicy,
) -> bool:
    if policy.malformed_outcome != "retry_once_with_corrective_packet":
        return False
    return (
        probe.reason == "missing dual_agent_outcome block"
        or probe.reason.startswith("invalid dual_agent_outcome block")
    )


def _expand_transcript(seed_transcript: str, token_size: int) -> str:
    body = _repeat_to_token_floor(
        "Specialist signal: Planner kept the PRD/TDD scope intact; "
        "Reviewer checked tests and objections; Implementer reported no blocker.\n",
        token_size,
    )
    if "{body}" in seed_transcript:
        return seed_transcript.replace("{body}", body)
    return body + "\n" + seed_transcript


def _repeat_to_token_floor(text: str, token_size: int) -> str:
    target_chars = max(token_size * 4, len(text))
    repeats = (target_chars // len(text)) + 1
    return (text * repeats)[:target_chars]

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
    evaluate_worker_invocation,
)
from .agent_mailbox import (
    AgentMailboxMessage,
    outcome_confidence_report,
    planning_artifact_refs,
)
from .dual_agent_lead import (
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
    verify_planning_artifact_boundaries,
    write_handoff_packet,
)
from .planning_validator import (
    planning_validation_probe,
    required_planning_kinds_for_gate,
    validate_planning_artifacts,
)
from .state import State


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
    lead_skill_path: str | Path | None = None
    outcome_validation_policy: OutcomeValidationPolicy = field(default_factory=OutcomeValidationPolicy)


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
        planning_result = validate_planning_artifacts(
            spec.planning_artifacts,
            required_kinds=required_planning_kinds_for_gate(spec.gate),
            gate=spec.gate,
        )
        if state is not None:
            state.write_event(
                run_id=spec.run_id,
                source="dual_agent",
                kind="dual_agent_planning_validation",
                payload=planning_result.to_event_payload(
                    task_id=spec.task_id,
                    gate=spec.gate,
                ),
            )
        planning_probe = planning_validation_probe(planning_result)
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
                        content="Planning validation blocked the gate before Claude Code /lead was invoked.",
                        confidence=None,
                        artifacts=planning_artifact_refs(spec.planning_artifacts),
                        metadata={
                            "planning_validation": planning_result.to_event_payload(
                                task_id=spec.task_id,
                                gate=spec.gate,
                            ),
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
        if state is not None:
            state.write_event(
                run_id=spec.run_id,
                source="dual_agent",
                kind="dual_agent_interaction_message",
                payload=AgentMailboxMessage(
                    task_id=spec.task_id,
                    gate=spec.gate,
                    sender="codex",
                    recipient="claude_code",
                    message_type="gate_request",
                    content=spec.instruction,
                    artifacts=planning_artifact_refs(spec.planning_artifacts),
                    metadata={
                        "expected_specialists": list(spec.expected_specialists),
                        "expected_decisions": list(spec.expected_decisions),
                        "expected_objections": list(spec.expected_objections),
                    },
                ).to_event_payload(),
            )
        packet_path = write_handoff_packet(
            request,
            planning_artifacts=spec.planning_artifacts,
            lead_skill_path=spec.lead_skill_path,
            outcome_validation_policy=spec.outcome_validation_policy,
        )
        request = _lead_request(spec, packet_path=packet_path)
        lead_result = invoke_claude_lead(request, runner=runner)
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
            lead_result = invoke_claude_lead(corrective, runner=runner)
            attempts = 2

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
                    content=(
                        lead_result.outcome.summary
                        if lead_result.outcome is not None
                        else lead_result.probe.reason
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
                    artifacts=planning_artifact_refs(spec.planning_artifacts),
                    metadata={
                        "probe": lead_result.probe.__dict__,
                        "attempts": attempts,
                        "model": lead_result.model,
                        "cost_usd": lead_result.cost_usd,
                        "outcome": outcome_payload,
                    },
                ).to_event_payload(),
            )

        probes: dict[str, ProbeResult] = {}
        probes["P2"] = _p2_from_lead_result(lead_result)
        probes["P3"] = lead_result.probe
        probes["P1"] = verify_planning_artifact_boundaries(packet_path)
        probes["P_planning"] = planning_probe

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
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return ProbeResult(
            "P1",
            "red",
            "handoff_lock_held",
            {
                "lock_path": str(lock_path),
                "handoff_packet_path": str(packet_path),
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
        handoff_packet_path=packet_path,
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

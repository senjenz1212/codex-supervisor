from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path
from typing import get_args

import pytest

from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI, _gate_result_payload
from supervisor.dual_agent import ProbeResult
from supervisor.dual_agent_runner import DualAgentGateResult
from supervisor.state import SCHEMA, State


def _state(tmp_path) -> State:
    return State(str(tmp_path / "state.db"))


def _insert_event(
    state: State,
    *,
    run_id: str = "run-1",
    kind: str = "dual_agent_gate_round",
    payload: dict | str = None,
    ts: int = 1000,
) -> int:
    if payload is None:
        payload = {
            "task_id": "task-1",
            "gate": "tdd_review",
            "round": {
                "round_index": 1,
                "codex_decision": "accept",
                "claude_decision": "accept",
                "codex_confidence": 0.9,
                "claude_confidence": 0.9,
                "objection": None,
            },
        }
    payload_json = payload if isinstance(payload, str) else json.dumps(payload)
    cur = state._conn.execute(
        "INSERT INTO events(run_id, ts, source, kind, payload_json) VALUES(?, ?, ?, ?, ?)",
        (run_id, ts, "test", kind, payload_json),
    )
    state._conn.commit()
    return int(cur.lastrowid)


def _round_payload(
    *,
    task_id: str = "task-1",
    gate: str = "tdd_review",
    codex_decision: str = "accept",
    claude_decision: str = "accept",
    codex_confidence=0.9,
    claude_confidence=0.9,
    objection: str | None = None,
    round_index: int = 1,
) -> dict:
    return {
        "task_id": task_id,
        "gate": gate,
        "round": {
            "round_index": round_index,
            "codex_decision": codex_decision,
            "claude_decision": claude_decision,
            "codex_confidence": codex_confidence,
            "claude_confidence": claude_confidence,
            "objection": objection,
        },
    }


def _result_payload(
    *,
    task_id: str = "task-1",
    gate: str = "outcome_review",
    status: str = "accepted",
    p2_status: str = "green",
    p3_status: str = "green",
    p2_reason: str = "worker_orchestration_invocation_ok",
    p3_reason: str = "outcome_fidelity_ok",
    handoff_packet_path: str | None = "/tmp/handoff.json",
    escalation: dict | None = None,
) -> dict:
    probes = {
        "P2": ProbeResult("P2", p2_status, p2_reason, {}),
        "P3": ProbeResult("P3", p3_status, p3_reason, {}),
        "P1": ProbeResult("P1", "green", "planning_artifact_boundaries_ok", {}),
    }
    result = DualAgentGateResult(
        task_id=task_id,
        gate=gate,
        status=status,
        probes=probes,
        handoff_packet_path=Path(handoff_packet_path or ""),
        attempts=1,
    )
    payload = _gate_result_payload(result)
    if handoff_packet_path is None:
        payload.pop("handoff_packet_path", None)
    if escalation is not None:
        payload["escalation"] = escalation
    return payload


def _snapshot(state: State, **kwargs):
    from supervisor.agent_interaction_snapshot import get_agent_interaction_snapshot

    defaults = {"run_id": "run-1", "task_id": "task-1", "now_s": 1900}
    defaults.update(kwargs)
    return get_agent_interaction_snapshot(state, **defaults)


def test_state_read_dual_agent_gate_events_filters_and_orders(tmp_path):
    state = _state(tmp_path)
    other_kind = _insert_event(
        state,
        run_id="run-a",
        kind="message",
        payload={"task_id": "task-1"},
        ts=9999,
    )
    first = _insert_event(state, run_id="run-a", kind="dual_agent_gate_result", payload=_result_payload(), ts=3000)
    second = _insert_event(state, run_id="run-a", kind="dual_agent_gate_round", payload=_round_payload(), ts=1000)
    _insert_event(state, run_id="run-b", kind="dual_agent_gate_round", payload=_round_payload(), ts=2000)

    rows = state.read_dual_agent_gate_events("run-a")

    assert [row["event_id"] for row in rows] == [first, second]
    assert other_kind not in [row["event_id"] for row in rows]
    assert set(rows[0].keys()) >= {"event_id", "ts", "kind", "payload_json"}


def test_public_signature_is_keyword_only_and_returns_frozen_dataclasses():
    import supervisor.agent_interaction_snapshot as snapshot

    signature = inspect.signature(snapshot.get_agent_interaction_snapshot)
    assert list(signature.parameters) == ["state", "run_id", "task_id", "now_s"]
    assert signature.parameters["run_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["task_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["now_s"].kind is inspect.Parameter.KEYWORD_ONLY
    for cls in (
        snapshot.AgentInteractionSnapshot,
        snapshot.ConfidencePair,
        snapshot.Blocker,
        snapshot.SnapshotWarning,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen


def test_dto_field_surfaces_and_literal_aliases_are_pinned():
    import supervisor.agent_interaction_snapshot as snapshot

    assert [field.name for field in dataclasses.fields(snapshot.AgentInteractionSnapshot)] == [
        "run_id",
        "task_id",
        "status",
        "latest_event_id",
        "latest_result_event_id",
        "rounds_count",
        "latest_gate",
        "codex_decision",
        "claude_decision",
        "confidence_pair",
        "blocker",
        "liveness",
        "handoff_packet_path",
        "generated_at_s",
        "warnings",
    ]
    assert [field.name for field in dataclasses.fields(snapshot.ConfidencePair)] == ["codex", "claude"]
    assert [field.name for field in dataclasses.fields(snapshot.Blocker)] == ["source", "code", "message"]
    assert [field.name for field in dataclasses.fields(snapshot.SnapshotWarning)] == ["code", "message", "event_id"]
    assert get_args(snapshot.SnapshotStatus) == ("ok", "not_found", "partial")
    assert get_args(snapshot.InteractionLiveness) == ("unknown", "active", "stale", "complete", "blocked")
    assert get_args(snapshot.GateDecision) == ("accept", "revise", "deny", "unknown")


def test_empty_ledger_returns_exact_not_found_defaults(tmp_path):
    from supervisor.agent_interaction_snapshot import (
        AgentInteractionSnapshot,
        ConfidencePair,
        SnapshotWarning,
    )

    state = _state(tmp_path)

    assert _snapshot(state, run_id="missing", task_id="missing", now_s=1234567890) == AgentInteractionSnapshot(
        run_id="missing",
        task_id="missing",
        status="not_found",
        latest_event_id=None,
        latest_result_event_id=None,
        rounds_count=0,
        latest_gate=None,
        codex_decision=None,
        claude_decision=None,
        confidence_pair=ConfidencePair(codex=None, claude=None),
        blocker=None,
        liveness="unknown",
        handoff_packet_path=None,
        generated_at_s=1234567890,
        warnings=[
            SnapshotWarning(
                code="no_dual_agent_events",
                message="No dual-agent gate events found for run_id/task_id.",
                event_id=None,
            )
        ],
    )


def test_default_now_s_uses_time_time_epoch_seconds(tmp_path, monkeypatch):
    import supervisor.agent_interaction_snapshot as snapshot

    monkeypatch.setattr(snapshot.time, "time", lambda: 1234567890.9)
    state = _state(tmp_path)

    result = snapshot.get_agent_interaction_snapshot(state, run_id="missing", task_id="missing")

    assert result.generated_at_s == 1234567890


def test_run_id_match_task_id_mismatch_is_not_found(tmp_path):
    state = _state(tmp_path)
    run_id = "run-1"
    _insert_event(state, run_id=run_id, payload=_round_payload(task_id="other-task"))

    result = _snapshot(state, run_id=run_id, task_id="task-1")

    assert result.status == "not_found"
    assert [warning.code for warning in result.warnings] == ["no_dual_agent_events"]


def test_accepted_three_round_sequence_is_complete_and_latest_gate_is_result_gate(tmp_path):
    state = _state(tmp_path)
    api = CodexSupervisorMcpAPI(None, state)  # type: ignore[arg-type]
    for index, codex, claude, objection in [
        (1, "revise", "revise", "tighten"),
        (2, "revise", "revise", "still vague"),
        (3, "accept", "accept", None),
    ]:
        api.record_gate_round(
            run_id="run-1",
            task_id="task-1",
            gate="tdd_review",
            round_index=index,
            codex_decision=codex,
            claude_decision=claude,
            codex_confidence=0.95,
            claude_confidence=0.95,
            objection=objection,
        )
    result_id = _insert_event(state, kind="dual_agent_gate_result", payload=_result_payload(gate="outcome_review"), ts=2000)

    result = _snapshot(state)

    assert result.status == "ok"
    assert result.liveness == "complete"
    assert result.rounds_count == 3
    assert result.latest_gate == "outcome_review"
    assert result.latest_result_event_id == result_id
    assert result.codex_decision == "accept"
    assert result.claude_decision == "accept"
    assert result.confidence_pair.codex == 0.95
    assert result.confidence_pair.claude == 0.95
    assert result.handoff_packet_path == "/tmp/handoff.json"
    assert result.blocker is None


@pytest.mark.parametrize("probe_id", ["P2", "P3"])
def test_red_probe_beats_accepted_completion_for_p2_and_p3_only(tmp_path, probe_id):
    state = _state(tmp_path)
    _insert_event(state, payload=_round_payload(codex_decision="accept", claude_decision="accept"), ts=1000)
    payload = _result_payload()
    payload["probes"][probe_id]["status"] = "red"
    payload["probes"][probe_id]["reason"] = "lead_invocation_failed"
    _insert_event(state, kind="dual_agent_gate_result", payload=payload, ts=1001)

    result = _snapshot(state)

    assert result.liveness == "blocked"
    assert result.blocker is not None
    assert result.blocker.source == "validation_probe"
    assert result.blocker.code == "lead_invocation_failed"


def test_non_p2_p3_red_probe_is_ignored(tmp_path):
    state = _state(tmp_path)
    _insert_event(state, payload=_round_payload(codex_decision="accept", claude_decision="accept"), ts=1000)
    payload = _result_payload()
    payload["probes"]["P4"] = {"probe_id": "P4", "status": "red", "reason": "ignored", "details": {}}
    _insert_event(state, kind="dual_agent_gate_result", payload=payload, ts=1001)

    result = _snapshot(state)

    assert result.liveness == "complete"
    assert result.blocker is None


def test_escalation_at_payload_escalation_beats_red_probe_and_completion(tmp_path):
    state = _state(tmp_path)
    _insert_event(state, payload=_round_payload(codex_decision="accept", claude_decision="accept"), ts=1000)
    payload = _result_payload(escalation={"type": "deadlock", "reason": "budget exhausted"})
    payload["probes"]["P2"]["status"] = "red"
    payload["probes"]["P2"]["reason"] = "lead_invocation_failed"
    _insert_event(state, kind="dual_agent_gate_result", payload=payload, ts=1001)

    result = _snapshot(state)

    assert result.liveness == "blocked"
    assert result.blocker is not None
    assert result.blocker.source == "result_escalation"
    assert result.blocker.code == "deadlock"
    assert result.blocker.message == "budget exhausted"


def test_blocked_result_without_probe_or_objection_has_exact_fallback_blocker(tmp_path):
    from supervisor.agent_interaction_snapshot import Blocker

    state = _state(tmp_path)
    _insert_event(state, kind="dual_agent_gate_result", payload=_result_payload(status="blocked"), ts=1000)

    result = _snapshot(state)

    assert result.liveness == "blocked"
    assert result.blocker == Blocker(
        source="validation_probe",
        code="blocked_without_probe_reason",
        message="Gate result status blocked without escalation, red probe, or round objection.",
    )


@pytest.mark.parametrize(
    ("codex_decision", "claude_decision"),
    [
        ("deny", "accept"),
        ("accept", "deny"),
        ("deny", "revise"),
        ("revise", "deny"),
        ("deny", "deny"),
    ],
)
def test_deny_without_objection_has_fallback_blocker_for_all_decision_pairs(
    tmp_path,
    codex_decision,
    claude_decision,
):
    state = _state(tmp_path)
    _insert_event(
        state,
        payload=_round_payload(
            codex_decision=codex_decision,
            claude_decision=claude_decision,
            objection=None,
        ),
        ts=1000,
    )

    result = _snapshot(state)

    assert result.liveness == "blocked"
    assert result.blocker is not None
    assert result.blocker.source == "latest_round_objection"
    assert result.blocker.code == "deny_without_objection"
    assert result.blocker.message == "Latest round denied without an objection message."


def test_revise_with_objection_blocks_but_revise_without_objection_uses_injected_freshness(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        payload=_round_payload(codex_decision="revise", claude_decision="accept", objection="missing test"),
        ts=1000,
    )

    blocked = _snapshot(state, now_s=1900)

    assert blocked.liveness == "blocked"
    assert blocked.blocker is not None
    assert blocked.blocker.code == "codex=revise;claude=accept"

    fresh_state = _state(tmp_path / "fresh")
    _insert_event(
        fresh_state,
        payload=_round_payload(codex_decision="revise", claude_decision="accept", objection=None),
        ts=1000,
    )

    fresh = _snapshot(fresh_state, now_s=1900)

    assert fresh.liveness == "active"
    assert fresh.blocker is None


@pytest.mark.parametrize(
    ("setup", "expected_source", "expected_code"),
    [
        ("escalation_red_blocked_objection", "result_escalation", "deadlock"),
        ("red_blocked_objection", "validation_probe", "lead_invocation_failed"),
        ("blocked_objection", "latest_round_objection", "codex=revise;claude=accept"),
        ("blocked_only", "validation_probe", "blocked_without_probe_reason"),
        ("deny_no_result", "latest_round_objection", "deny_without_objection"),
    ],
)
def test_blocker_precedence_matrix_with_simultaneous_triggers(tmp_path, setup, expected_source, expected_code):
    state = _state(tmp_path)
    if setup in {"escalation_red_blocked_objection", "red_blocked_objection", "blocked_objection"}:
        _insert_event(
            state,
            payload=_round_payload(codex_decision="revise", claude_decision="accept", objection="round objection"),
            ts=1000,
        )
    elif setup == "deny_no_result":
        _insert_event(state, payload=_round_payload(codex_decision="deny", claude_decision="accept"), ts=1000)

    if setup != "deny_no_result":
        payload = _result_payload(status="blocked")
        if setup in {"escalation_red_blocked_objection", "red_blocked_objection"}:
            payload["probes"]["P2"]["status"] = "red"
            payload["probes"]["P2"]["reason"] = "lead_invocation_failed"
        if setup == "escalation_red_blocked_objection":
            payload["escalation"] = {"type": "deadlock", "reason": "budget exhausted"}
        _insert_event(state, kind="dual_agent_gate_result", payload=payload, ts=1001)

    result = _snapshot(state)

    assert result.liveness == "blocked"
    assert result.blocker is not None
    assert result.blocker.source == expected_source
    assert result.blocker.code == expected_code


@pytest.mark.parametrize(("now_s", "expected"), [(1900, "active"), (1901, "stale")])
def test_stale_and_active_round_use_events_ts_epoch_seconds_boundary(tmp_path, now_s, expected):
    state = _state(tmp_path)
    _insert_event(state, payload=_round_payload(codex_decision="accept", claude_decision="revise"), ts=1000)

    result = _snapshot(state, now_s=now_s)

    assert "ts            INTEGER NOT NULL" in SCHEMA
    assert result.liveness == expected


def test_corrupt_and_unexpected_events_are_skipped_with_partial_status_even_when_latest_valid(tmp_path):
    state = _state(tmp_path)
    corrupt_id = _insert_event(state, payload="{bad-json", ts=1000)
    unexpected_id = _insert_event(state, payload={"task_id": "task-1", "gate": "tdd_review"}, ts=1001)
    valid_id = _insert_event(state, payload=_round_payload(), ts=1002)

    result = _snapshot(state)

    assert result.status == "partial"
    assert result.rounds_count == 1
    assert result.latest_event_id == valid_id
    warnings = {(warning.code, warning.event_id) for warning in result.warnings}
    assert ("corrupt_event_skipped", corrupt_id) in warnings
    assert ("unexpected_event_shape", unexpected_id) in warnings


def test_all_corrupt_relevant_rows_returns_partial_unknown_with_latest_event_id_none(tmp_path):
    state = _state(tmp_path)
    _insert_event(state, payload="{bad-json", ts=1000)
    _insert_event(state, payload={"task_id": "task-1", "gate": "tdd_review"}, ts=1001)

    result = _snapshot(state)

    assert result.status == "partial"
    assert result.liveness == "unknown"
    assert result.latest_event_id is None
    assert result.latest_result_event_id is None
    assert result.rounds_count == 0
    assert {warning.code for warning in result.warnings} == {"corrupt_event_skipped", "unexpected_event_shape"}


def test_unknown_decision_aliases_and_confidence_boundaries(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        payload=_round_payload(
            codex_decision="needs_revision",
            claude_decision="wat",
            codex_confidence=0.0,
            claude_confidence=1.0,
        ),
        ts=1000,
    )
    _insert_event(
        state,
        payload=_round_payload(
            codex_decision="accept",
            claude_decision="accept",
            codex_confidence=-0.01,
            claude_confidence="bad",
            round_index=2,
        ),
        ts=1001,
    )

    result = _snapshot(state)

    assert result.codex_decision == "accept"
    assert result.claude_decision == "accept"
    assert result.confidence_pair.codex is None
    assert result.confidence_pair.claude is None
    assert "unknown_decision_value" in {warning.code for warning in result.warnings}
    assert "invalid_confidence" in {warning.code for warning in result.warnings}


def test_multiple_result_events_event_id_beats_ts_order(tmp_path):
    state = _state(tmp_path)
    _insert_event(state, kind="dual_agent_gate_result", payload=_result_payload(status="accepted"), ts=9999)
    blocked_id = _insert_event(state, kind="dual_agent_gate_result", payload=_result_payload(status="blocked"), ts=1000)

    result = _snapshot(state)

    assert result.latest_result_event_id == blocked_id
    assert result.liveness == "blocked"


def test_missing_handoff_packet_path_warns_for_accepted_result(tmp_path):
    state = _state(tmp_path)
    _insert_event(state, kind="dual_agent_gate_result", payload=_result_payload(handoff_packet_path=None), ts=1000)

    result = _snapshot(state)

    assert result.handoff_packet_path is None
    assert "missing_handoff_packet_path" in {warning.code for warning in result.warnings}


def test_valid_result_missing_gate_is_unexpected_event_shape(tmp_path):
    state = _state(tmp_path)
    payload = _result_payload()
    payload.pop("gate")
    event_id = _insert_event(state, kind="dual_agent_gate_result", payload=payload, ts=1000)

    result = _snapshot(state)

    assert result.status == "partial"
    assert result.liveness == "unknown"
    assert result.latest_result_event_id is None
    assert [(warning.code, warning.event_id) for warning in result.warnings] == [
        ("unexpected_event_shape", event_id)
    ]


def test_read_only_runtime_guard_matches_static_for_state_write_methods(tmp_path, monkeypatch):
    state = _state(tmp_path)
    _insert_event(state, payload=_round_payload(), ts=1000)

    for name in [
        "write_event",
        "record_action",
        "complete_action",
        "mark_action_resume_requested",
        "claim_resume_signal",
    ]:
        if hasattr(State, name):
            monkeypatch.setattr(State, name, lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError(name)))

    result = _snapshot(state)

    assert result.status == "ok"


def test_static_ast_guard_forbidden_calls_and_imports():
    path = Path("supervisor/agent_interaction_snapshot.py")
    tree = ast.parse(path.read_text())
    forbidden = {
        "write_event",
        "record_action",
        "complete_action",
        "mark_action_resume_requested",
        "claim_resume_signal",
        "request_deadlock_escalation",
        "request_validation_escalation",
        "resolve_deadlock_escalation",
        "resolve_validation_escalation",
        "TelegramNotifier",
        "send_approval_prompt",
        "send_message",
        "sync_desktop_status",
        "inject_status",
        "inject_items",
    }
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)

    assert not (names & forbidden)
    assert not [name for name in names if name.startswith(("request_", "resolve_")) and "escalation" in name]


def test_schema_parity_against_real_dual_agent_writers_field_to_behavior(tmp_path):
    state = _state(tmp_path)
    api = CodexSupervisorMcpAPI(None, state)  # type: ignore[arg-type]
    api.record_gate_round(
        run_id="run-1",
        task_id="task-1",
        gate="tdd_review",
        round_index=1,
        codex_decision="accept",
        claude_decision="accept",
        codex_confidence=1.0,
        claude_confidence=0.0,
        objection=None,
    )
    red_payload = _result_payload()
    red_payload["probes"]["P2"]["status"] = "red"
    red_payload["probes"]["P2"]["reason"] = "lead_invocation_failed"
    _insert_event(state, kind="dual_agent_gate_result", payload=red_payload, ts=1001)

    red_result = _snapshot(state)

    assert red_result.codex_decision == "accept"
    assert red_result.claude_decision == "accept"
    assert red_result.confidence_pair.codex == 1.0
    assert red_result.confidence_pair.claude == 0.0
    assert red_result.blocker is not None
    assert red_result.blocker.source == "validation_probe"
    assert red_result.blocker.code == "lead_invocation_failed"

    green_state = _state(tmp_path / "green")
    green_api = CodexSupervisorMcpAPI(None, green_state)  # type: ignore[arg-type]
    green_api.record_gate_round(
        run_id="run-1",
        task_id="task-1",
        gate="tdd_review",
        round_index=1,
        codex_decision="accept",
        claude_decision="accept",
        codex_confidence=1.0,
        claude_confidence=1.0,
        objection=None,
    )
    _insert_event(green_state, kind="dual_agent_gate_result", payload=_result_payload(), ts=1001)

    green_result = _snapshot(green_state)

    assert green_result.liveness == "complete"
    assert green_result.handoff_packet_path == "/tmp/handoff.json"

    escalation_state = _state(tmp_path / "escalation")
    _insert_event(escalation_state, payload=_round_payload(codex_decision="accept", claude_decision="accept"), ts=1000)
    _insert_event(
        escalation_state,
        kind="dual_agent_gate_result",
        payload=_result_payload(escalation={"type": "deadlock", "reason": "budget exhausted"}),
        ts=1001,
    )

    escalation_result = _snapshot(escalation_state)

    assert escalation_result.blocker is not None
    assert escalation_result.blocker.source == "result_escalation"


def test_no_desktop_repaint_claim_or_import():
    source = Path("supervisor/agent_interaction_snapshot.py").read_text()
    for forbidden in [
        "desktop_gui_control",
        "desktop_status_sync",
        "app_server",
        "progress sync",
        "inject_status",
    ]:
        assert forbidden not in source

    doc = Path("docs/testing/agent-interaction-snapshot.md")
    text = doc.read_text()
    assert "ledger snapshot" in text
    assert "live GUI repaint" not in text

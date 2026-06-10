from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI, _workflow_lesson_snapshot
from supervisor.config import Config
from supervisor.dual_agent_lead import LeadInvocationRequest, build_handoff_packet
from supervisor.lessons import (
    LESSON_BLOCK_HEADER,
    build_lesson_injection,
    record_lessons_for_run,
)
from supervisor.state import State


def _cfg(tmp_path: Path) -> Config:
    return Config(**{
        "target": {
            "kind": "codex",
            "codex": {
                "sessions_root": str(tmp_path / "sessions"),
                "cli_command": "codex",
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "42"},
    })


def test_failed_run_writes_supervisor_lesson_record(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.write_event(
        run_id="failed-run",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": "task-1",
            "gate": "execution",
            "status": "blocked",
            "probes": {
                "P11": {
                    "probe_id": "P11",
                    "status": "red",
                    "reason": "claim_without_receipts",
                },
            },
        },
    )

    first = record_lessons_for_run(
        state,
        run_id="failed-run",
        task_id="task-1",
        task_class="large",
    )
    second = record_lessons_for_run(
        state,
        run_id="failed-run",
        task_id="task-1",
        task_class="large",
    )

    lessons = state.list_supervisor_lessons()
    assert len(lessons) == 1
    assert first[0]["created"] is True
    assert second[0]["created"] is False
    assert lessons[0]["task_class"] == "large"
    assert lessons[0]["gate"] == "execution"
    assert lessons[0]["taxonomy_code"] == "FM-3.2"
    assert lessons[0]["source_run_id"] == "failed-run"
    events = state.read_events_since("failed-run", after_event_id=0, limit=20)
    assert [event["kind"] for event in events].count("supervisor_lesson_recorded") == 1


def test_matching_future_gate_injects_lesson_and_records_hash(tmp_path):
    state = State(str(tmp_path / "state.db"))
    lesson, created = state.record_supervisor_lesson(
        task_class="large",
        gate="execution",
        taxonomy_code="FM-3.2",
        root_cause="No or incomplete verification",
        remediation="Verify the claim with supervisor-generated receipts before reporting acceptance.",
        source_run_id="source-run",
        created_at=10,
    )
    assert created is True
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    kwargs = api._workflow_gate_start_kwargs(
        run_id="future-run",
        task_id="task-2",
        gate="execution",
        intent="Implement the feature.",
        corrective_context="",
        lesson_task_class="large",
        round_index=1,
    )

    assert LESSON_BLOCK_HEADER in kwargs["instruction"]
    assert lesson["lesson_id"] in kwargs["injected_lesson_ids"]
    assert kwargs["injected_lesson_block_sha256"] == sha256(
        kwargs["injected_lesson_block"].encode("utf-8")
    ).hexdigest()
    events = state.read_events_since("future-run", after_event_id=0, limit=10)
    [event] = [item for item in events if item["kind"] == "supervisor_lesson_injection"]
    payload = event["payload"]
    assert payload["block_sha256"] == kwargs["injected_lesson_block_sha256"]
    assert payload["advisory_only"] is True
    assert payload["gate_authority"] == "unchanged"


def test_workflow_lesson_snapshot_pins_gate_block_hashes(tmp_path):
    state = State(str(tmp_path / "state.db"))
    lesson, _created = state.record_supervisor_lesson(
        task_class="large",
        gate="execution",
        taxonomy_code="FM-3.2",
        root_cause="No or incomplete verification",
        remediation="Verify the claim with supervisor-generated receipts before reporting acceptance.",
        source_run_id="source-run",
        created_at=10,
    )

    snapshot = _workflow_lesson_snapshot(
        state,
        lesson_task_class="large",
        route_gates=("execution", "outcome_review"),
    )

    injection = snapshot["gates"]["execution"]
    assert snapshot["schema_version"] == "supervisor-lesson-snapshot/v1"
    assert injection["lesson_ids"] == [lesson["lesson_id"]]
    assert injection["block_sha256"] == sha256(injection["block"].encode("utf-8")).hexdigest()
    assert "outcome_review" not in snapshot["gates"]


def test_non_matching_task_class_or_gate_does_not_inject(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.record_supervisor_lesson(
        task_class="large",
        gate="tdd_review",
        taxonomy_code="FM-1.1",
        root_cause="Disobey task specification",
        remediation="Repair planning before invoking the lead again.",
        source_run_id="source-run",
        created_at=10,
    )
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    kwargs = api._workflow_gate_start_kwargs(
        run_id="future-run",
        task_id="task-2",
        gate="execution",
        intent="Implement the feature.",
        corrective_context="",
        lesson_task_class="small",
        round_index=1,
    )

    assert LESSON_BLOCK_HEADER not in kwargs["instruction"]
    assert kwargs["injected_lesson_ids"] == []
    assert [
        event for event in state.read_events_since("future-run", after_event_id=0, limit=10)
        if event["kind"] == "supervisor_lesson_injection"
    ] == []


def test_lesson_injection_is_advisory_and_does_not_emit_gate_result(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.record_supervisor_lesson(
        task_class="large",
        gate="execution",
        taxonomy_code="FM-3.2",
        root_cause="No or incomplete verification",
        remediation="Verify runtime receipts before claiming success.",
        source_run_id="source-run",
        created_at=10,
    )
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    kwargs = api._workflow_gate_start_kwargs(
        run_id="future-run",
        task_id="task-2",
        gate="execution",
        intent="Implement the feature.",
        corrective_context="",
        lesson_task_class="large",
        round_index=1,
    )

    assert kwargs["injected_lesson_ids"]
    events = state.read_events_since("future-run", after_event_id=0, limit=10)
    kinds = [event["kind"] for event in events]
    assert "supervisor_lesson_injection" in kinds
    assert "dual_agent_gate_result" not in kinds
    [injection] = [event for event in events if event["kind"] == "supervisor_lesson_injection"]
    assert injection["payload"]["advisory_only"] is True
    assert injection["payload"]["gate_authority"] == "unchanged"


def test_injection_hash_is_stable_and_handoff_reconstructs_block(tmp_path):
    state = State(str(tmp_path / "state.db"))
    lesson, _created = state.record_supervisor_lesson(
        task_class="large",
        gate="outcome_review",
        taxonomy_code="FM-2.4",
        root_cause="Information withholding",
        remediation="Address independent reviewer objections with concrete evidence references.",
        source_run_id="source-run",
        created_at=20,
    )
    first = build_lesson_injection([lesson])
    second = build_lesson_injection([dict(reversed(list(lesson.items())))])
    assert first["block"] == second["block"]
    assert first["block_sha256"] == second["block_sha256"]

    request = LeadInvocationRequest(
        task_id="task-3",
        gate="outcome_review",
        instruction="Review outcome.\n\n" + first["block"],
        cwd=tmp_path,
        injected_lesson_block=first["block"],
        injected_lesson_block_sha256=first["block_sha256"],
        injected_lesson_ids=tuple(first["lesson_ids"]),
    )
    packet = build_handoff_packet(request)

    assert packet.injected_lesson_block == first["block"]
    assert packet.injected_lesson_block_sha256 == first["block_sha256"]
    assert packet.injected_lesson_ids == [lesson["lesson_id"]]
    assert sha256(packet.injected_lesson_block.encode("utf-8")).hexdigest() == first["block_sha256"]


def test_reviewer_disagreement_and_sequence_failures_produce_lessons(tmp_path):
    state = State(str(tmp_path / "state.db"))
    handoff_payload = {
        "task_id": "task-4",
        "gate": "implementation_plan",
        "status": "accepted",
        "required_probes": [],
        "handoff_packet_sha256": "same-handoff",
    }
    state.write_event(
        run_id="sequence-run",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=handoff_payload,
    )
    state.write_event(
        run_id="sequence-run",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=handoff_payload,
    )
    state.write_event(
        run_id="sequence-run",
        source="dual_agent",
        kind="independent_reviewer_review",
        payload={
            "task_id": "task-4",
            "gate": "implementation_plan",
            "independent_reviewer_panel_decision": {
                "decision": "revise",
                "reason": "missing evidence",
            },
        },
    )

    record_lessons_for_run(
        state,
        run_id="sequence-run",
        task_id="task-4",
        task_class="large",
    )

    codes = {lesson["taxonomy_code"] for lesson in state.list_supervisor_lessons()}
    assert {"FM-1.3", "FM-2.4"} <= codes


def test_drift_probe_cohort_failure_produces_lesson(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.write_event(
        run_id="drift-run",
        source="drift",
        kind="drift_probe_cohort_result",
        payload={
            "task_id": "task-5",
            "gate": "outcome_review",
            "status": "failed",
            "probe_cohort": {"name": "runtime-native-evidence"},
        },
    )

    recorded = record_lessons_for_run(
        state,
        run_id="drift-run",
        task_id="task-5",
        task_class="large",
    )

    assert len(recorded) == 1
    assert recorded[0]["gate"] == "outcome_review"
    assert recorded[0]["taxonomy_code"] == "drift_probe_cohort_failure"
    assert "deterministic probe" in recorded[0]["remediation"]

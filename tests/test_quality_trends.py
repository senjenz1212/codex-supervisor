from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from supervisor.quality_trends import (
    query_quality_trends,
    record_quality_trends_for_run,
    run_sampled_p11_false_accept_audit,
)
from supervisor.state import State


def _write_event(
    state: State,
    *,
    run_id: str = "trend-run",
    kind: str,
    payload: dict,
    ts: int,
) -> int:
    event_id = state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind=kind,
        payload=payload,
    )
    state._conn.execute(
        "UPDATE events SET ts=? WHERE run_id=? AND event_id=?",
        (ts, run_id, event_id),
    )
    state._conn.commit()
    return event_id


def _gate_result(
    *,
    gate: str,
    status: str,
    attempts: int = 1,
    changed_files: list[str] | None = None,
) -> dict:
    return {
        "task_id": "trend-task",
        "gate": gate,
        "status": status,
        "supervisor_final_status": status,
        "claude_gate_status": status,
        "attempts": attempts,
        "handoff_packet_path": "",
        "probes": {},
        "outcome": {
            "decision": "accept" if status == "accepted" else "revise",
            "changed_files": changed_files or [],
            "tests": [],
            "summary": "done",
        },
    }


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=path, check=True, capture_output=True, text=True)


def test_quality_trends_record_run_computes_first_pass_revision_rounds_and_time_to_accept(tmp_path):
    state = State(str(tmp_path / "state.db"))
    _write_event(
        state,
        kind="dual_agent_workflow_route",
        ts=100,
        payload={
            "task_id": "trend-task",
            "run_id": "trend-run",
            "lesson_task_class": "source_change",
        },
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=110,
        payload=_gate_result(gate="tdd_review", status="accepted", attempts=1),
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=120,
        payload=_gate_result(gate="implementation_plan", status="blocked", attempts=1),
    )
    accepted_id = _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=180,
        payload=_gate_result(gate="implementation_plan", status="accepted", attempts=2),
    )

    rows = record_quality_trends_for_run(state, run_id="trend-run")

    by_gate = {row["gate"]: row for row in rows}
    assert by_gate["tdd_review"]["first_pass_accepted"] is True
    assert by_gate["tdd_review"]["revision_rounds"] == 0
    plan = by_gate["implementation_plan"]
    assert plan["accepted"] is True
    assert plan["first_pass_accepted"] is False
    assert plan["revision_rounds"] == 2
    assert plan["time_to_accepted_outcome_s"] == pytest.approx(60.0)
    assert plan["details"]["accepted_gate_result_event_id"] == accepted_id

    summary = query_quality_trends(
        state,
        task_class="source_change",
        gate="implementation_plan",
    )
    assert summary == [{
        "task_class": "source_change",
        "gate": "implementation_plan",
        "run_count": 1,
        "accepted_count": 1,
        "acceptance_rate": 1.0,
        "first_pass_accepted_count": 0,
        "first_pass_acceptance_rate": 0.0,
        "avg_revision_rounds": 2.0,
        "avg_time_to_accepted_outcome_s": 60.0,
        "p11_audit_sample_size": 0,
        "false_accept_count": 0,
        "false_accept_denominator": 0,
        "false_accept_rate": 0.0,
    }]


def test_quality_trends_uses_final_gate_acceptance_after_reviewer_override(tmp_path):
    state = State(str(tmp_path / "state.db"))
    _write_event(
        state,
        kind="dual_agent_workflow_route",
        ts=100,
        payload={
            "task_id": "trend-task",
            "run_id": "trend-run",
            "lesson_task_class": "source_change",
        },
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=110,
        payload=_gate_result(gate="tdd_review", status="accepted", attempts=1),
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=140,
        payload={
            **_gate_result(gate="tdd_review", status="blocked", attempts=1),
            "supervisor_final_status": None,
            "claude_gate_status": None,
            "codex_decision": "revise",
            "claude_decision": "accept",
        },
    )
    final_accept_id = _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=200,
        payload=_gate_result(gate="tdd_review", status="accepted", attempts=1),
    )

    rows = record_quality_trends_for_run(state, run_id="trend-run")

    row = rows[0]
    assert row["accepted"] is True
    assert row["first_pass_accepted"] is False
    assert row["revision_rounds"] == 1
    assert row["time_to_accepted_outcome_s"] == pytest.approx(90.0)
    assert row["details"]["accepted_gate_result_event_id"] == final_accept_id


def test_quality_trends_does_not_keep_stale_acceptance_for_final_block(tmp_path):
    state = State(str(tmp_path / "state.db"))
    _write_event(
        state,
        kind="dual_agent_workflow_route",
        ts=100,
        payload={
            "task_id": "trend-task",
            "run_id": "trend-run",
            "lesson_task_class": "source_change",
        },
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=110,
        payload=_gate_result(gate="outcome_review", status="accepted", attempts=1),
    )
    final_block_id = _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=200,
        payload={
            **_gate_result(gate="outcome_review", status="blocked", attempts=1),
            "supervisor_final_status": None,
            "claude_gate_status": None,
            "codex_decision": "revise",
            "claude_decision": "accept",
        },
    )

    rows = record_quality_trends_for_run(state, run_id="trend-run")

    row = rows[0]
    assert row["accepted"] is False
    assert row["first_pass_accepted"] is False
    assert row["revision_rounds"] == 2
    assert row["time_to_accepted_outcome_s"] is None
    assert row["details"]["accepted_gate_result_event_id"] is None
    assert row["details"]["gate_result_event_ids"][-1] == final_block_id


def test_quality_trends_sampled_p11_audit_catches_false_accept(tmp_path):
    _init_git_repo(tmp_path)
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow(
        run_id="trend-run",
        task_id="trend-task",
        cwd=str(tmp_path),
        intent="audit accepted deliverables",
        current_gate="outcome_review",
        status="accepted",
        max_rounds_per_gate=2,
        user_facing=False,
    )
    _write_event(
        state,
        kind="dual_agent_workflow_route",
        ts=100,
        payload={
            "task_id": "trend-task",
            "run_id": "trend-run",
            "lesson_task_class": "source_change",
            "cwd": str(tmp_path),
        },
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=110,
        payload=_gate_result(
            gate="outcome_review",
            status="accepted",
            attempts=1,
            changed_files=["supervisor/missing.py"],
        ),
    )

    audit = run_sampled_p11_false_accept_audit(
        state,
        run_id="trend-run",
        sample_size=1,
        test_timeout_s=1,
    )

    assert audit["false_accept_count"] == 1
    assert audit["false_accept_denominator"] == 1
    assert audit["false_accept_rate"] == 1.0
    assert audit["audited"][0]["false_accept"] is True
    assert "runtime_deliverable_missing" in audit["audited"][0]["failures"]
    summary = query_quality_trends(state, task_class="source_change", gate="outcome_review")
    assert summary[0]["false_accept_count"] == 1
    assert summary[0]["false_accept_denominator"] == 1
    assert summary[0]["false_accept_rate"] == 1.0


def test_quality_trends_query_filters_by_task_class_and_gate_without_writes(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_quality_trend_row(
        run_id="run-a",
        task_id="task-a",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=10.0,
    )
    state.upsert_quality_trend_row(
        run_id="run-b",
        task_id="task-b",
        task_class="docs",
        gate="execution",
        accepted=False,
        first_pass_accepted=False,
        revision_rounds=2,
        time_to_accepted_outcome_s=None,
    )
    before_rows = state.count_quality_trend_rows()
    before_events = state._conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]

    rows = query_quality_trends(state, task_class="source_change", gate="execution")

    assert len(rows) == 1
    assert rows[0]["task_class"] == "source_change"
    assert rows[0]["gate"] == "execution"
    assert state.count_quality_trend_rows() == before_rows
    after_events = state._conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
    assert after_events == before_events


def test_quality_trends_cli_query_is_read_only_json(tmp_path):
    state_path = tmp_path / "state.db"
    state = State(str(state_path))
    state.upsert_quality_trend_row(
        run_id="run-a",
        task_id="task-a",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=10.0,
    )
    before_rows = state.count_quality_trend_rows()

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_supervisor_trend_metrics.py",
            "--state-path",
            str(state_path),
            "query",
            "--task-class",
            "source_change",
            "--gate",
            "execution",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["read_only"] is True
    assert payload["rows"][0]["task_class"] == "source_change"
    assert state.count_quality_trend_rows() == before_rows


def test_quality_trends_metrics_do_not_advance_or_block_gates(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow(
        run_id="trend-run",
        task_id="trend-task",
        cwd=str(tmp_path),
        intent="record observational metrics only",
        current_gate="execution",
        status="running",
        max_rounds_per_gate=2,
        user_facing=False,
    )
    _write_event(
        state,
        kind="dual_agent_workflow_route",
        ts=100,
        payload={
            "task_id": "trend-task",
            "run_id": "trend-run",
            "lesson_task_class": "source_change",
        },
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=110,
        payload=_gate_result(gate="execution", status="accepted", attempts=1),
    )
    state.record_dual_agent_workflow_step(
        run_id="trend-run",
        task_id="trend-task",
        gate="execution",
        status="accepted",
        attempt_count=1,
        latest_event_id=1,
    )
    gate_events_before = [
        row["event_id"]
        for row in state.read_dual_agent_gate_events("trend-run")
        if row["kind"] == "dual_agent_gate_result"
    ]
    workflow_before = dict(state.get_dual_agent_workflow(
        run_id="trend-run",
        task_id="trend-task",
    ))
    steps_before = [
        dict(row)
        for row in state.list_dual_agent_workflow_steps(
            run_id="trend-run",
            task_id="trend-task",
        )
    ]

    record_quality_trends_for_run(state, run_id="trend-run")
    query_quality_trends(state, task_class="source_change", gate="execution")

    gate_events_after = [
        row["event_id"]
        for row in state.read_dual_agent_gate_events("trend-run")
        if row["kind"] == "dual_agent_gate_result"
    ]
    assert gate_events_after == gate_events_before
    workflow_after = dict(state.get_dual_agent_workflow(
        run_id="trend-run",
        task_id="trend-task",
    ))
    steps_after = [
        dict(row)
        for row in state.list_dual_agent_workflow_steps(
            run_id="trend-run",
            task_id="trend-task",
        )
    ]
    for volatile in ("updated_at",):
        workflow_before.pop(volatile, None)
        workflow_after.pop(volatile, None)
    assert workflow_after == workflow_before
    assert steps_after == steps_before


def test_quality_trends_prefers_supervisor_final_status_over_claude_status(tmp_path):
    state = State(str(tmp_path / "state.db"))
    payload = _gate_result(gate="outcome_review", status="blocked", attempts=1)
    payload["claude_gate_status"] = "accepted"
    payload["supervisor_final_status"] = "blocked"
    _write_event(
        state,
        kind="dual_agent_gate_result",
        ts=100,
        payload=payload,
    )

    rows = record_quality_trends_for_run(state, run_id="trend-run")

    assert rows[0]["accepted"] is False
    assert rows[0]["first_pass_accepted"] is False

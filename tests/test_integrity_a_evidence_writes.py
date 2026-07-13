from __future__ import annotations

import json
from pathlib import Path

from supervisor.state import State


def _state_with_workflow_job(tmp_path: Path, *, job_id: str = "job-integrity") -> State:
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id=job_id,
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
    )
    return state


def test_duplicate_identical_terminal_completion_is_an_idempotent_no_op(tmp_path):
    state = _state_with_workflow_job(tmp_path)
    original = {
        "status": "accepted",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
        "details": {"b": 2, "a": 1},
    }
    canonically_identical = {
        "details": {"a": 1, "b": 2},
        "task_id": "workflow-task",
        "run_id": "workflow-run",
        "status": "accepted",
    }

    state.complete_dual_agent_workflow_job(
        job_id="job-integrity",
        status="accepted",
        terminal_outcome=original,
    )
    cursor = state.latest_event_id("workflow-run")

    state.complete_dual_agent_workflow_job(
        job_id="job-integrity",
        status="accepted",
        terminal_outcome=canonically_identical,
    )

    job = state.get_dual_agent_workflow_job(job_id="job-integrity")
    assert json.loads(job["terminal_outcome_json"]) == canonically_identical
    assert state.read_events_since("workflow-run", after_event_id=cursor, limit=20) == []
    terminal_events = [
        event
        for event in state.read_events_since("workflow-run", after_event_id=0, limit=20)
        if event["kind"] == "dual_agent_workflow_terminal_outcome"
    ]
    assert len(terminal_events) == 1

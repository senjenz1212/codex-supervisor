from __future__ import annotations

import json
import sqlite3

import pytest

from supervisor.evidence_ledger import canonical_json_bytes, sha256_hex
from supervisor.state import State


def test_terminal_completion_event_hashes_the_complete_persisted_record(tmp_path):
    state = State(str(tmp_path / "state.db"))
    result_path = str(tmp_path / "result.json")
    state.upsert_dual_agent_workflow_job(
        job_id="job-terminal-record",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        request_path=str(tmp_path / "request.json"),
        result_path=result_path,
        log_path=str(tmp_path / "worker.log"),
    )
    outcome = {
        "status": "accepted",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
        "evidence": ["receipt"],
    }

    event_id = state.complete_dual_agent_workflow_job(
        job_id="job-terminal-record",
        status="accepted",
        terminal_status="accepted",
        terminal_outcome=outcome,
        returncode=0,
    )

    job = state.get_dual_agent_workflow_job(job_id="job-terminal-record")
    event = state.get_event(run_id="workflow-run", event_id=event_id)
    payload = json.loads(event["payload_json"])
    terminal_record = {
        "job_id": "job-terminal-record",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
        "result_path": result_path,
        "status": "accepted",
        "recovery_point": "terminal",
        "terminal_status": "accepted",
        "terminal_outcome": outcome,
        "terminal_outcome_recorded_at": job["terminal_outcome_recorded_at"],
        "returncode": 0,
        "error": None,
    }
    assert payload["terminal_record"] == terminal_record
    assert payload["terminal_record_sha256"] == sha256_hex(
        canonical_json_bytes(terminal_record)
    )


@pytest.mark.parametrize(
    ("status", "terminal_status", "outcome", "message"),
    (
        (
            "running",
            "running",
            {"status": "running"},
            "terminal status",
        ),
        (
            "accepted",
            "blocked",
            {"status": "accepted"},
            "terminal status mismatch",
        ),
        (
            "accepted",
            "accepted",
            {"status": "blocked"},
            "terminal outcome status mismatch",
        ),
        (
            "accepted",
            "accepted",
            {"status": "accepted", "run_id": "other-run"},
            "terminal outcome run_id mismatch",
        ),
        (
            "accepted",
            "accepted",
            {"status": "accepted", "task_id": "other-task"},
            "terminal outcome task_id mismatch",
        ),
    ),
)
def test_terminal_completion_rejects_inconsistent_identity_or_status(
    tmp_path,
    status,
    terminal_status,
    outcome,
    message,
):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-invalid-completion",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
    )

    with pytest.raises(ValueError, match=message):
        state.complete_dual_agent_workflow_job(
            job_id="job-invalid-completion",
            status=status,
            terminal_status=terminal_status,
            terminal_outcome=outcome,
        )

    job = state.get_dual_agent_workflow_job(job_id="job-invalid-completion")
    assert job["status"] == "running"
    assert job["terminal_outcome_json"] is None
    assert state.read_events_since("workflow-run", after_event_id=0, limit=20) == []


def test_terminal_job_semantics_and_identity_are_frozen_at_api_and_db_boundaries(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    paths = {
        "cwd": str(tmp_path),
        "request_path": str(tmp_path / "request.json"),
        "result_path": str(tmp_path / "result.json"),
        "log_path": str(tmp_path / "worker.log"),
    }
    state.upsert_dual_agent_workflow_job(
        job_id="job-frozen",
        run_id="workflow-run",
        task_id="workflow-task",
        status="running",
        **paths,
    )
    outcome = {
        "status": "accepted",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
    }
    state.complete_dual_agent_workflow_job(
        job_id="job-frozen",
        status="accepted",
        terminal_outcome=outcome,
        returncode=0,
    )

    with pytest.raises(RuntimeError, match="terminal workflow job is immutable"):
        state.update_dual_agent_workflow_job(
            job_id="job-frozen",
            status="blocked",
            error="overwrite",
        )
    with pytest.raises(RuntimeError, match="terminal workflow job is immutable"):
        state.upsert_dual_agent_workflow_job(
            job_id="job-frozen",
            run_id="other-run",
            task_id="workflow-task",
            status="blocked",
            returncode=1,
            error="overwrite",
            **paths,
        )
    with pytest.raises(
        sqlite3.IntegrityError,
        match="terminal workflow job fields are immutable",
    ):
        state._conn.execute(
            """UPDATE dual_agent_workflow_jobs
                  SET run_id='other-run', status='blocked'
                WHERE job_id='job-frozen'"""
        )

    job = state.get_dual_agent_workflow_job(job_id="job-frozen")
    assert job["run_id"] == "workflow-run"
    assert job["task_id"] == "workflow-task"
    assert job["status"] == "accepted"
    assert job["terminal_status"] == "accepted"
    assert job["returncode"] == 0
    assert job["error"] is None


def test_terminal_job_helper_apis_reject_semantic_mutation_consistently(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-helper-frozen",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
    )
    state.complete_dual_agent_workflow_job(
        job_id="job-helper-frozen",
        status="accepted",
        terminal_outcome={
            "status": "accepted",
            "run_id": "workflow-run",
            "task_id": "workflow-task",
        },
    )

    with pytest.raises(RuntimeError, match="terminal workflow job is immutable"):
        state.clear_dual_agent_workflow_job_lease(
            job_id="job-helper-frozen",
            error="late dispatcher error",
        )
    with pytest.raises(RuntimeError, match="terminal workflow job is immutable"):
        state.park_dual_agent_workflow_job(
            job_id="job-helper-frozen",
            reason="late park",
        )

    job = state.get_dual_agent_workflow_job(job_id="job-helper-frozen")
    assert job["status"] == "accepted"
    assert job["error"] is None
    assert job["parked_reason"] is None


def test_generic_job_update_cannot_forge_worker_reap_proof(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-no-forged-reap",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        pid=41010,
        worker_pgid=41010,
        worker_started_at=123.5,
        worker_containment_id="containment-1",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
        recovery_point="spawned",
    )

    with pytest.raises(
        RuntimeError,
        match="containment-verified reap API",
    ):
        state.update_dual_agent_workflow_job(
            job_id="job-no-forged-reap",
            worker_reaped_at=200,
        )

    row = state.get_dual_agent_workflow_job(job_id="job-no-forged-reap")
    assert row["worker_reaped_at"] is None
    assert state.read_events_since(
        "workflow-run",
        after_event_id=0,
        limit=20,
    ) == []


def test_exact_duplicate_terminal_completion_is_idempotent(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-idempotent",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
    )
    outcome = {
        "status": "accepted",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
    }
    first_event_id = state.complete_dual_agent_workflow_job(
        job_id="job-idempotent",
        status="accepted",
        terminal_status="accepted",
        terminal_outcome=outcome,
        returncode=0,
    )

    duplicate_event_id = state.complete_dual_agent_workflow_job(
        job_id="job-idempotent",
        status="accepted",
        terminal_status="accepted",
        terminal_outcome=outcome,
        returncode=0,
    )

    assert duplicate_event_id == 0
    events = state.read_events_since(
        "workflow-run",
        after_event_id=0,
        limit=20,
    )
    assert [event["event_id"] for event in events] == [first_event_id]
    assert events[0]["kind"] == "dual_agent_workflow_terminal_outcome"


def test_conflicting_terminal_completion_records_discrepancy_and_preserves_original(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-conflict",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
    )
    original = {
        "status": "accepted",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
        "evidence": ["original"],
    }
    conflicting = {
        "status": "blocked",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
        "evidence": ["conflicting"],
    }
    state.complete_dual_agent_workflow_job(
        job_id="job-conflict",
        status="accepted",
        terminal_outcome=original,
    )
    cursor = state.latest_event_id("workflow-run")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="terminal outcome discrepancy"):
            state.complete_dual_agent_workflow_job(
                job_id="job-conflict",
                status="blocked",
                terminal_status="blocked",
                terminal_outcome=conflicting,
                error="conflicting completion",
            )

    job = state.get_dual_agent_workflow_job(job_id="job-conflict")
    assert job["status"] == "accepted"
    assert job["terminal_status"] == "accepted"
    assert json.loads(job["terminal_outcome_json"]) == original
    [event] = state.read_events_since("workflow-run", after_event_id=cursor, limit=20)
    assert event["kind"] == "dual_agent_workflow_terminal_discrepancy"
    assert len(event["payload"]["conflict_sha256"]) == 64
    assert event["payload"]["job_id"] == "job-conflict"
    assert event["payload"]["original_terminal_outcome"] == original
    assert event["payload"]["conflicting_terminal_outcome"] == conflicting
    assert event["payload"]["original_status"] == "accepted"
    assert event["payload"]["conflicting_status"] == "blocked"


@pytest.mark.parametrize(
    "second_call",
    (
        {"status": "completed"},
        {"status": "accepted", "terminal_status": "completed"},
        {"status": "accepted", "returncode": 1},
        {"status": "accepted", "error": "different terminal error"},
    ),
)
def test_identical_outcome_with_conflicting_terminal_metadata_is_not_idempotent(
    tmp_path,
    second_call,
):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-metadata-conflict",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
    )
    outcome = {
        "status": "accepted",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
    }
    state.complete_dual_agent_workflow_job(
        job_id="job-metadata-conflict",
        status="accepted",
        terminal_status="accepted",
        terminal_outcome=outcome,
        returncode=0,
        error=None,
    )
    cursor = state.latest_event_id("workflow-run")

    with pytest.raises(RuntimeError, match="terminal outcome discrepancy"):
        repeated = {
            "status": "accepted",
            "terminal_status": "accepted",
            "returncode": 0,
            "error": None,
        }
        repeated.update(second_call)
        state.complete_dual_agent_workflow_job(
            job_id="job-metadata-conflict",
            terminal_outcome=outcome,
            **repeated,
        )

    job = state.get_dual_agent_workflow_job(
        job_id="job-metadata-conflict"
    )
    assert job["status"] == "accepted"
    assert job["terminal_status"] == "accepted"
    assert job["returncode"] == 0
    assert job["error"] is None
    [event] = state.read_events_since(
        "workflow-run",
        after_event_id=cursor,
        limit=20,
    )
    assert event["kind"] == "dual_agent_workflow_terminal_discrepancy"


def test_spawned_job_cannot_publish_terminal_before_worker_reap(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-reap-required",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        pid=12345,
        worker_pgid=12345,
        worker_started_at=100.0,
        worker_containment_id="containment-1",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
        recovery_point="spawned",
    )

    with pytest.raises(
        RuntimeError,
        match="worker reap must be recorded atomically",
    ):
        state.complete_dual_agent_workflow_job(
            job_id="job-reap-required",
            status="accepted",
            terminal_outcome={
                "status": "accepted",
                "run_id": "workflow-run",
                "task_id": "workflow-task",
            },
        )

    row = state.get_dual_agent_workflow_job(job_id="job-reap-required")
    assert row["worker_reaped_at"] is None
    assert row["terminal_outcome_json"] is None


def test_worker_reap_and_terminal_publication_are_one_transaction(
    monkeypatch,
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-atomic-reap",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        pid=12345,
        worker_pgid=12345,
        worker_started_at=100.0,
        worker_containment_id="containment-1",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
        recovery_point="spawned",
    )
    original_insert = state._insert_event_unlocked
    inserted = 0

    def fail_after_reap_event(**kwargs):
        nonlocal inserted
        inserted += 1
        event_id = original_insert(**kwargs)
        if inserted == 2:
            raise RuntimeError("injected terminal event failure")
        return event_id

    monkeypatch.setattr(state, "_insert_event_unlocked", fail_after_reap_event)
    with pytest.raises(RuntimeError, match="injected terminal event failure"):
        state.complete_dual_agent_workflow_job(
            job_id="job-atomic-reap",
            status="accepted",
            terminal_outcome={
                "status": "accepted",
                "run_id": "workflow-run",
                "task_id": "workflow-task",
            },
            worker_reaped_at=200,
            termination={
                "status": "worker_tree_terminated",
                "safe_to_finalize": True,
                "containment_id": "containment-1",
            },
        )

    row = state.get_dual_agent_workflow_job(job_id="job-atomic-reap")
    assert row["worker_reaped_at"] is None
    assert row["terminal_outcome_json"] is None
    assert state.read_events_since(
        "workflow-run",
        after_event_id=0,
        limit=20,
    ) == []


def test_worker_reap_event_precedes_terminal_event(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-reap-order",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        pid=12345,
        worker_pgid=12345,
        worker_started_at=100.0,
        worker_containment_id="containment-1",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
        recovery_point="spawned",
    )

    terminal_event_id = state.complete_dual_agent_workflow_job(
        job_id="job-reap-order",
        status="accepted",
        terminal_outcome={
            "status": "accepted",
            "run_id": "workflow-run",
            "task_id": "workflow-task",
        },
        worker_reaped_at=200,
        termination={
            "status": "worker_tree_terminated",
            "safe_to_finalize": True,
            "containment_id": "containment-1",
        },
    )

    events = state.read_events_since(
        "workflow-run",
        after_event_id=0,
        limit=20,
    )
    assert [event["kind"] for event in events] == [
        "dual_agent_workflow_worker_reaped",
        "dual_agent_workflow_terminal_outcome",
    ]
    assert events[-1]["event_id"] == terminal_event_id
    row = state.get_dual_agent_workflow_job(job_id="job-reap-order")
    assert row["worker_reaped_at"] == 200

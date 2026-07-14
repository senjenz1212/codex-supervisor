"""Regression tests for dispatcher reap-path failure handling."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import supervisor.workflow_job_dispatcher as workflow_job_dispatcher_module
from supervisor.state import State
from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher


def _stub_safe_termination(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_terminate_containment(**kwargs: object) -> dict[str, object]:
        return {
            "status": "worker_already_reaped",
            "safe_to_finalize": True,
            "pid": kwargs["root_pid"],
            "pgid": kwargs.get("expected_process_group_id"),
            "containment_id": kwargs.get("containment_id"),
            "descendant_pids": [],
            "surviving_pids": [],
            "scan_errors": [],
            "containment_kind": "inherited_environment_same_user",
            "root_pid_reused": False,
        }

    monkeypatch.setattr(
        workflow_job_dispatcher_module,
        "terminate_containment",
        fake_terminate_containment,
    )


def _insert_spawned_job(
    state: State,
    tmp_path: Path,
    *,
    job_id: str,
    containment_id: str | None,
    pid: int = 41001,
) -> Path:
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / job_id
    state.upsert_dual_agent_workflow_job(
        job_id=job_id,
        run_id=f"run-{job_id}",
        task_id=f"task-{job_id}",
        cwd=str(tmp_path),
        status="running",
        pid=pid,
        worker_pgid=pid,
        worker_started_at=111.0,
        worker_containment_id=containment_id,
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    state.update_dual_agent_workflow_job(
        job_id=job_id,
        leased_by=f"worker:{pid}",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    return job_dir / "result.json"


def test_malformed_terminal_result_dead_letters_instead_of_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_safe_termination(monkeypatch)
    state = State(str(tmp_path / "state.db"))
    result_path = _insert_spawned_job(
        state,
        tmp_path,
        job_id="poison-result",
        containment_id="containment-poison",
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps({"status": "not_a_terminal_status"}),
        encoding="utf-8",
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        pid_alive=lambda _pid: False,
        process_identity_probe=lambda _pid: None,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    result = dispatcher.reap_stale_leases()

    assert result["failed"] == ["poison-result"]
    row = state.get_dual_agent_workflow_job(job_id="poison-result")
    assert row is not None
    assert row["status"] == "failed"
    assert row["recovery_point"] == "terminal"
    assert "malformed_worker_result" in str(row["error"])


def test_malformed_result_identity_mismatch_dead_letters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_safe_termination(monkeypatch)
    state = State(str(tmp_path / "state.db"))
    result_path = _insert_spawned_job(
        state,
        tmp_path,
        job_id="poison-identity",
        containment_id="containment-poison-identity",
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "job_id": "some-other-job",
                "run_id": "some-other-run",
            }
        ),
        encoding="utf-8",
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        pid_alive=lambda _pid: False,
        process_identity_probe=lambda _pid: None,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    result = dispatcher.reap_stale_leases()

    assert result["failed"] == ["poison-identity"]
    row = state.get_dual_agent_workflow_job(job_id="poison-identity")
    assert row is not None
    assert row["status"] == "failed"
    assert "malformed_worker_result" in str(row["error"])


def test_run_forever_survives_tick_exceptions(tmp_path: Path) -> None:
    state = State(str(tmp_path / "state.db"))
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
    )
    stop = threading.Event()
    calls: list[int] = []

    def poison_reap() -> dict[str, list[str]]:
        calls.append(1)
        if len(calls) >= 3:
            stop.set()
        raise ValueError("poison result file")

    dispatcher.reap_stale_leases = poison_reap  # type: ignore[method-assign]

    dispatcher.run_forever(interval_s=0.01, stop_event=stop)

    assert len(calls) >= 3


def test_cleanup_retry_parks_after_attempt_cap(tmp_path: Path) -> None:
    state = State(str(tmp_path / "state.db"))
    _insert_spawned_job(
        state,
        tmp_path,
        job_id="cleanup-wedge",
        containment_id="containment-wedge",
    )
    clock = {"now": 1000}
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        max_cleanup_retry_attempts=2,
        pid_alive=lambda _pid: True,
        process_identity_probe=lambda _pid: (99999, 111.0),
        now=lambda: clock["now"],
        jitter=lambda _delay: 0,
    )

    for _ in range(5):
        dispatcher.reap_stale_leases()
        row = state.get_dual_agent_workflow_job(job_id="cleanup-wedge")
        assert row is not None
        if row["status"] == "parked":
            break
        clock["now"] += 1000

    row = state.get_dual_agent_workflow_job(job_id="cleanup-wedge")
    assert row is not None
    assert row["status"] == "parked"
    assert str(row["parked_reason"]).startswith(
        "cleanup_retry_attempts_exhausted"
    )
    assert row["leased_by"] is None
    assert (
        state.count_active_dual_agent_workflow_job_leases(now=clock["now"])
        == 0
    )


def test_legacy_spawned_row_without_containment_parks_deterministically(
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    _insert_spawned_job(
        state,
        tmp_path,
        job_id="legacy-spawned",
        containment_id=None,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        pid_alive=lambda _pid: False,
        process_identity_probe=lambda _pid: None,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    result = dispatcher.reap_stale_leases()

    assert result["cleanup_retry_pending"] == []
    row = state.get_dual_agent_workflow_job(job_id="legacy-spawned")
    assert row is not None
    assert row["status"] == "parked"
    assert "worker_containment_identity_missing" in str(row["parked_reason"])
    assert row["leased_by"] is None
    assert state.count_active_dual_agent_workflow_job_leases(now=1000) == 0


def test_terminal_row_without_containment_skips_reap_with_single_event(
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    state._conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, pid, worker_pgid,
             worker_started_at, worker_containment_id, worker_reaped_at,
             request_path, result_path, log_path, recovery_point,
             terminal_status, terminal_outcome_json,
             terminal_outcome_recorded_at, returncode, error,
             created_at, updated_at)
           VALUES(
             'legacy-terminal', 'run-legacy-terminal', 'task-legacy-terminal',
             '.', 'completed', 41001, 41001, 123.5, NULL,
             NULL, 'req', 'res', 'log', 'terminal', 'completed',
             '{"status":"completed"}', 100, 0, NULL, 1, 1
           )"""
    )
    state._conn.commit()
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        pid_alive=lambda _pid: False,
        process_identity_probe=lambda _pid: None,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    first = dispatcher.reap_stale_leases()
    second = dispatcher.reap_stale_leases()

    assert first["reaped"] == []
    assert second["reaped"] == []
    events = state._conn.execute(
        """SELECT payload_json
             FROM events
            WHERE run_id='run-legacy-terminal'
              AND kind='dual_agent_workflow_job'"""
    ).fetchall()
    skip_events = [
        event
        for event in events
        if json.loads(event["payload_json"]).get("status")
        == "reap_skipped_missing_containment_identity"
    ]
    assert len(skip_events) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-q"]))

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
    event_kinds = [
        event["kind"]
        for event in state.read_events_since(
            "run-poison-result",
            after_event_id=0,
            limit=20,
        )
    ]
    assert event_kinds.index("dual_agent_workflow_worker_reaped") < (
        event_kinds.index("dual_agent_workflow_terminal_outcome")
    )


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


def test_cleanup_retry_escalates_durably_without_parking_live_worker(
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    _insert_spawned_job(
        state,
        tmp_path,
        job_id="cleanup-wedge",
        containment_id="containment-wedge",
    )
    clock = {"now": 1000}
    for attempt in range(1, 4):
        dispatcher = WorkflowJobDispatcher(
            state,
            dispatcher_id=f"dispatcher-test-{attempt}",
            max_cleanup_retry_attempts=2,
            pid_alive=lambda _pid: True,
            process_identity_probe=lambda _pid: (99999, 111.0),
            now=lambda: clock["now"],
            jitter=lambda _delay: 0,
        )
        dispatcher.reap_stale_leases()
        row = state.get_dual_agent_workflow_job(job_id="cleanup-wedge")
        assert row is not None
        assert row["cleanup_attempts"] == attempt
        assert row["status"] == "running"
        assert row["parked_reason"] is None
        if attempt <= 2:
            assert row["cleanup_escalated_at"] is None
        clock["now"] += 1000

    row = state.get_dual_agent_workflow_job(job_id="cleanup-wedge")
    assert row is not None
    assert row["status"] == "running"
    assert row["cleanup_attempts"] == 3
    assert row["cleanup_escalated_at"] == 3000
    assert row["parked_reason"] is None
    assert str(row["error"]).startswith(
        "cleanup_retry_attempts_exhausted"
    )
    assert str(row["leased_by"]).startswith("cleanup:")
    assert (
        state.count_active_dual_agent_workflow_job_leases(now=3000)
        == 0
    )


def _insert_request_written_job(
    state: State,
    tmp_path: Path,
    *,
    job_id: str,
) -> Path:
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / job_id
    state.upsert_dual_agent_workflow_job(
        job_id=job_id,
        run_id=f"run-{job_id}",
        task_id=f"task-{job_id}",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="request_written",
    )
    return job_dir / "result.json"


class _FastExitProcess:
    pid = 54321

    def poll(self) -> int:
        return 0


class _LiveProcess:
    pid = 54321

    def poll(self) -> None:
        return None


def test_spawn_persistence_failure_retains_durable_cleanup_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = State(str(tmp_path / "state.db"))
    _insert_request_written_job(
        state,
        tmp_path,
        job_id="persist-failure",
    )
    termination_calls: list[dict[str, object]] = []

    def fail_spawned_identity(**_kwargs: object) -> None:
        raise RuntimeError("persist denied")

    def unsafe_termination(**kwargs: object) -> dict[str, object]:
        termination_calls.append(dict(kwargs))
        return {
            "status": "worker_tree_survived_sigkill",
            "safe_to_finalize": False,
            "pid": kwargs["root_pid"],
            "pgid": kwargs.get("expected_process_group_id"),
            "containment_id": kwargs.get("containment_id"),
            "descendant_pids": [],
            "surviving_pids": [54321],
            "scan_errors": [],
            "containment_kind": "inherited_environment_same_user",
            "root_pid_reused": False,
        }

    monkeypatch.setattr(
        state,
        "record_dual_agent_workflow_job_spawned",
        fail_spawned_identity,
    )
    monkeypatch.setattr(
        workflow_job_dispatcher_module,
        "terminate_containment",
        unsafe_termination,
    )
    containment_id = "containment-persist-failure"
    monkeypatch.setattr(
        workflow_job_dispatcher_module,
        "new_containment_id",
        lambda: containment_id,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=lambda *_args, **_kwargs: _LiveProcess(),
        process_identity_probe=lambda _pid: (54321, 222.0),
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )
    monkeypatch.setattr(
        dispatcher,
        "_process_containment_id",
        lambda _pid: containment_id,
    )

    result = dispatcher.run_once(job_id="persist-failure")

    assert result["status"] == "cleanup_retry_pending"
    row = state.get_dual_agent_workflow_job(job_id="persist-failure")
    assert row is not None
    assert row["recovery_point"] == "spawn_prepared"
    assert row["status"] == "running"
    assert row["worker_containment_id"]
    assert row["cleanup_attempts"] == 1
    assert row["parked_reason"] is None
    assert str(row["leased_by"]).startswith("cleanup:")
    assert termination_calls
    assert termination_calls[0]["expected_root_started_at"] == 222.0
    assert (
        termination_calls[0]["containment_id"]
        == row["worker_containment_id"]
    )


def test_spawn_persistence_failure_uses_reap_proof_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_safe_termination(monkeypatch)
    state = State(str(tmp_path / "state.db"))
    _insert_request_written_job(
        state,
        tmp_path,
        job_id="persist-failure-reaped",
    )

    def fail_spawned_identity(**_kwargs: object) -> None:
        raise RuntimeError("persist denied")

    monkeypatch.setattr(
        state,
        "record_dual_agent_workflow_job_spawned",
        fail_spawned_identity,
    )
    containment_id = "containment-persist-failure-reaped"
    monkeypatch.setattr(
        workflow_job_dispatcher_module,
        "new_containment_id",
        lambda: containment_id,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=lambda *_args, **_kwargs: _LiveProcess(),
        process_identity_probe=lambda _pid: (54321, 222.0),
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )
    monkeypatch.setattr(
        dispatcher,
        "_process_containment_id",
        lambda _pid: containment_id,
    )

    result = dispatcher.run_once(job_id="persist-failure-reaped")

    assert result["status"] == "retry_scheduled"
    row = state.get_dual_agent_workflow_job(
        job_id="persist-failure-reaped"
    )
    assert row is not None
    assert row["recovery_point"] == "request_written"
    assert row["pid"] == 54321
    assert row["worker_pgid"] == 54321
    assert row["worker_started_at"] == 222.0
    assert row["worker_reaped_at"] == 1000
    assert row["dispatch_attempts"] == 1
    assert row["parked_reason"] is None
    reap_events = [
        event
        for event in state.read_events_since(
            "run-persist-failure-reaped",
            after_event_id=0,
            limit=20,
        )
        if event["kind"] == "dual_agent_workflow_worker_reaped"
    ]
    assert len(reap_events) == 1


def test_termination_forwards_observed_start_when_persisted_start_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = State(str(tmp_path / "state.db"))
    calls: list[dict[str, object]] = []

    def safe_termination(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "status": "worker_tree_terminated",
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
        safe_termination,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        process_identity_probe=lambda _pid: (41001, 333.25),
    )

    result = dispatcher._terminate_process_group(
        41001,
        expected_pgid=41001,
        expected_started_at=None,
        expected_containment_id="containment-observed-start",
    )

    assert result["safe_to_finalize"] is True
    assert calls[0]["expected_root_started_at"] == 333.25


def test_dispatcher_uses_same_user_scope_for_all_terminal_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = State(str(tmp_path / "state.db"))
    calls: list[dict[str, object]] = []

    def safe_termination(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
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
        safe_termination,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        process_identity_probe=lambda _pid: (41001, 333.25),
    )
    monkeypatch.setattr(
        dispatcher,
        "_process_containment_id",
        lambda _pid: "containment-scope",
    )

    dispatcher._terminate_process_group(
        41001,
        expected_pgid=41001,
        expected_started_at=333.25,
        expected_containment_id="containment-scope",
    )
    dispatcher._terminate_process_group(
        None,
        expected_containment_started_at=300.0,
        expected_containment_id="containment-scope",
    )

    assert calls[0]["unreadable_scope"] == "same_user"
    assert calls[1]["unreadable_scope"] == "same_user"


def test_spawn_identity_untrusted_rejects_preexisting_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_safe_termination(monkeypatch)
    state = State(str(tmp_path / "state.db"))
    result_path = _insert_request_written_job(
        state,
        tmp_path,
        job_id="fast-exit",
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "run_id": "run-fast-exit",
                "task_id": "task-fast-exit",
            }
        ),
        encoding="utf-8",
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=lambda *_args, **_kwargs: _FastExitProcess(),
        process_identity_probe=lambda _pid: None,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    retried = dispatcher.run_once(job_id="fast-exit")

    assert retried["status"] == "retry_scheduled"
    assert retried["recovery_point"] == "request_written"
    assert retried["parked_reason"] is None
    assert retried["error"] == "spawned_worker_identity_untrusted"
    assert retried["pid"] is None
    assert retried["worker_pgid"] is None
    assert retried["worker_started_at"] is None
    assert retried["worker_containment_id"] is None
    assert retried["worker_reaped_at"] is None
    assert retried["dispatch_attempts"] == 1
    assert retried["next_dispatch_at"] is not None
    assert not result_path.exists()
    quarantined = list((result_path.parent / ".quarantine").glob("*.stale"))
    assert len(quarantined) == 1
    assert json.loads(quarantined[0].read_text(encoding="utf-8")) == {
        "status": "completed",
        "run_id": "run-fast-exit",
        "task_id": "task-fast-exit",
    }
    events = state.read_events_since(
        "run-fast-exit",
        after_event_id=0,
        limit=20,
    )
    event_kinds = [event["kind"] for event in events]
    quarantine_payloads = [
        event["payload"]
        for event in events
        if event["payload"].get("status") == "result_quarantined"
    ]
    assert len(quarantine_payloads) == 1
    assert quarantine_payloads[0]["error"] == "before_new_spawn_attempt"
    assert (
        quarantine_payloads[0]["result_quarantine"]["quarantine_path"]
        == str(quarantined[0])
    )
    assert "dual_agent_workflow_worker_reaped" not in event_kinds
    assert "dual_agent_workflow_terminal_outcome" not in event_kinds


def test_spawn_cas_loss_does_not_quarantine_another_owners_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = State(str(tmp_path / "state.db"))
    result_path = _insert_request_written_job(
        state,
        tmp_path,
        job_id="spawn-cas-lost",
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    expected_result = {
        "status": "completed",
        "run_id": "run-spawn-cas-lost",
        "task_id": "task-spawn-cas-lost",
    }
    result_path.write_text(
        json.dumps(expected_result),
        encoding="utf-8",
    )
    claimed = state.claim_next_dual_agent_workflow_job_for_dispatch(
        dispatcher_id="dispatcher-loser",
        lease_ttl_s=60,
        now=1000,
        job_id="spawn-cas-lost",
    )
    assert claimed is not None
    state.update_dual_agent_workflow_job(
        job_id="spawn-cas-lost",
        leased_by="dispatcher-winner",
        lease_expires_at=1060,
        heartbeat_at=1000,
    )
    popen_called = False

    def forbidden_popen(*_args: object, **_kwargs: object) -> object:
        nonlocal popen_called
        popen_called = True
        raise AssertionError("losing dispatcher must not spawn")

    monkeypatch.setattr(
        workflow_job_dispatcher_module,
        "new_containment_id",
        lambda: "containment-losing-dispatcher",
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-loser",
        popen=forbidden_popen,
        now=lambda: 1000,
    )

    observed = dispatcher._spawn(claimed)

    assert observed["leased_by"] == "dispatcher-winner"
    assert observed["recovery_point"] == "request_written"
    assert popen_called is False
    assert result_path.exists()
    assert json.loads(result_path.read_text(encoding="utf-8")) == expected_result
    assert not (result_path.parent / ".quarantine").exists()
    assert all(
        event["payload"].get("status") != "result_quarantined"
        for event in state.read_events_since(
            "run-spawn-cas-lost",
            after_event_id=0,
            limit=20,
        )
    )


def test_spawn_identity_untrusted_without_result_schedules_retry_then_parks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_safe_termination(monkeypatch)
    state = State(str(tmp_path / "state.db"))
    _insert_request_written_job(
        state,
        tmp_path,
        job_id="no-result",
    )
    clock = {"now": 1000}
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        max_dispatch_attempts=2,
        popen=lambda *_args, **_kwargs: _FastExitProcess(),
        process_identity_probe=lambda _pid: None,
        now=lambda: clock["now"],
        jitter=lambda _delay: 0,
    )

    retried = dispatcher.run_once(job_id="no-result")
    assert retried["status"] == "retry_scheduled"
    assert retried["dispatch_attempts"] == 1
    assert retried["next_dispatch_at"] is not None
    assert retried["error"] == "spawned_worker_identity_untrusted"
    assert retried["pid"] is None
    assert retried["worker_pgid"] is None
    assert retried["worker_reaped_at"] is None

    clock["now"] = int(retried["next_dispatch_at"])
    parked = dispatcher.run_once(job_id="no-result")
    assert parked["status"] == "parked"
    assert str(parked["parked_reason"]).startswith(
        "max_dispatch_attempts_exceeded: spawned_worker_identity_untrusted"
    )
    reap_events = [
        event
        for event in state.read_events_since(
            "run-no-result",
            after_event_id=0,
            limit=20,
        )
        if event["kind"] == "dual_agent_workflow_worker_reaped"
    ]
    assert reap_events == []


def test_cleanup_retries_do_not_consume_dispatch_attempts(
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    _insert_spawned_job(
        state,
        tmp_path,
        job_id="cleanup-counter",
        containment_id="containment-counter",
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        max_cleanup_retry_attempts=5,
        pid_alive=lambda _pid: True,
        process_identity_probe=lambda _pid: (99999, 111.0),
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    dispatcher.reap_stale_leases()

    row = state.get_dual_agent_workflow_job(job_id="cleanup-counter")
    assert row is not None
    assert row["status"] == "running"
    assert str(row["leased_by"]).startswith("cleanup:")
    assert row["dispatch_attempts"] == 0
    assert row["cleanup_attempts"] == 1


def test_spawn_prepared_is_active_and_reaped_to_durable_retry(
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    _insert_request_written_job(
        state,
        tmp_path,
        job_id="prepared-restart",
    )
    claimed = state.claim_next_dual_agent_workflow_job_for_dispatch(
        dispatcher_id="dispatcher-before-crash",
        lease_ttl_s=60,
        now=1000,
        job_id="prepared-restart",
    )
    assert claimed is not None
    prepared = state.prepare_dual_agent_workflow_job_spawn(
        job_id="prepared-restart",
        dispatcher_id="dispatcher-before-crash",
        containment_id="containment-prepared-restart",
        lease_ttl_s=60,
        now=1000,
    )
    assert prepared is not None
    assert state.count_active_dual_agent_workflow_job_leases(now=1001) == 1
    state.update_dual_agent_workflow_job(
        job_id="prepared-restart",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-after-crash",
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )
    dispatcher._terminate_row_worker = lambda _row: {  # type: ignore[method-assign]
        "status": "worker_containment_empty",
        "safe_to_finalize": True,
        "pid": 0,
        "pgid": None,
        "containment_id": "containment-prepared-restart",
        "descendant_pids": [],
        "surviving_pids": [],
        "scan_errors": [],
        "containment_kind": "inherited_environment_same_user",
        "root_pid_reused": False,
    }

    result = dispatcher.reap_stale_leases()

    assert result["reclaimed"] == ["prepared-restart"]
    row = state.get_dual_agent_workflow_job(job_id="prepared-restart")
    assert row is not None
    assert row["status"] == "submitted"
    assert row["recovery_point"] == "request_written"
    assert row["worker_reaped_at"] == 1000
    assert row["dispatch_attempts"] == 1
    assert row["next_dispatch_at"] is not None
    assert state.count_active_dual_agent_workflow_job_leases(now=1000) == 0


def test_spawn_prepared_pidless_recovery_uses_containment_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_terminate_containment(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "status": "worker_containment_empty",
            "safe_to_finalize": True,
            "pid": 0,
            "pgid": None,
            "containment_id": kwargs["containment_id"],
        }

    monkeypatch.setattr(
        workflow_job_dispatcher_module,
        "terminate_containment",
        fake_terminate_containment,
    )
    dispatcher = WorkflowJobDispatcher(
        State(str(tmp_path / "state.db")),
    )

    result = dispatcher._terminate_process_group(
        None,
        expected_pgid=None,
        expected_started_at=None,
        expected_containment_id="containment-only-recovery",
    )

    assert result["safe_to_finalize"] is True
    assert calls == [
        {
            "root_pid": None,
            "expected_root_started_at": None,
            "expected_process_group_id": None,
            "containment_id": "containment-only-recovery",
            "process": None,
            "unreadable_scope": "same_user",
        }
    ]


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

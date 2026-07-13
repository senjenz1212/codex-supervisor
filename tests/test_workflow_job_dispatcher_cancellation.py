from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import psutil
import pytest

import supervisor.process_containment as process_containment_module
from supervisor.process_containment import (
    containment_environment,
    new_containment_id,
    terminate_containment,
)
from supervisor.state import State
from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher


pytestmark = pytest.mark.skipif(
    not hasattr(os, "killpg"),
    reason="process-group cancellation requires POSIX process groups",
)


def _spawn_stubborn_worker_with_child(
    tmp_path: Path,
    *,
    child_starts_new_session: bool = False,
) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    ready_path = tmp_path / "worker-tree.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

if sys.argv[2] == "setsid":
    os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({
        "child_pid": os.getpid(),
        "parent_pid": os.getppid(),
        "pgid": os.getpgrp(),
    }),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import signal
import subprocess
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([
    sys.executable,
    "-c",
    sys.argv[1],
    sys.argv[2],
    sys.argv[3],
])
while True:
    child.poll()
    time.sleep(0.05)
"""
    containment_id = new_containment_id()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(ready_path),
            "setsid" if child_starts_new_session else "same-group",
        ],
        env=containment_environment(os.environ, containment_id),
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if ready_path.exists():
                try:
                    tree = json.loads(
                        ready_path.read_text(encoding="utf-8")
                    )
                    tree["containment_id"] = containment_id
                    return process, tree
                except json.JSONDecodeError:
                    pass
            if process.poll() is not None:
                raise AssertionError(
                    f"worker exited before child became ready: {process.returncode}"
                )
            time.sleep(0.01)
        raise AssertionError("worker child did not become ready")
    except Exception:
        _cleanup_process_group(process, process.pid)
        raise


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _cleanup_process_group(process: subprocess.Popen[bytes], pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _cleanup_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _spawn_orphaned_tagged_child(
    tmp_path: Path,
    *,
    name: str,
) -> dict[str, Any]:
    ready_path = tmp_path / f"{name}.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import subprocess
import sys
import time

subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2]],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
time.sleep(0.2)
"""
    containment_id = new_containment_id()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(ready_path),
        ],
        env=containment_environment(os.environ, containment_id),
        start_new_session=True,
    )
    root_pid = process.pid
    root_started_at = psutil.Process(root_pid).create_time()
    process.wait(timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not ready_path.exists():
        time.sleep(0.01)
    if not ready_path.exists():
        raise AssertionError("orphaned tagged child did not become ready")
    child = json.loads(ready_path.read_text(encoding="utf-8"))
    return {
        "root_pid": root_pid,
        "root_started_at": root_started_at,
        "containment_id": containment_id,
        "child_pid": int(child["pid"]),
        "child_pgid": int(child["pgid"]),
    }


def test_cancel_term_kill_removes_worker_process_group_and_descendant(
    tmp_path: Path,
) -> None:
    process, tree = _spawn_stubborn_worker_with_child(tmp_path)
    pgid = tree["pgid"]
    assert pgid == process.pid
    assert os.getpgid(tree["child_pid"]) == pgid

    try:
        dispatcher = WorkflowJobDispatcher(object())
        dispatcher._terminate_process(process)

        assert not _process_group_exists(pgid)
        with pytest.raises(ProcessLookupError):
            os.getpgid(tree["child_pid"])
    finally:
        _cleanup_process_group(process, pgid)


def test_cancel_kills_descendant_that_escaped_with_setsid(tmp_path: Path) -> None:
    process, tree = _spawn_stubborn_worker_with_child(
        tmp_path,
        child_starts_new_session=True,
    )
    child_pid = tree["child_pid"]
    assert tree["pgid"] == child_pid
    assert tree["pgid"] != process.pid

    try:
        dispatcher = WorkflowJobDispatcher(object())
        result = dispatcher._terminate_process(process)

        assert result["safe_to_finalize"] is True
        assert child_pid in result["descendant_pids"]
        with pytest.raises(ProcessLookupError):
            os.getpgid(child_pid)
    finally:
        _cleanup_process_group(process, process.pid)
        _cleanup_pid(child_pid)


def test_cancel_kills_setsid_descendant_spawned_by_sigterm_handler(
    tmp_path: Path,
) -> None:
    ready_path = tmp_path / "spawn-on-term-root.json"
    child_path = tmp_path / "spawn-on-term-child.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

def on_term(_signum, _frame):
    subprocess.Popen(
        [sys.executable, "-c", sys.argv[1], sys.argv[3]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    raise SystemExit(0)

signal.signal(signal.SIGTERM, on_term)
Path(sys.argv[2]).write_text(
    json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}),
    encoding="utf-8",
)
while True:
    time.sleep(0.01)
"""
    containment_id = new_containment_id()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(ready_path),
            str(child_path),
        ],
        env=containment_environment(os.environ, containment_id),
        start_new_session=True,
    )
    child_pid = 0
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready_path.exists():
            time.sleep(0.01)
        assert ready_path.exists()
        started_at = psutil.Process(process.pid).create_time()

        result = WorkflowJobDispatcher(object())._terminate_process(
            process,
            expected_pgid=process.pid,
            expected_started_at=started_at,
            expected_containment_id=containment_id,
        )

        if child_path.exists():
            child_pid = int(
                json.loads(child_path.read_text(encoding="utf-8"))["pid"]
            )
        assert result["safe_to_finalize"] is True
        assert child_pid > 0
        assert child_pid in result["descendant_pids"]
        assert not psutil.pid_exists(child_pid)
    finally:
        _cleanup_process_group(process, process.pid)
        if child_pid:
            _cleanup_pid(child_pid)


def test_cancel_finds_detached_descendant_after_root_already_exited(
    tmp_path: Path,
) -> None:
    child_path = tmp_path / "orphan-child.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import subprocess
import sys

subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2]],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
"""
    containment_id = new_containment_id()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(child_path),
        ],
        env=containment_environment(os.environ, containment_id),
        start_new_session=True,
    )
    started_at = psutil.Process(process.pid).create_time()
    process.wait(timeout=5)
    child_pid = 0
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_path.exists():
            time.sleep(0.01)
        child_pid = int(
            json.loads(child_path.read_text(encoding="utf-8"))["pid"]
        )
        assert psutil.pid_exists(child_pid)

        result = WorkflowJobDispatcher(
            object(),
            process_identity_probe=lambda _pid: None,
        )._terminate_process_group(
            process.pid,
            expected_pgid=process.pid,
            expected_started_at=started_at,
            expected_containment_id=containment_id,
        )

        assert result["safe_to_finalize"] is True
        assert child_pid in result["descendant_pids"]
        assert not psutil.pid_exists(child_pid)
    finally:
        if child_pid:
            _cleanup_pid(child_pid)


def test_stale_lease_kills_process_group_before_marking_job_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process, tree = _spawn_stubborn_worker_with_child(tmp_path)
    pgid = tree["pgid"]
    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-stale"
    state.upsert_dual_agent_workflow_job(
        job_id="job-stale",
        run_id="run-stale",
        task_id="task-stale",
        cwd=str(tmp_path),
        status="running",
        pid=process.pid,
        worker_pgid=pgid,
        worker_started_at=psutil.Process(process.pid).create_time(),
        worker_containment_id=tree["containment_id"],
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    state.update_dual_agent_workflow_job(
        job_id="job-stale",
        leased_by=f"worker:{process.pid}",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    original_complete = state.complete_dual_agent_workflow_job
    completion_observations: list[bool] = []

    def complete_after_group_exit(**kwargs: object) -> int:
        completion_observations.append(not _process_group_exists(pgid))
        return original_complete(**kwargs)

    monkeypatch.setattr(
        state,
        "complete_dual_agent_workflow_job",
        complete_after_group_exit,
    )

    try:
        dispatcher = WorkflowJobDispatcher(
            state,
            dispatcher_id="dispatcher-test",
            now=lambda: 1000,
        )
        result = dispatcher.reap_stale_leases()

        assert result["failed"] == ["job-stale"]
        assert completion_observations == [True]
        assert not _process_group_exists(pgid)
        with pytest.raises(ProcessLookupError):
            os.getpgid(tree["child_pid"])
        job = state.get_dual_agent_workflow_job(job_id="job-stale")
        assert job is not None
        assert job["status"] == "failed"
        assert job["recovery_point"] == "terminal"
    finally:
        _cleanup_process_group(process, pgid)


def test_stale_reaper_loses_when_heartbeat_renews_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "heartbeat-race"
    state.upsert_dual_agent_workflow_job(
        job_id="heartbeat-race",
        run_id="run-heartbeat-race",
        task_id="task-heartbeat-race",
        cwd=str(tmp_path),
        status="running",
        pid=41010,
        worker_pgid=41010,
        worker_started_at=123.0,
        worker_containment_id="containment-heartbeat-race",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    state.update_dual_agent_workflow_job(
        job_id="heartbeat-race",
        leased_by="worker:41010",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    list_leases = state.list_dual_agent_workflow_job_leases

    def stale_snapshot_then_heartbeat() -> list[Any]:
        rows = list_leases()
        assert state.heartbeat_dual_agent_workflow_job(
            job_id="heartbeat-race",
            leased_by="worker:41010",
            lease_ttl_s=60,
            now=1000,
        )
        return rows

    monkeypatch.setattr(
        state,
        "list_dual_agent_workflow_job_leases",
        stale_snapshot_then_heartbeat,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-heartbeat-race",
        pid_alive=lambda _pid: True,
        now=lambda: 1000,
    )
    terminations: list[str] = []
    monkeypatch.setattr(
        dispatcher,
        "_terminate_row_worker",
        lambda row: (
            terminations.append(str(row["job_id"]))
            or {
                "status": "worker_already_reaped",
                "safe_to_finalize": True,
                "pid": row["pid"],
                "pgid": row["worker_pgid"],
                "containment_id": row["worker_containment_id"],
                "descendant_pids": [],
            }
        ),
    )

    result = dispatcher.reap_stale_leases()

    row = state.get_dual_agent_workflow_job(job_id="heartbeat-race")
    assert row is not None
    assert terminations == []
    assert result["failed"] == []
    assert result["completed"] == []
    assert row["status"] == "running"
    assert row["terminal_outcome_json"] is None
    assert row["leased_by"] == "worker:41010"
    assert row["lease_expires_at"] == 1060
    assert row["heartbeat_at"] == 1000


def test_two_stale_reapers_signal_only_after_one_snapshot_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    seed_state = State(str(db_path))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "two-reapers"
    seed_state.upsert_dual_agent_workflow_job(
        job_id="two-reapers",
        run_id="run-two-reapers",
        task_id="task-two-reapers",
        cwd=str(tmp_path),
        status="running",
        pid=41011,
        worker_pgid=41011,
        worker_started_at=124.0,
        worker_containment_id="containment-two-reapers",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    seed_state.update_dual_agent_workflow_job(
        job_id="two-reapers",
        leased_by="worker:41011",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    states = [State(str(db_path)), State(str(db_path))]
    snapshot_barrier = Barrier(2)
    for state in states:
        list_leases = state.list_dual_agent_workflow_job_leases

        def synchronized_list(
            list_leases: Any = list_leases,
        ) -> list[Any]:
            rows = list_leases()
            snapshot_barrier.wait(timeout=5)
            return rows

        monkeypatch.setattr(
            state,
            "list_dual_agent_workflow_job_leases",
            synchronized_list,
        )

    terminations: list[str] = []
    dispatchers = [
        WorkflowJobDispatcher(
            state,
            dispatcher_id=f"dispatcher-two-reapers-{index}",
            pid_alive=lambda _pid: True,
            now=lambda: 1000,
        )
        for index, state in enumerate(states)
    ]
    for dispatcher in dispatchers:
        monkeypatch.setattr(
            dispatcher,
            "_terminate_row_worker",
            lambda row, dispatcher=dispatcher: (
                terminations.append(dispatcher.dispatcher_id)
                or {
                    "status": "worker_already_reaped",
                    "safe_to_finalize": True,
                    "pid": row["pid"],
                    "pgid": row["worker_pgid"],
                    "containment_id": row["worker_containment_id"],
                    "descendant_pids": [],
                }
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda dispatcher: dispatcher.reap_stale_leases(), dispatchers))

    row = seed_state.get_dual_agent_workflow_job(job_id="two-reapers")
    assert row is not None
    assert len(terminations) == 1
    assert sum(result["failed"] == ["two-reapers"] for result in results) == 1
    assert row["status"] == "failed"
    assert row["recovery_point"] == "terminal"


def test_terminating_already_dead_process_group_is_idempotent() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        start_new_session=True,
    )
    pgid = process.pid
    process.wait(timeout=2)
    assert not _process_group_exists(pgid)
    dispatcher = WorkflowJobDispatcher(object())

    first = dispatcher._terminate_process(process, expected_pgid=pgid)
    second = dispatcher._terminate_process(process, expected_pgid=pgid)

    assert first["safe_to_finalize"] is True
    assert second["safe_to_finalize"] is True
    assert first["status"] == "worker_already_reaped"
    assert second["status"] == "worker_already_reaped"
    assert not _process_group_exists(pgid)


def test_terminal_pending_reap_persists_once_with_append_only_evidence(
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
             'terminal-pending-reap', 'run-terminal-reap', 'task-terminal-reap',
             '.', 'completed', 41001, 41001, 123.5, 'containment-terminal',
             NULL, 'req', 'res', 'log', 'terminal', 'completed',
             '{"status":"completed"}', 100, 0, NULL, 1, 1
           )"""
    )
    state._conn.commit()
    termination = {
        "status": "worker_already_reaped",
        "safe_to_finalize": True,
        "pid": 41001,
        "pgid": 41001,
        "containment_id": "containment-terminal",
        "descendant_pids": [],
    }

    event_id = state.record_dual_agent_workflow_worker_reaped(
        job_id="terminal-pending-reap",
        worker_reaped_at=200,
        termination=termination,
    )
    duplicate = state.record_dual_agent_workflow_worker_reaped(
        job_id="terminal-pending-reap",
        worker_reaped_at=200,
        termination=termination,
    )

    row = state.get_dual_agent_workflow_job(job_id="terminal-pending-reap")
    assert row is not None
    assert row["worker_reaped_at"] == 200
    assert event_id > 0
    assert duplicate == 0
    events = state._conn.execute(
        """SELECT kind, payload_json
             FROM events
            WHERE run_id='run-terminal-reap'
            ORDER BY event_id"""
    ).fetchall()
    assert [event["kind"] for event in events] == [
        "dual_agent_workflow_worker_reaped"
    ]


def test_spawn_identity_failure_uses_known_containment_to_kill_detached_child(
    tmp_path: Path,
) -> None:
    child_path = tmp_path / "spawn-identity-child.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import subprocess
import sys

subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2]],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
"""
    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "identity-failure"
    state.upsert_dual_agent_workflow_job(
        job_id="identity-failure",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="request_written",
    )
    row = state.get_dual_agent_workflow_job(job_id="identity-failure")
    assert row is not None
    child_pid = 0

    def spawn_then_exit(_command: list[str], **kwargs: Any):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                parent_code,
                child_code,
                str(child_path),
            ],
            env=kwargs["env"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            start_new_session=kwargs["start_new_session"],
        )
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_path.exists():
            time.sleep(0.01)
        return process

    try:
        dispatcher = WorkflowJobDispatcher(
            state,
            popen=spawn_then_exit,
            process_identity_probe=lambda _pid: None,
        )
        parked = dispatcher._spawn(row)
        child_pid = int(
            json.loads(child_path.read_text(encoding="utf-8"))["pid"]
        )

        assert parked["status"] == "parked"
        assert parked["parked_reason"] == "spawned_worker_identity_unavailable"
        assert not psutil.pid_exists(child_pid)
    finally:
        if child_pid:
            _cleanup_pid(child_pid)


def test_stale_pid_reuse_is_not_signalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
    )
    dispatcher = WorkflowJobDispatcher(
        object(),
        process_identity_probe=lambda _pid: (777, 222.0),
    )

    result = dispatcher._terminate_process_group(
        777,
        expected_pgid=777,
        expected_started_at=111.0,
    )

    assert result["status"] == "worker_identity_mismatch_pid_reused"
    assert result["safe_to_finalize"] is False
    assert signalled == []


def test_pid_reuse_still_terminates_surviving_tagged_descendant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tree = _spawn_orphaned_tagged_child(
        tmp_path,
        name="pid-reuse-shared-containment",
    )
    root_pid = int(tree["root_pid"])
    child_pid = int(tree["child_pid"])
    reused_started_at = float(tree["root_started_at"]) + 1000.0
    real_process_identity = process_containment_module.process_identity
    real_kill = os.kill
    real_killpg = os.killpg
    root_signals: list[signal.Signals] = []
    root_group_signals: list[signal.Signals] = []

    def reused_root_identity(
        pid: int,
    ) -> process_containment_module.ProcessIdentity | None:
        if int(pid) == root_pid:
            return process_containment_module.ProcessIdentity(
                root_pid,
                reused_started_at,
            )
        return real_process_identity(pid)

    def guarded_kill(pid: int, sig: signal.Signals) -> None:
        if int(pid) == root_pid:
            root_signals.append(sig)
        real_kill(pid, sig)

    def guarded_killpg(pgid: int, sig: signal.Signals) -> None:
        if int(pgid) == root_pid:
            root_group_signals.append(sig)
        real_killpg(pgid, sig)

    monkeypatch.setattr(
        process_containment_module,
        "process_identity",
        reused_root_identity,
    )
    monkeypatch.setattr(os, "kill", guarded_kill)
    monkeypatch.setattr(os, "killpg", guarded_killpg)

    try:
        result = terminate_containment(
            root_pid=root_pid,
            expected_root_started_at=tree["root_started_at"],
            expected_process_group_id=root_pid,
            containment_id=tree["containment_id"],
            term_timeout_s=1.0,
            kill_timeout_s=1.0,
            quiescence_s=0.05,
            poll_s=0.01,
        )

        assert result["safe_to_finalize"] is True
        assert result["root_pid_reused"] is True
        assert child_pid in result["descendant_pids"]
        assert not psutil.pid_exists(child_pid)
        assert root_signals == []
        assert root_group_signals == []
    finally:
        _cleanup_pid(child_pid)


def test_pid_reuse_reaps_tagged_descendant_before_dispatcher_finalizes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tree = _spawn_orphaned_tagged_child(
        tmp_path,
        name="pid-reuse-dispatcher",
    )
    root_pid = int(tree["root_pid"])
    child_pid = int(tree["child_pid"])
    reused_started_at = float(tree["root_started_at"]) + 1000.0
    real_process_identity = process_containment_module.process_identity
    real_kill = os.kill
    real_killpg = os.killpg
    root_signals: list[signal.Signals] = []
    root_group_signals: list[signal.Signals] = []

    def reused_root_identity(
        pid: int,
    ) -> process_containment_module.ProcessIdentity | None:
        if int(pid) == root_pid:
            return process_containment_module.ProcessIdentity(
                root_pid,
                reused_started_at,
            )
        return real_process_identity(pid)

    def guarded_kill(pid: int, sig: signal.Signals) -> None:
        if int(pid) == root_pid:
            root_signals.append(sig)
        real_kill(pid, sig)

    def guarded_killpg(pgid: int, sig: signal.Signals) -> None:
        if int(pgid) == root_pid:
            root_group_signals.append(sig)
        real_killpg(pgid, sig)

    monkeypatch.setattr(
        process_containment_module,
        "process_identity",
        reused_root_identity,
    )
    monkeypatch.setattr(os, "kill", guarded_kill)
    monkeypatch.setattr(os, "killpg", guarded_killpg)

    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "pid-reuse"
    state.upsert_dual_agent_workflow_job(
        job_id="pid-reuse",
        run_id="run-pid-reuse",
        task_id="task-pid-reuse",
        cwd=str(tmp_path),
        status="running",
        pid=root_pid,
        worker_pgid=root_pid,
        worker_started_at=tree["root_started_at"],
        worker_containment_id=tree["containment_id"],
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    state.update_dual_agent_workflow_job(
        job_id="pid-reuse",
        leased_by=f"worker:{root_pid}",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    original_complete = state.complete_dual_agent_workflow_job
    completion_observations: list[bool] = []

    def complete_after_containment_exit(**kwargs: object) -> int:
        completion_observations.append(not psutil.pid_exists(child_pid))
        return original_complete(**kwargs)

    monkeypatch.setattr(
        state,
        "complete_dual_agent_workflow_job",
        complete_after_containment_exit,
    )

    try:
        result = WorkflowJobDispatcher(
            state,
            dispatcher_id="dispatcher-test",
            pid_alive=lambda _pid: True,
            process_identity_probe=lambda _pid: (
                root_pid,
                reused_started_at,
            ),
            now=lambda: 1000,
        ).reap_stale_leases()

        row = state.get_dual_agent_workflow_job(job_id="pid-reuse")
        assert row is not None
        assert result["failed"] == ["pid-reuse"]
        assert result["cleanup_retry_pending"] == []
        assert completion_observations == [True]
        assert row["status"] == "failed"
        assert row["recovery_point"] == "terminal"
        assert row["worker_reaped_at"] == 1000
        assert root_signals == []
        assert root_group_signals == []
    finally:
        _cleanup_pid(child_pid)


def test_result_recovery_kills_live_worker_before_terminal_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process, tree = _spawn_stubborn_worker_with_child(tmp_path)
    pgid = tree["pgid"]
    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-result"
    job_dir.mkdir(parents=True)
    result_path = job_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "accepted",
                "run_id": "run-result",
                "task_id": "task-result",
            }
        ),
        encoding="utf-8",
    )
    state.upsert_dual_agent_workflow_job(
        job_id="job-result",
        run_id="run-result",
        task_id="task-result",
        cwd=str(tmp_path),
        status="running",
        pid=process.pid,
        worker_pgid=pgid,
        worker_started_at=psutil.Process(process.pid).create_time(),
        worker_containment_id=tree["containment_id"],
        request_path=str(job_dir / "request.json"),
        result_path=str(result_path),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    state.update_dual_agent_workflow_job(
        job_id="job-result",
        leased_by=f"worker:{process.pid}",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    original_complete = state.complete_dual_agent_workflow_job
    completion_observations: list[bool] = []

    def complete_after_group_exit(**kwargs: object) -> int:
        completion_observations.append(not _process_group_exists(pgid))
        return original_complete(**kwargs)

    monkeypatch.setattr(
        state,
        "complete_dual_agent_workflow_job",
        complete_after_group_exit,
    )

    try:
        dispatcher = WorkflowJobDispatcher(
            state,
            dispatcher_id="dispatcher-test",
            now=lambda: 1000,
        )
        result = dispatcher.reap_stale_leases()

        assert result["completed"] == ["job-result"]
        assert completion_observations == [True]
        job = state.get_dual_agent_workflow_job(job_id="job-result")
        assert job is not None
        assert job["worker_reaped_at"] == 1000
    finally:
        _cleanup_process_group(process, pgid)


def test_pid_reuse_with_empty_containment_finalizes_without_cleanup_retry(
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "cleanup-retry"
    state.upsert_dual_agent_workflow_job(
        job_id="cleanup-retry",
        run_id="run-cleanup-retry",
        task_id="task-cleanup-retry",
        cwd=str(tmp_path),
        status="running",
        pid=41001,
        worker_pgid=41001,
        worker_started_at=111.0,
        worker_containment_id="containment-cleanup-retry",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    state.update_dual_agent_workflow_job(
        job_id="cleanup-retry",
        leased_by="worker:41001",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    identity_probes: list[int] = []

    def identity_reports_reuse(
        pid: int,
    ) -> tuple[int, float] | None:
        identity_probes.append(pid)
        return (41001, 222.0)

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        pid_alive=lambda _pid: True,
        process_identity_probe=identity_reports_reuse,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    result = dispatcher.reap_stale_leases()
    row = state.get_dual_agent_workflow_job(job_id="cleanup-retry")
    assert row is not None
    assert result["failed"] == ["cleanup-retry"]
    assert result["cleanup_retry_pending"] == []
    assert row["status"] == "failed"
    assert row["recovery_point"] == "terminal"
    assert row["worker_reaped_at"] == 1000
    assert identity_probes == [41001]


def test_self_process_group_identity_is_quarantined_without_signalling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "self-pgid"
    current_pid = os.getpid()
    current_pgid = os.getpgrp()
    state.upsert_dual_agent_workflow_job(
        job_id="self-pgid",
        run_id="run-self-pgid",
        task_id="task-self-pgid",
        cwd=str(tmp_path),
        status="running",
        pid=current_pid,
        worker_pgid=current_pgid,
        worker_started_at=psutil.Process(current_pid).create_time(),
        worker_containment_id="containment-self-pgid",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    state.update_dual_agent_workflow_job(
        job_id="self-pgid",
        leased_by=f"worker:{current_pid}",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
    )

    result = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    ).reap_stale_leases()

    row = state.get_dual_agent_workflow_job(job_id="self-pgid")
    assert row is not None
    assert result["cleanup_retry_pending"] == ["self-pgid"]
    assert row["status"] == "running"
    assert row["error"] == "worker_process_group_matches_supervisor"
    assert str(row["leased_by"]).startswith("cleanup:")
    assert signalled == []


def test_corrupt_process_group_identity_is_quarantined_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "corrupt-pgid"
    state.upsert_dual_agent_workflow_job(
        job_id="corrupt-pgid",
        run_id="run-corrupt-pgid",
        task_id="task-corrupt-pgid",
        cwd=str(tmp_path),
        status="running",
        pid=41002,
        worker_pgid="not-a-pgid",  # type: ignore[arg-type]
        worker_started_at=111.0,
        worker_containment_id="containment-corrupt-pgid",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        recovery_point="spawned",
    )
    state.update_dual_agent_workflow_job(
        job_id="corrupt-pgid",
        leased_by="worker:41002",
        lease_expires_at=999,
        heartbeat_at=999,
    )
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
    )

    result = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        pid_alive=lambda _pid: True,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    ).reap_stale_leases()

    row = state.get_dual_agent_workflow_job(job_id="corrupt-pgid")
    assert row is not None
    assert result["cleanup_retry_pending"] == ["corrupt-pgid"]
    assert row["status"] == "running"
    assert row["error"] == "invalid_worker_process_group"
    assert str(row["leased_by"]).startswith("cleanup:")
    assert signalled == []


def test_shared_containment_rejects_malformed_process_group_without_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
    )

    result = terminate_containment(
        root_pid=41003,
        expected_root_started_at=None,
        expected_process_group_id="not-a-pgid",  # type: ignore[arg-type]
        containment_id="containment-malformed-pgid",
    )

    assert result["status"] == "invalid_worker_process_group"
    assert result["safe_to_finalize"] is False
    assert signalled == []


def test_process_identity_access_denied_fails_closed_without_signalling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: signalled.append((pgid, sig)),
    )

    def deny_identity(_pid: int) -> tuple[int, float] | None:
        raise psutil.AccessDenied(pid=41004)

    result = WorkflowJobDispatcher(
        object(),
        process_identity_probe=deny_identity,
    )._terminate_process_group(
        41004,
        expected_pgid=41004,
        expected_started_at=111.0,
        expected_containment_id="containment-access-denied",
    )

    assert result["status"] == "worker_identity_probe_failed"
    assert result["safe_to_finalize"] is False
    assert signalled == []

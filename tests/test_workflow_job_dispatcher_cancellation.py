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


_REAL_OS_KILL = os.kill
_REAL_OS_KILLPG = os.killpg
_REAL_PSUTIL_PIDS = psutil.pids
_REAL_PSUTIL_PROCESS = psutil.Process


class _TaggedProcessCleanup:
    def __init__(self, request: pytest.FixtureRequest) -> None:
        self._containment_ids: set[str] = set()
        self._processes: list[tuple[subprocess.Popen[bytes], int | None]] = []
        request.addfinalizer(self._finalize)

    def register(self, containment_id: str) -> None:
        normalized = str(containment_id).strip()
        assert normalized
        self._containment_ids.add(normalized)

    def watch(
        self,
        process: subprocess.Popen[bytes],
        *,
        pgid: int | None = None,
    ) -> None:
        self._processes.append((process, pgid))

    def snapshot(
        self,
        containment_id: str | None = None,
    ) -> tuple[process_containment_module.ProcessIdentity, ...]:
        ids = (
            {str(containment_id)}
            if containment_id is not None
            else self._containment_ids
        )
        found: list[process_containment_module.ProcessIdentity] = []
        for pid in _REAL_PSUTIL_PIDS():
            if pid == os.getpid():
                continue
            try:
                process = _REAL_PSUTIL_PROCESS(pid)
                started_at = float(process.create_time())
                if (
                    not process.is_running()
                    or process.status() == psutil.STATUS_ZOMBIE
                ):
                    continue
                marker = str(
                    process.environ().get(
                        process_containment_module.CONTAINMENT_ENV_VAR,
                        "",
                    )
                )
            except (
                OSError,
                ValueError,
                psutil.AccessDenied,
                psutil.NoSuchProcess,
                psutil.ZombieProcess,
                ProcessLookupError,
            ):
                continue
            if marker in ids:
                found.append(
                    process_containment_module.ProcessIdentity(
                        pid=pid,
                        started_at=started_at,
                    )
                )
        return tuple(sorted(found))

    def cleanup(self) -> tuple[process_containment_module.ProcessIdentity, ...]:
        for process, pgid in self._processes:
            if process.poll() is not None:
                continue
            try:
                if pgid is not None:
                    _REAL_OS_KILLPG(pgid, signal.SIGKILL)
                else:
                    _REAL_OS_KILL(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.monotonic() + 3
        quiet_scans = 0
        survivors: tuple[
            process_containment_module.ProcessIdentity,
            ...,
        ] = ()
        while time.monotonic() < deadline:
            survivors = self.snapshot()
            if not survivors:
                quiet_scans += 1
                if quiet_scans >= 2:
                    break
            else:
                quiet_scans = 0
                for identity in survivors:
                    try:
                        process = _REAL_PSUTIL_PROCESS(identity.pid)
                        if abs(
                            float(process.create_time())
                            - identity.started_at
                        ) <= 0.001:
                            _REAL_OS_KILL(identity.pid, signal.SIGKILL)
                    except (
                        OSError,
                        psutil.AccessDenied,
                        psutil.NoSuchProcess,
                        psutil.ZombieProcess,
                        ProcessLookupError,
                    ):
                        pass
            time.sleep(0.02)
        for process, _pgid in self._processes:
            try:
                process.wait(timeout=0.5)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                pass
        return self.snapshot()

    def _finalize(self) -> None:
        survivors = self.cleanup()
        assert survivors == (), (
            "tagged process survivors after fallback cleanup: "
            f"{survivors!r}"
        )


@pytest.fixture
def tagged_process_cleanup(
    request: pytest.FixtureRequest,
) -> _TaggedProcessCleanup:
    return _TaggedProcessCleanup(request)


pytestmark = pytest.mark.skipif(
    not hasattr(os, "killpg"),
    reason="process-group cancellation requires POSIX process groups",
)


def _spawn_stubborn_worker_with_child(
    tmp_path: Path,
    cleanup: _TaggedProcessCleanup,
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
    cleanup.register(containment_id)
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
    cleanup.watch(process, pgid=process.pid)
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
        cleanup.cleanup()
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


def _hermetic_containment_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop unrelated environ-unreadable processes from containment scans.

    Root-identity-less scans fail closed on any same-user process that denies
    environment reads (common on macOS). These tests assert termination
    outcomes for the launched tree itself, so they emulate a host without
    unrelated unreadable processes.
    """
    real_process_iter = process_containment_module.psutil.process_iter

    def readable_process_iter(attrs: list[str]) -> list[Any]:
        readable: list[Any] = []
        for proc in real_process_iter(attrs):
            try:
                proc.environ()
            except Exception:
                continue
            readable.append(proc)
        return readable

    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        readable_process_iter,
    )


def _spawn_orphaned_tagged_child(
    tmp_path: Path,
    cleanup: _TaggedProcessCleanup,
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
    cleanup.register(containment_id)
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
    cleanup.watch(process, pgid=process.pid)
    try:
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
    except Exception:
        cleanup.cleanup()
        raise


def test_cancel_term_kill_removes_worker_process_group_and_descendant(
    tmp_path: Path,
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    process, tree = _spawn_stubborn_worker_with_child(
        tmp_path,
        tagged_process_cleanup,
    )
    pgid = tree["pgid"]

    try:
        assert pgid == process.pid
        assert os.getpgid(tree["child_pid"]) == pgid
        dispatcher = WorkflowJobDispatcher(object())
        dispatcher._terminate_process(process)

        product_survivors = tagged_process_cleanup.snapshot(
            tree["containment_id"]
        )
        assert not _process_group_exists(pgid)
        with pytest.raises(ProcessLookupError):
            os.getpgid(tree["child_pid"])
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
        _cleanup_process_group(process, pgid)


def test_cancel_kills_descendant_that_escaped_with_setsid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    _hermetic_containment_scan(monkeypatch)
    process, tree = _spawn_stubborn_worker_with_child(
        tmp_path,
        tagged_process_cleanup,
        child_starts_new_session=True,
    )
    child_pid = tree["child_pid"]

    try:
        assert tree["pgid"] == child_pid
        assert tree["pgid"] != process.pid
        dispatcher = WorkflowJobDispatcher(object())
        result = dispatcher._terminate_process(process)

        product_survivors = tagged_process_cleanup.snapshot(
            tree["containment_id"]
        )
        assert result["safe_to_finalize"] is True
        assert child_pid in result["descendant_pids"]
        with pytest.raises(ProcessLookupError):
            os.getpgid(child_pid)
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
        _cleanup_process_group(process, process.pid)
        _cleanup_pid(child_pid)


def test_cancel_kills_setsid_descendant_spawned_by_sigterm_handler(
    tmp_path: Path,
    tagged_process_cleanup: _TaggedProcessCleanup,
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
    tagged_process_cleanup.register(containment_id)
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
    tagged_process_cleanup.watch(process, pgid=process.pid)
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
        product_survivors = tagged_process_cleanup.snapshot(containment_id)
        assert result["safe_to_finalize"] is True
        assert child_pid > 0
        assert child_pid in result["descendant_pids"]
        assert not psutil.pid_exists(child_pid)
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
        _cleanup_process_group(process, process.pid)
        if child_pid:
            _cleanup_pid(child_pid)


def test_cancel_finds_detached_descendant_after_root_already_exited(
    tmp_path: Path,
    tagged_process_cleanup: _TaggedProcessCleanup,
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
    tagged_process_cleanup.register(containment_id)
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
    tagged_process_cleanup.watch(process, pgid=process.pid)
    child_pid = 0
    try:
        started_at = psutil.Process(process.pid).create_time()
        process.wait(timeout=5)
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

        product_survivors = tagged_process_cleanup.snapshot(containment_id)
        assert result["safe_to_finalize"] is True
        assert child_pid in result["descendant_pids"]
        assert not psutil.pid_exists(child_pid)
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
        if child_pid:
            _cleanup_pid(child_pid)


def test_stale_lease_kills_process_group_before_marking_job_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    process, tree = _spawn_stubborn_worker_with_child(
        tmp_path,
        tagged_process_cleanup,
    )
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

        product_survivors = tagged_process_cleanup.snapshot(
            tree["containment_id"]
        )
        assert result["failed"] == ["job-stale"]
        assert completion_observations == [True]
        assert not _process_group_exists(pgid)
        with pytest.raises(ProcessLookupError):
            os.getpgid(tree["child_pid"])
        job = state.get_dual_agent_workflow_job(job_id="job-stale")
        assert job is not None
        assert job["status"] == "failed"
        assert job["recovery_point"] == "terminal"
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
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


def test_terminating_already_dead_process_group_is_idempotent(
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        start_new_session=True,
    )
    tagged_process_cleanup.watch(process, pgid=process.pid)
    pgid = process.pid
    try:
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
    finally:
        tagged_process_cleanup.cleanup()


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
    monkeypatch: pytest.MonkeyPatch,
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    _hermetic_containment_scan(monkeypatch)
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
    dispatcher_id = "dispatcher-identity-failure"
    state.update_dual_agent_workflow_job(
        job_id="identity-failure",
        leased_by=dispatcher_id,
    )
    row = state.get_dual_agent_workflow_job(job_id="identity-failure")
    assert row is not None
    child_pid = 0
    spawned_child: dict[str, Any] = {}

    def spawn_then_exit(_command: list[str], **kwargs: Any):
        containment_id = str(
            kwargs["env"][
                process_containment_module.CONTAINMENT_ENV_VAR
            ]
        )
        tagged_process_cleanup.register(containment_id)
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
        tagged_process_cleanup.watch(process, pgid=process.pid)
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_path.exists():
            time.sleep(0.01)
        if child_path.exists():
            spawned_child.update(
                json.loads(child_path.read_text(encoding="utf-8"))
            )
        return process

    try:
        dispatcher = WorkflowJobDispatcher(
            state,
            dispatcher_id=dispatcher_id,
            popen=spawn_then_exit,
            process_identity_probe=lambda _pid: None,
        )
        retried = dispatcher._spawn(row)
        child_pid = int(spawned_child["pid"])
        product_survivors = tagged_process_cleanup.snapshot()

        assert retried["status"] == "submitted"
        assert retried["recovery_point"] == "request_written"
        assert retried["dispatch_attempts"] == 1
        assert retried["next_dispatch_at"] is not None
        assert retried["error"] == "spawned_worker_identity_unavailable"
        assert not psutil.pid_exists(child_pid)
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
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
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    _hermetic_containment_scan(monkeypatch)
    tree = _spawn_orphaned_tagged_child(
        tmp_path,
        tagged_process_cleanup,
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

        product_survivors = tagged_process_cleanup.snapshot(
            tree["containment_id"]
        )
        assert result["safe_to_finalize"] is True
        assert result["root_pid_reused"] is True
        assert child_pid in result["descendant_pids"]
        assert not psutil.pid_exists(child_pid)
        assert root_signals == []
        assert root_group_signals == []
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
        _cleanup_pid(child_pid)


def test_pid_reuse_reaps_tagged_descendant_before_dispatcher_finalizes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    _hermetic_containment_scan(monkeypatch)
    tree = _spawn_orphaned_tagged_child(
        tmp_path,
        tagged_process_cleanup,
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
        product_survivors = tagged_process_cleanup.snapshot(
            tree["containment_id"]
        )
        assert row is not None
        assert result["failed"] == ["pid-reuse"]
        assert result["cleanup_retry_pending"] == []
        assert completion_observations == [True]
        assert row["status"] == "failed"
        assert row["recovery_point"] == "terminal"
        assert row["worker_reaped_at"] == 1000
        assert root_signals == []
        assert root_group_signals == []
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
        _cleanup_pid(child_pid)


def test_result_recovery_kills_live_worker_before_terminal_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    process, tree = _spawn_stubborn_worker_with_child(
        tmp_path,
        tagged_process_cleanup,
    )
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

        product_survivors = tagged_process_cleanup.snapshot(
            tree["containment_id"]
        )
        assert result["completed"] == ["job-result"]
        assert completion_observations == [True]
        job = state.get_dual_agent_workflow_job(job_id="job-result")
        assert job is not None
        assert job["worker_reaped_at"] == 1000
        assert product_survivors == ()
    finally:
        tagged_process_cleanup.cleanup()
        _cleanup_process_group(process, pgid)


def test_pid_reuse_with_empty_containment_finalizes_without_cleanup_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hermetic_containment_scan(monkeypatch)
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


def test_scan_fails_closed_for_unreadable_reparented_child_outside_root_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    class UnreadableDetachedChild:
        pid = 50003
        info = {
            "pid": 50003,
            "ppid": 1,
            "username": "test-user",
            "create_time": 200.0,
        }

        def environ(self) -> dict[str, str]:
            raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        lambda *_args, **_kwargs: CurrentProcess(),
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [UnreadableDetachedChild()],
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda _identity: False,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpid",
        lambda: 99999,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpgid",
        lambda pid: (
            70004
            if int(pid) == 50003
            else (_ for _ in ()).throw(ProcessLookupError())
        ),
    )

    snapshot = process_containment_module.scan_containment(
        "containment-root-exited",
        root_identity=process_containment_module.ProcessIdentity(
            pid=41003,
            started_at=100.0,
        ),
    )

    assert snapshot.processes == ()
    assert snapshot.scan_complete is False
    assert snapshot.errors == ("access_denied:50003",)


def test_terminate_never_certifies_unreadable_reparented_child_outside_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    class UnreadableDetachedChild:
        pid = 50004
        info = {
            "pid": 50004,
            "ppid": 1,
            "username": "test-user",
            "create_time": 200.0,
        }

        def environ(self) -> dict[str, str]:
            raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        lambda *_args, **_kwargs: CurrentProcess(),
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [UnreadableDetachedChild()],
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda _identity: False,
    )
    monkeypatch.setattr(
        process_containment_module,
        "process_identity",
        lambda _pid: None,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpid",
        lambda: 99999,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpgrp",
        lambda: 99998,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpgid",
        lambda pid: (
            70005
            if int(pid) == 50004
            else (_ for _ in ()).throw(ProcessLookupError())
        ),
    )

    result = terminate_containment(
        root_pid=41004,
        expected_root_started_at=100.0,
        expected_process_group_id=70004,
        containment_id="containment-detached-unreadable",
        term_timeout_s=0.02,
        kill_timeout_s=0.02,
        quiescence_s=0,
        poll_s=0.001,
    )

    assert result["safe_to_finalize"] is False
    assert result["status"] == "worker_containment_scan_incomplete"
    assert result["scan_errors"] == ["access_denied:50004"]


def test_containment_tree_does_not_follow_reused_parent_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    class ReusedRoot:
        pid = 41003
        info = {
            "pid": 41003,
            "ppid": 1,
            "username": "test-user",
            "create_time": 900.0,
        }

        def environ(self) -> dict[str, str]:
            return {}

    class UnreadableChild:
        pid = 50005
        info = {
            "pid": 50005,
            "ppid": 41003,
            "username": "test-user",
            "create_time": 901.0,
        }

        def environ(self) -> dict[str, str]:
            raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        lambda *_args, **_kwargs: CurrentProcess(),
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [ReusedRoot(), UnreadableChild()],
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda _identity: False,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpid",
        lambda: 99999,
    )

    snapshot = process_containment_module.scan_containment(
        "containment-reused-parent",
        root_identity=process_containment_module.ProcessIdentity(
            pid=41003,
            started_at=100.0,
        ),
        unreadable_scope="containment_tree",
    )

    assert snapshot.scan_complete is True
    assert snapshot.errors == ()


def test_scan_retains_exact_unreadable_identity_after_reparenting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    class MatchingRoot:
        pid = 41006
        info = {
            "pid": 41006,
            "ppid": 1,
            "username": "test-user",
            "create_time": 100.0,
        }

        def environ(self) -> dict[str, str]:
            return {}

    class UnreadableChild:
        pid = 50006

        def __init__(self, ppid: int) -> None:
            self.info = {
                "pid": self.pid,
                "ppid": ppid,
                "username": "test-user",
                "create_time": 200.0,
            }

        def environ(self) -> dict[str, str]:
            raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        lambda *_args, **_kwargs: CurrentProcess(),
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda _identity: False,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpid",
        lambda: 99999,
    )
    root_identity = process_containment_module.ProcessIdentity(
        pid=41006,
        started_at=100.0,
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [MatchingRoot(), UnreadableChild(41006)],
    )

    first = process_containment_module.scan_containment(
        "containment-reparented-identity",
        root_identity=root_identity,
        unreadable_scope="containment_tree",
    )

    expected = (
        process_containment_module.UnreadableProcessIdentity(
            pid=50006,
            started_at=200.0,
            relation="structural_descendant",
            error="access_denied:50006",
        ),
    )
    assert first.unreadable_identities == expected
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [UnreadableChild(1)],
    )

    second = process_containment_module.scan_containment(
        "containment-reparented-identity",
        root_identity=root_identity,
        known_unreadable_identities=first.unreadable_identities,
        unreadable_scope="containment_tree",
    )

    assert second.scan_complete is False
    assert second.unreadable_identities == expected
    assert second.errors == ("access_denied:50006",)


def test_terminate_retains_unreadable_identity_across_reparenting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    class MatchingRoot:
        pid = 41007
        info = {
            "pid": 41007,
            "ppid": 1,
            "username": "test-user",
            "create_time": 100.0,
        }

        def environ(self) -> dict[str, str]:
            return {}

    class UnreadableChild:
        pid = 50007

        def __init__(self, ppid: int) -> None:
            self.info = {
                "pid": self.pid,
                "ppid": ppid,
                "username": "test-user",
                "create_time": 200.0,
            }

        def environ(self) -> dict[str, str]:
            raise psutil.AccessDenied(pid=self.pid)

    scan_count = 0

    def process_iter(_attrs: list[str]) -> list[object]:
        nonlocal scan_count
        scan_count += 1
        if scan_count == 1:
            return [MatchingRoot(), UnreadableChild(41007)]
        return [UnreadableChild(1)]

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        lambda *_args, **_kwargs: CurrentProcess(),
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        process_iter,
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda _identity: False,
    )
    monkeypatch.setattr(
        process_containment_module,
        "process_identity",
        lambda _pid: None,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpid",
        lambda: 99999,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpgrp",
        lambda: 99998,
    )

    result = terminate_containment(
        root_pid=41007,
        expected_root_started_at=100.0,
        expected_process_group_id=70007,
        containment_id="containment-reparented-unreadable",
        term_timeout_s=0.02,
        kill_timeout_s=0.02,
        quiescence_s=0,
        poll_s=0.001,
        unreadable_scope="containment_tree",
    )

    assert result["safe_to_finalize"] is False
    assert result["status"] == "worker_containment_scan_incomplete"
    assert result["unresolved_process_identities"] == [
        {
            "pid": 50007,
            "started_at": 200.0,
            "relation": "structural_descendant",
            "error": "access_denied:50007",
        }
    ]


def test_scan_does_not_treat_iterator_omission_as_exact_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    def process_factory(pid: int | None = None) -> CurrentProcess:
        if pid == 50008:
            raise psutil.AccessDenied(pid=pid)
        return CurrentProcess()

    unresolved = process_containment_module.UnreadableProcessIdentity(
        pid=50008,
        started_at=200.0,
        relation="structural_descendant",
        error="access_denied:50008",
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        process_factory,
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [],
    )

    snapshot = process_containment_module.scan_containment(
        "containment-iterator-omission",
        known_unreadable_identities=(unresolved,),
        unreadable_scope="containment_tree",
    )

    assert snapshot.scan_complete is False
    assert snapshot.unreadable_identities == (unresolved,)
    assert snapshot.errors == ("access_denied:50008",)


def test_scan_retains_known_identity_when_create_time_becomes_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    class CreateTimeDenied:
        pid = 50008
        info = {
            "pid": 50008,
            "ppid": 1,
            "username": "test-user",
            "create_time": None,
        }

        def create_time(self) -> float:
            raise psutil.AccessDenied(pid=self.pid)

    unresolved = process_containment_module.UnreadableProcessIdentity(
        pid=50008,
        started_at=200.0,
        relation="known_identity",
        error="access_denied:50008",
    )

    def process_factory(pid: int | None = None) -> CurrentProcess:
        if pid == unresolved.pid:
            raise psutil.AccessDenied(pid=pid)
        return CurrentProcess()

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        process_factory,
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [CreateTimeDenied()],
    )

    snapshot = process_containment_module.scan_containment(
        "containment-create-time-denied",
        root_identity=unresolved.identity,
        unreadable_scope="containment_tree",
    )

    assert snapshot.scan_complete is False
    assert snapshot.unreadable_identities == (unresolved,)
    assert snapshot.errors == ("access_denied:50008",)


@pytest.mark.parametrize("resolution", ("exit", "reuse", "readable"))
def test_scan_resolves_unreadable_only_after_exact_classification(
    monkeypatch: pytest.MonkeyPatch,
    resolution: str,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    class ReusedProcess:
        pid = 50008
        info = {
            "pid": 50008,
            "ppid": 1,
            "username": "test-user",
            "create_time": 300.0,
        }

        def environ(self) -> dict[str, str]:
            raise psutil.AccessDenied(pid=self.pid)

    class ReadableProcess:
        pid = 50008
        info = {
            "pid": 50008,
            "ppid": 1,
            "username": "test-user",
            "create_time": 200.0,
        }

        def environ(self) -> dict[str, str]:
            return {}

    unresolved = process_containment_module.UnreadableProcessIdentity(
        pid=50008,
        started_at=200.0,
        relation="structural_descendant",
        error="access_denied:50008",
    )

    def process_factory(pid: int | None = None) -> CurrentProcess:
        if pid == unresolved.pid:
            raise psutil.NoSuchProcess(pid)
        return CurrentProcess()

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        process_factory,
    )
    observed: list[object]
    if resolution == "reuse":
        observed = [ReusedProcess()]
    elif resolution == "readable":
        observed = [ReadableProcess()]
    else:
        observed = []
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: observed,
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda _identity: False,
    )

    snapshot = process_containment_module.scan_containment(
        "containment-exact-resolution",
        known_unreadable_identities=(unresolved,),
        unreadable_scope="containment_tree",
    )

    assert snapshot.scan_complete is True
    assert snapshot.unreadable_identities == ()
    assert snapshot.errors == ()


def test_terminate_signals_generation_matched_unreadable_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unresolved = process_containment_module.UnreadableProcessIdentity(
        pid=50009,
        started_at=200.0,
        relation="structural_descendant",
        error="access_denied:50009",
    )
    signals: list[signal.Signals] = []

    def fake_scan(
        _containment_id: str,
        **_kwargs: object,
    ) -> process_containment_module.ContainmentSnapshot:
        return process_containment_module.ContainmentSnapshot(
            processes=(),
            scan_complete=False,
            errors=(unresolved.error,),
            unreadable_identities=(unresolved,),
        )

    def fake_identity(
        pid: int,
    ) -> process_containment_module.ProcessIdentity | None:
        if int(pid) == unresolved.pid:
            return unresolved.identity
        return None

    monkeypatch.setattr(
        process_containment_module,
        "scan_containment",
        fake_scan,
    )
    monkeypatch.setattr(
        process_containment_module,
        "process_identity",
        fake_identity,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "kill",
        lambda pid, sig: (
            signals.append(sig)
            if int(pid) == unresolved.pid
            else None
        ),
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpgrp",
        lambda: 99998,
    )

    result = terminate_containment(
        root_pid=41009,
        expected_root_started_at=100.0,
        expected_process_group_id=70009,
        containment_id="containment-unreadable-signal",
        term_timeout_s=0.005,
        kill_timeout_s=0.005,
        quiescence_s=0,
        poll_s=0.001,
        unreadable_scope="containment_tree",
    )

    assert signal.SIGTERM in signals
    assert signal.SIGKILL in signals
    assert result["safe_to_finalize"] is False
    assert result["unresolved_process_identities"][0]["pid"] == 50009


def test_terminate_never_signals_reused_unreadable_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unresolved = process_containment_module.UnreadableProcessIdentity(
        pid=50010,
        started_at=200.0,
        relation="structural_descendant",
        error="access_denied:50010",
    )
    signals: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(
        process_containment_module,
        "scan_containment",
        lambda _containment_id, **_kwargs: (
            process_containment_module.ContainmentSnapshot(
                processes=(),
                scan_complete=False,
                errors=(unresolved.error,),
                unreadable_identities=(unresolved,),
            )
        ),
    )
    monkeypatch.setattr(
        process_containment_module,
        "process_identity",
        lambda pid: (
            process_containment_module.ProcessIdentity(
                pid=int(pid),
                started_at=201.0,
            )
            if int(pid) == unresolved.pid
            else None
        ),
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "kill",
        lambda pid, sig: signals.append((int(pid), sig)),
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpgrp",
        lambda: 99998,
    )

    result = terminate_containment(
        root_pid=41010,
        expected_root_started_at=100.0,
        expected_process_group_id=70010,
        containment_id="containment-unreadable-reused",
        term_timeout_s=0,
        kill_timeout_s=0,
        quiescence_s=0,
        poll_s=0.001,
        unreadable_scope="containment_tree",
    )

    assert signals == []
    assert result["safe_to_finalize"] is False
    assert result["surviving_pids"] == [50010]


def test_terminate_supports_containment_id_only_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = process_containment_module.ProcessIdentity(
        pid=50011,
        started_at=200.0,
    )
    alive = True
    signals: list[signal.Signals] = []

    def fake_scan(
        _containment_id: str,
        **_kwargs: object,
    ) -> process_containment_module.ContainmentSnapshot:
        return process_containment_module.ContainmentSnapshot(
            processes=(identity,) if alive else (),
            scan_complete=True,
        )

    def fake_kill(pid: int, sig: signal.Signals) -> None:
        nonlocal alive
        assert int(pid) == identity.pid
        signals.append(sig)
        alive = False

    monkeypatch.setattr(
        process_containment_module,
        "scan_containment",
        fake_scan,
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda observed: alive and observed == identity,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "kill",
        fake_kill,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpgid",
        lambda _pid: None,
    )

    result = terminate_containment(
        root_pid=None,
        expected_root_started_at=None,
        expected_process_group_id=None,
        containment_id="containment-id-only",
        term_timeout_s=0.02,
        kill_timeout_s=0.02,
        quiescence_s=0,
        poll_s=0.001,
    )

    assert result["safe_to_finalize"] is True
    assert result["status"] == "worker_tree_terminated"
    assert result["pid"] == 0
    assert result["pgid"] is None
    assert result["descendant_pids"] == [50011]
    assert signals == [signal.SIGTERM]


def test_containment_id_only_recovery_reaps_real_tagged_process(
    tagged_process_cleanup: _TaggedProcessCleanup,
) -> None:
    containment_id = new_containment_id()
    tagged_process_cleanup.register(containment_id)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env=containment_environment(os.environ, containment_id),
        start_new_session=True,
    )
    tagged_process_cleanup.watch(process, pgid=process.pid)
    try:
        result = terminate_containment(
            root_pid=None,
            expected_root_started_at=None,
            expected_process_group_id=None,
            containment_id=containment_id,
            term_timeout_s=1,
            kill_timeout_s=1,
            quiescence_s=0.05,
            poll_s=0.01,
            unreadable_scope="containment_tree",
        )

        product_survivors = tagged_process_cleanup.snapshot(containment_id)
        product_returncode = process.poll()
        assert result["safe_to_finalize"] is True
        assert process.pid in result["descendant_pids"]
        assert product_survivors == ()
        assert product_returncode is not None
    finally:
        tagged_process_cleanup.cleanup()

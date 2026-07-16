from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import psutil
import pytest

import supervisor.dual_agent_runner as dual_agent_runner_module
from supervisor.agent_runtime import AgentTask, ClaudeCodeRuntime
from supervisor.dual_agent_runner import (
    DualAgentCancellationCleanupError,
    DualAgentGateSpec,
    build_lead_replay_stdout,
    run_dual_agent_gate_with_escalation,
)
from supervisor.process_containment import (
    ContainmentSnapshot,
    ProcessIdentity,
    same_process,
)
from supervisor.provider_routing import direct_anthropic_env
from supervisor.runtime_execution import (
    RuntimeExecution,
    execute_agent_task_blocking,
)
from supervisor.state import State


def _accepted_stdout(task_id: str) -> str:
    outcome = {
        "task_id": task_id,
        "summary": "Gate accepted.",
        "specialists": [
            {
                "name": "Planner",
                "decision": "accept plan",
                "objection": None,
            },
        ],
        "decisions": ["accept plan"],
        "objections": [],
        "changed_files": [],
        "tests": [],
        "test_status": "passed",
        "confidence": 0.99,
        "claims": [],
    }
    return build_lead_replay_stdout(
        "Accepted.\n"
        f"<dual_agent_outcome>{json.dumps(outcome)}</dual_agent_outcome>"
    )


def _spec(tmp_path: Path, task_id: str) -> DualAgentGateSpec:
    return DualAgentGateSpec(
        task_id=task_id,
        run_id=f"run-{task_id}",
        gate="intent",
        instruction="Review the intent.",
        cwd=tmp_path,
        required_planning_kinds=(),
        expected_specialists=("Planner",),
        expected_decisions=("accept plan",),
        expected_objections=(),
        timeout_s=30,
    )


class _NoEscalationNotifier:
    async def send_approval_prompt(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError(f"cancelled gate must not escalate: {kwargs}")


class _RecordingState:
    def __init__(self, path: Path) -> None:
        self._state = State(str(path))
        self._lock = threading.Lock()
        self.kinds: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._state, name)

    def write_event(self, *args: Any, **kwargs: Any) -> int:
        with self._lock:
            self.kinds.append(str(kwargs["kind"]))
        return self._state.write_event(*args, **kwargs)

    def snapshot(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self.kinds)


class _BlockingCancellableRunner:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.started = threading.Event()
        self.cancel_called = threading.Event()
        self.release = threading.Event()
        self.exited = threading.Event()

    def __call__(
        self,
        argv: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        self.started.set()
        try:
            if not self.release.wait(timeout=10):
                raise AssertionError("runner cancellation was not delivered")
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=self.stdout,
                stderr="",
            )
        finally:
            self.exited.set()

    def cancel(self) -> None:
        self.cancel_called.set()
        self.release.set()


class _ImmediateCancellableRunner:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.cancel_called = False

    def __call__(
        self,
        argv: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=self.stdout,
            stderr="",
        )

    def cancel(self) -> None:
        self.cancel_called = True


class _FailingCleanupRunner(_BlockingCancellableRunner):
    def __init__(self, stdout: str, cleanup_error: BaseException) -> None:
        super().__init__(stdout)
        self.cleanup_error = cleanup_error

    def cancel(self) -> None:
        self.cancel_called.set()
        self.release.set()
        raise self.cleanup_error


class _PostInvocationBlockingResult:
    def __init__(self, stdout: str) -> None:
        self.returncode = 0
        self.stderr = ""
        self._stdout = stdout
        self.read_started = threading.Event()
        self.release = threading.Event()
        self.read_finished = threading.Event()

    @property
    def stdout(self) -> str:
        self.read_started.set()
        try:
            if not self.release.wait(timeout=10):
                raise AssertionError("post-invocation gate join was not released")
            return self._stdout
        finally:
            self.read_finished.set()


class _PostInvocationBlockingRunner:
    def __init__(self, stdout: str) -> None:
        self.result = _PostInvocationBlockingResult(stdout)

    def __call__(
        self,
        _argv: list[str],
        **_kwargs: Any,
    ) -> _PostInvocationBlockingResult:
        return self.result


def test_incomplete_empty_containment_scan_does_not_report_quiescence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancellation = dual_agent_runner_module._GateCancellation()
    monkeypatch.setattr(
        dual_agent_runner_module,
        "scan_containment",
        lambda _containment_id: ContainmentSnapshot(
            processes=(),
            scan_complete=False,
            errors=("access_denied:123",),
        ),
    )

    assert cancellation._terminate_gate_processes({"containment-1"}) is False


@pytest.mark.asyncio
async def test_normal_completion_preserves_gate_result_and_state_writes(
    tmp_path: Path,
) -> None:
    state = _RecordingState(tmp_path / "state.db")
    runner = _ImmediateCancellableRunner(_accepted_stdout("gate-complete"))

    result = await run_dual_agent_gate_with_escalation(
        _spec(tmp_path, "gate-complete"),
        state=state,  # type: ignore[arg-type]
        notifier=_NoEscalationNotifier(),
        runner=runner,
    )

    assert result.status == "accepted"
    assert not runner.cancel_called
    assert state.snapshot() == (
        "dual_agent_planning_validation",
        "dual_agent_interaction_message",
        "dual_agent_interaction_message",
    )
    assert not (tmp_path / ".handoff" / ".dual-agent.lock").exists()


@pytest.mark.asyncio
async def test_cancellation_waits_for_gate_thread_releases_lock_and_blocks_late_writes(
    tmp_path: Path,
) -> None:
    state = _RecordingState(tmp_path / "state.db")
    runner = _BlockingCancellableRunner(_accepted_stdout("gate-cancel"))
    lock_path = tmp_path / ".handoff" / ".dual-agent.lock"
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-cancel"),
            state=state,  # type: ignore[arg-type]
            notifier=_NoEscalationNotifier(),
            runner=runner,
        )
    )

    assert await asyncio.to_thread(runner.started.wait, 2)
    assert lock_path.exists()
    writes_at_cancel = state.snapshot()
    gate_task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(gate_task, timeout=3)

        assert runner.cancel_called.is_set()
        assert runner.exited.is_set()
        assert not lock_path.exists()
        await asyncio.sleep(0)
        assert state.snapshot() == writes_at_cancel
    finally:
        runner.release.set()
        await asyncio.to_thread(runner.exited.wait, 2)


@pytest.mark.asyncio
async def test_cancellation_fails_closed_for_unreadable_new_marker_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_username = psutil.Process().username()

    class UnreadableNewProcess:
        pid = 3_999_991
        info = {
            "pid": pid,
            "username": current_username,
            "create_time": time.time() + 60,
        }

        def environ(self) -> dict[str, str]:
            raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(
        dual_agent_runner_module,
        "DUAL_AGENT_CANCELLATION_TIMEOUT_S",
        0.05,
    )
    monkeypatch.setattr(
        dual_agent_runner_module,
        "scan_containment",
        lambda _containment_id: ContainmentSnapshot(
            processes=(),
            scan_complete=True,
        ),
    )
    monkeypatch.setattr(
        dual_agent_runner_module.psutil,
        "process_iter",
        lambda _attrs: [UnreadableNewProcess()],
    )
    runner = _BlockingCancellableRunner(_accepted_stdout("gate-unreadable"))
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-unreadable"),
            state=State(str(tmp_path / "state.db")),
            notifier=_NoEscalationNotifier(),
            runner=runner,
        )
    )

    assert await asyncio.to_thread(runner.started.wait, 2)
    gate_task.cancel()

    with pytest.raises(
        DualAgentCancellationCleanupError,
        match="quiescent",
    ):
        await gate_task
    assert runner.exited.is_set()


@pytest.mark.asyncio
async def test_cancellation_fails_closed_for_unreadable_process_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_username = psutil.Process().username()

    class UnreadableIdentityProcess:
        pid = 3_999_992
        info = {
            "pid": pid,
            "username": current_username,
            "create_time": None,
        }

        def create_time(self) -> float:
            raise psutil.AccessDenied(pid=self.pid)

        def environ(self) -> dict[str, str]:
            raise AssertionError(
                "environment must not be read without a process identity"
            )

    monkeypatch.setattr(
        dual_agent_runner_module,
        "DUAL_AGENT_CANCELLATION_TIMEOUT_S",
        0.05,
    )
    monkeypatch.setattr(
        dual_agent_runner_module,
        "scan_containment",
        lambda _containment_id: ContainmentSnapshot(
            processes=(),
            scan_complete=True,
        ),
    )
    monkeypatch.setattr(
        dual_agent_runner_module.psutil,
        "process_iter",
        lambda _attrs: [UnreadableIdentityProcess()],
    )
    runner = _BlockingCancellableRunner(
        _accepted_stdout("gate-unreadable-identity"),
    )
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-unreadable-identity"),
            state=State(str(tmp_path / "state.db")),
            notifier=_NoEscalationNotifier(),
            runner=runner,
        )
    )

    assert await asyncio.to_thread(runner.started.wait, 2)
    gate_task.cancel()

    with pytest.raises(
        DualAgentCancellationCleanupError,
        match="quiescent",
    ) as exc_info:
        await gate_task
    assert exc_info.value.cleanup_quiescent is False
    assert exc_info.value.gate_thread_terminated is True
    assert runner.exited.is_set()
    assert not (tmp_path / ".handoff" / ".dual-agent.lock").exists()


@pytest.mark.asyncio
async def test_cancellation_ignores_process_that_disappears_during_identity_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_username = psutil.Process().username()

    class DisappearedProcess:
        pid = 3_999_993
        info = {
            "pid": pid,
            "username": current_username,
            "create_time": None,
        }

        def create_time(self) -> float:
            raise psutil.NoSuchProcess(pid=self.pid)

        def environ(self) -> dict[str, str]:
            raise AssertionError(
                "environment must not be read after process disappearance"
            )

    monkeypatch.setattr(
        dual_agent_runner_module,
        "DUAL_AGENT_CANCELLATION_TIMEOUT_S",
        0.5,
    )
    monkeypatch.setattr(
        dual_agent_runner_module,
        "scan_containment",
        lambda _containment_id: ContainmentSnapshot(
            processes=(),
            scan_complete=True,
        ),
    )
    monkeypatch.setattr(
        dual_agent_runner_module.psutil,
        "process_iter",
        lambda _attrs: [DisappearedProcess()],
    )
    runner = _BlockingCancellableRunner(
        _accepted_stdout("gate-disappeared-identity"),
    )
    lock_path = tmp_path / ".handoff" / ".dual-agent.lock"
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-disappeared-identity"),
            state=State(str(tmp_path / "state.db")),
            notifier=_NoEscalationNotifier(),
            runner=runner,
        )
    )

    assert await asyncio.to_thread(runner.started.wait, 2)
    gate_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await gate_task
    assert runner.exited.is_set()
    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_cancellation_surfaces_target_cleanup_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_error = RuntimeError("target cleanup failed")
    runner = _FailingCleanupRunner(
        _accepted_stdout("gate-cleanup-error"),
        cleanup_error,
    )
    monkeypatch.setattr(
        dual_agent_runner_module,
        "DUAL_AGENT_CANCELLATION_TIMEOUT_S",
        0.5,
    )
    monkeypatch.setattr(
        dual_agent_runner_module,
        "scan_containment",
        lambda _containment_id: ContainmentSnapshot(
            processes=(),
            scan_complete=True,
        ),
    )
    monkeypatch.setattr(
        dual_agent_runner_module.psutil,
        "process_iter",
        lambda _attrs: [],
    )
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-cleanup-error"),
            state=State(str(tmp_path / "state.db")),
            notifier=_NoEscalationNotifier(),
            runner=runner,
        )
    )

    assert await asyncio.to_thread(runner.started.wait, 2)
    gate_task.cancel()

    with pytest.raises(
        DualAgentCancellationCleanupError,
        match="target cleanup failed",
    ) as exc_info:
        await gate_task
    assert exc_info.value.__cause__ is cleanup_error
    assert exc_info.value.cleanup_quiescent is True
    assert exc_info.value.gate_thread_terminated is True
    assert runner.exited.is_set()


def test_target_cleanup_error_does_not_skip_containment_rescan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancellation = dual_agent_runner_module._GateCancellation()
    cleanup_error = RuntimeError("provider cancel failed")
    invocation_id = ""

    class FailingTarget:
        def cancel(self) -> None:
            cancellation._finish_invocation(invocation_id)
            raise cleanup_error

    invocation_id = cancellation._start_invocation(
        containment_id="containment-after-hook-error",
        cancel_target=FailingTarget(),
    )
    scans: list[set[str]] = []

    def terminate_gate_processes(
        containment_ids: set[str],
    ) -> bool:
        scans.append(set(containment_ids))
        return len(scans) >= 2

    monkeypatch.setattr(
        cancellation,
        "_terminate_gate_processes",
        terminate_gate_processes,
    )

    with pytest.raises(
        RuntimeError,
        match="provider cancel failed",
    ) as exc_info:
        cancellation.cancel_active_invocations(timeout_s=0.5)

    assert exc_info.value is cleanup_error
    assert len(scans) >= 3
    assert all(
        "containment-after-hook-error" in containment_ids
        for containment_ids in scans
    )


@pytest.mark.asyncio
async def test_cancellation_surfaces_handoff_lock_release_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _BlockingCancellableRunner(
        _accepted_stdout("gate-lock-release-error"),
    )
    lock_path = tmp_path / ".handoff" / ".dual-agent.lock"
    real_unlink = Path.unlink

    def fail_lock_release(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == lock_path:
            raise PermissionError("handoff lock release denied")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        dual_agent_runner_module,
        "DUAL_AGENT_CANCELLATION_TIMEOUT_S",
        0.5,
    )
    monkeypatch.setattr(
        dual_agent_runner_module,
        "scan_containment",
        lambda _containment_id: ContainmentSnapshot(
            processes=(),
            scan_complete=True,
        ),
    )
    monkeypatch.setattr(
        dual_agent_runner_module.psutil,
        "process_iter",
        lambda _attrs: [],
    )
    monkeypatch.setattr(Path, "unlink", fail_lock_release)
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-lock-release-error"),
            state=State(str(tmp_path / "state.db")),
            notifier=_NoEscalationNotifier(),
            runner=runner,
        )
    )

    assert await asyncio.to_thread(runner.started.wait, 2)
    assert lock_path.exists()
    gate_task.cancel()

    try:
        with pytest.raises(
            DualAgentCancellationCleanupError,
            match="gate_thread_terminated=True",
        ) as exc_info:
            await gate_task
        assert isinstance(exc_info.value.__cause__, PermissionError)
        assert "handoff lock release denied" in str(exc_info.value.__cause__)
        assert exc_info.value.cleanup_quiescent is True
        assert exc_info.value.gate_thread_terminated is True
        assert exc_info.value.phase == "gate_thread_cleanup"
        assert lock_path.exists()
    finally:
        os.unlink(lock_path)


@pytest.mark.asyncio
async def test_cancellation_bounds_post_invocation_gate_thread_join(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _PostInvocationBlockingRunner(
        _accepted_stdout("gate-post-invocation"),
    )
    monkeypatch.setattr(
        dual_agent_runner_module,
        "DUAL_AGENT_CANCELLATION_TIMEOUT_S",
        0.05,
    )
    monkeypatch.setattr(
        dual_agent_runner_module,
        "scan_containment",
        lambda _containment_id: ContainmentSnapshot(
            processes=(),
            scan_complete=True,
        ),
    )
    monkeypatch.setattr(
        dual_agent_runner_module.psutil,
        "process_iter",
        lambda _attrs: [],
    )
    lock_path = tmp_path / ".handoff" / ".dual-agent.lock"
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-post-invocation"),
            state=State(str(tmp_path / "state.db")),
            notifier=_NoEscalationNotifier(),
            runner=runner,
        )
    )

    assert await asyncio.to_thread(runner.result.read_started.wait, 2)
    assert lock_path.exists()
    started_at = time.monotonic()
    gate_task.cancel()

    async def repeat_cancellation() -> None:
        while not gate_task.done():
            gate_task.cancel()
            await asyncio.sleep(0.005)

    repeated_cancellation = asyncio.create_task(repeat_cancellation())
    try:
        with pytest.raises(
            DualAgentCancellationCleanupError,
            match="gate_thread_terminated=False",
        ) as exc_info:
            await gate_task
        elapsed_s = time.monotonic() - started_at
        assert elapsed_s < 0.5
        assert exc_info.value.cleanup_quiescent is True
        assert exc_info.value.gate_thread_terminated is False
        assert exc_info.value.phase == "gate_thread_join"
    finally:
        runner.result.release.set()
        await asyncio.to_thread(runner.result.read_finished.wait, 2)
        await repeated_cancellation

    lock_deadline = time.monotonic() + 2
    while lock_path.exists() and time.monotonic() < lock_deadline:
        await asyncio.sleep(0.01)
    assert not lock_path.exists()


_PROCESS_TREE_SCRIPT = """
import json
import os
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([
    sys.executable,
    "-c",
    "import time; time.sleep(300)",
])
Path(sys.argv[1]).write_text(
    json.dumps({
        "root_pid": os.getpid(),
        "root_pgid": os.getpgrp(),
        "child_pid": child.pid,
    }),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""


def _wait_for_process_tree(ready_path: Path) -> dict[str, int]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready_path.exists():
            try:
                return json.loads(ready_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        time.sleep(0.01)
    raise AssertionError("process tree did not become ready")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(os, "killpg"),
    reason="process-group cancellation requires POSIX process groups",
)
async def test_cancellation_terminates_legacy_subprocess_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_path = tmp_path / "legacy-tree.json"
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    claude = binary_dir / "claude"
    claude.write_text(
        f"#!{sys.executable}\n"
        + _PROCESS_TREE_SCRIPT.replace(
            "Path(sys.argv[1])",
            "(Path.cwd() / 'legacy-tree.json')",
        ),
        encoding="utf-8",
    )
    claude.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{binary_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    lock_path = tmp_path / ".handoff" / ".dual-agent.lock"
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-legacy-cancel"),
            state=State(str(tmp_path / "state.db")),
            notifier=_NoEscalationNotifier(),
        )
    )
    tree = await asyncio.to_thread(_wait_for_process_tree, ready_path)
    root_identity = ProcessIdentity(
        pid=int(tree["root_pid"]),
        started_at=psutil.Process(int(tree["root_pid"])).create_time(),
    )
    child_identity = ProcessIdentity(
        pid=int(tree["child_pid"]),
        started_at=psutil.Process(int(tree["child_pid"])).create_time(),
    )
    pgid = int(tree["root_pgid"])
    assert pgid == root_identity.pid
    assert lock_path.exists()
    gate_task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(gate_task, timeout=5)

        assert not same_process(root_identity)
        assert not same_process(child_identity)
        assert not lock_path.exists()
    finally:
        if same_process(root_identity):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if same_process(child_identity):
            try:
                os.kill(child_identity.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class _ProductionLikeRuntimeRunner:
    def __init__(self, binary: Path) -> None:
        self.binary = binary
        self.exited = threading.Event()

    def __call__(self, task: AgentTask) -> RuntimeExecution:
        environment = direct_anthropic_env({
            **os.environ,
            **{str(key): str(value) for key, value in task.env.items()},
        })
        assert any(
            ".codex-supervisor-dual-agent-" in path_part
            for path_part in environment["PATH"].split(os.pathsep)
        )
        controlled_task = replace(
            task,
            env=environment,
            inherit_env=False,
        )
        try:
            return execute_agent_task_blocking(
                ClaudeCodeRuntime(binary=str(self.binary)),
                controlled_task,
            )
        finally:
            self.exited.set()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(os, "killpg"),
    reason="process-group cancellation requires POSIX process groups",
)
async def test_cancellation_terminates_runtime_process_group_before_returning(
    tmp_path: Path,
) -> None:
    ready_path = tmp_path / "runtime-tree.json"
    binary = tmp_path / "fake-claude"
    binary.write_text(
        f"#!{sys.executable}\n"
        + _PROCESS_TREE_SCRIPT.replace(
            "Path(sys.argv[1])",
            "(Path.cwd() / 'runtime-tree.json')",
        ),
        encoding="utf-8",
    )
    binary.chmod(0o755)
    runtime_runner = _ProductionLikeRuntimeRunner(binary)
    lock_path = tmp_path / ".handoff" / ".dual-agent.lock"
    gate_task = asyncio.create_task(
        run_dual_agent_gate_with_escalation(
            _spec(tmp_path, "gate-runtime-cancel"),
            state=State(str(tmp_path / "state.db")),
            notifier=_NoEscalationNotifier(),
            runtime_runner=runtime_runner,
        )
    )

    tree = await asyncio.to_thread(_wait_for_process_tree, ready_path)
    root_identity = ProcessIdentity(
        pid=int(tree["root_pid"]),
        started_at=psutil.Process(int(tree["root_pid"])).create_time(),
    )
    child_identity = ProcessIdentity(
        pid=int(tree["child_pid"]),
        started_at=psutil.Process(int(tree["child_pid"])).create_time(),
    )
    pgid = int(tree["root_pgid"])
    assert pgid == root_identity.pid
    assert lock_path.exists()
    gate_task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(gate_task, timeout=5)

        assert runtime_runner.exited.is_set()
        assert not same_process(root_identity)
        assert not same_process(child_identity)
        assert not lock_path.exists()
    finally:
        if same_process(root_identity):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if same_process(child_identity):
            try:
                os.kill(child_identity.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await asyncio.to_thread(runtime_runner.exited.wait, 2)


def test_runtime_marker_env_falls_back_to_process_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(["/opt/homebrew/bin", "/usr/bin"]),
    )
    cancellation = dual_agent_runner_module._GateCancellation()

    environment = cancellation._tag_environment(
        {"ANTHROPIC_API_KEY": "test-key"},
        "containment-id",
        runtime_marker=True,
    )

    path_parts = environment["PATH"].split(os.pathsep)
    assert path_parts[0] == cancellation._path_marker
    assert "/opt/homebrew/bin" in path_parts
    assert "/usr/bin" in path_parts


def test_runtime_marker_env_prepends_marker_to_existing_task_path() -> None:
    cancellation = dual_agent_runner_module._GateCancellation()

    environment = cancellation._tag_environment(
        {"PATH": "/task/bin"},
        "containment-id",
        runtime_marker=True,
    )

    assert environment["PATH"].split(os.pathsep) == [
        cancellation._path_marker,
        "/task/bin",
    ]

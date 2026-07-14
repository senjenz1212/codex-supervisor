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

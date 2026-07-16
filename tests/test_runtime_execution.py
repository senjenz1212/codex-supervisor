from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from supervisor.agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    RuntimeEvent,
)
from supervisor.runtime_execution import execute_agent_task_blocking


REPO_ROOT = Path(__file__).resolve().parents[1]


def _task(cwd: Path) -> AgentTask:
    return AgentTask(
        task_id="task-1",
        instruction="exercise runtime",
        cwd=cwd,
        model="test",
    )


class _RecordingRuntime:
    kind = "recording"

    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []
        self.event = RuntimeEvent(
            kind="message",
            payload={"text": "done"},
            ts_ms=1,
        )
        self.handle = AgentRunHandle(
            run_id="run-1",
            task_id="task-1",
            runtime=self.kind,
            session_id="session-1",
            capabilities={"cancel": True, "stream": True},
        )
        self.result = AgentRunResult(
            run_id="run-1",
            task_id="task-1",
            runtime=self.kind,
            session_id="session-1",
            status="completed",
            output="done",
            events=(self.event,),
            started_at_ms=1,
            ended_at_ms=2,
            cost_usd=0.0,
            resolved_model="test",
            result_hash="result-hash",
        )

    def _record_thread(self) -> None:
        self.threads.append(threading.current_thread())

    async def start(self, task: AgentTask) -> AgentRunHandle:
        self._record_thread()
        return self.handle

    async def resume(
        self,
        handle: AgentRunHandle,
        instruction: str,
    ) -> None:
        raise AssertionError("resume is not used")

    async def cancel(self, handle: AgentRunHandle) -> None:
        raise AssertionError("cancel is not used")

    async def stream(
        self,
        handle: AgentRunHandle,
    ) -> AsyncIterator[RuntimeEvent]:
        self._record_thread()
        yield self.event

    async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
        self._record_thread()
        return self.result


def test_blocking_execution_runs_lifecycle_on_one_daemon_thread(
    tmp_path: Path,
) -> None:
    runtime = _RecordingRuntime()

    execution = execute_agent_task_blocking(runtime, _task(tmp_path))

    assert execution.handle is runtime.handle
    assert execution.events == (runtime.event,)
    assert execution.result is runtime.result
    assert len(runtime.threads) == 3
    assert len({thread.ident for thread in runtime.threads}) == 1
    assert runtime.threads[0] is not threading.current_thread()
    assert all(thread.daemon for thread in runtime.threads)


def test_blocking_execution_propagates_runtime_exception(
    tmp_path: Path,
) -> None:
    error = RuntimeError("runtime failed")

    class FailingRuntime(_RecordingRuntime):
        async def start(self, task: AgentTask) -> AgentRunHandle:
            self._record_thread()
            raise error

    runtime = FailingRuntime()

    with pytest.raises(RuntimeError) as exc_info:
        execute_agent_task_blocking(runtime, _task(tmp_path))

    assert exc_info.value is error
    assert runtime.threads[0].daemon is True


def test_keyboard_interrupt_abandons_uncooperative_worker_at_process_exit() -> None:
    script = textwrap.dedent(
        """
        from __future__ import annotations

        import asyncio
        import os
        import signal
        import threading
        import time
        from pathlib import Path

        import supervisor.runtime_execution as runtime_execution
        from supervisor.agent_runtime import AgentRunHandle, AgentTask


        cancel_started = threading.Event()


        class UncooperativeRuntime:
            kind = "uncooperative"

            async def start(self, task: AgentTask) -> AgentRunHandle:
                return AgentRunHandle(
                    run_id="run-1",
                    task_id=task.task_id,
                    runtime=self.kind,
                    session_id="session-1",
                    capabilities={"cancel": True, "stream": True},
                )

            async def resume(
                self,
                handle: AgentRunHandle,
                instruction: str,
            ) -> None:
                raise AssertionError("resume is not used")

            async def cancel(self, handle: AgentRunHandle) -> None:
                cancel_started.set()
                while True:
                    try:
                        await asyncio.sleep(3600)
                    except asyncio.CancelledError:
                        continue

            async def stream(self, handle: AgentRunHandle):
                await asyncio.Event().wait()
                if False:
                    yield

            async def collect(self, handle: AgentRunHandle):
                raise AssertionError("collect is not reached")


        runtime_execution.INTERRUPT_LOOP_READY_WAIT_S = 0.05
        runtime_execution.INTERRUPT_CLEANUP_WAIT_S = 0.05

        def interrupt_main() -> None:
            time.sleep(0.1)
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=interrupt_main, daemon=True).start()
        task = AgentTask(
            task_id="task-1",
            instruction="block forever",
            cwd=Path.cwd(),
            model="test",
        )
        try:
            runtime_execution.execute_agent_task_blocking(
                UncooperativeRuntime(),
                task,
            )
        except KeyboardInterrupt:
            if not cancel_started.wait(timeout=0.5):
                raise AssertionError("runtime cancellation was not requested")
            print("keyboard-interrupt-propagated", flush=True)
        else:
            raise AssertionError("KeyboardInterrupt did not propagate")
        """
    )

    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            "interpreter waited for the abandoned runtime worker at exit; "
            f"stdout={exc.stdout!r} stderr={exc.stderr!r}"
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "keyboard-interrupt-propagated"

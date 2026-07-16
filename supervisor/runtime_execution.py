"""Shared execution helpers for synchronous code using ``AgentRuntime``."""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentRuntime,
    AgentTask,
    RuntimeEvent,
)
from .runtime_cleanup import (
    CANCEL_CLEANUP_DEADLINE_S,
    cancel_runtime_after_failure,
)

log = logging.getLogger(__name__)

INTERRUPT_LOOP_READY_WAIT_S = 5.0
INTERRUPT_CLEANUP_WAIT_S = CANCEL_CLEANUP_DEADLINE_S + 5.0


@dataclass(frozen=True)
class RuntimeExecution:
    handle: AgentRunHandle
    events: tuple[RuntimeEvent, ...]
    result: AgentRunResult


RuntimeTaskRunner = Callable[[AgentTask], RuntimeExecution]
RuntimeFactory = Callable[[], AgentRuntime]


async def execute_agent_task(
    runtime: AgentRuntime,
    task: AgentTask,
) -> RuntimeExecution:
    """Execute one task through the complete provider-neutral lifecycle."""

    handle = await runtime.start(task)
    try:
        events = tuple(
            [event async for event in runtime.stream(handle)]
        )
        result = await runtime.collect(handle)
    except BaseException:
        import logging

        await cancel_runtime_after_failure(
            runtime,
            handle,
            logger=logging.getLogger(__name__),
        )
        raise
    return RuntimeExecution(
        handle=handle,
        events=events,
        result=result,
    )


def execute_agent_task_blocking(
    runtime: AgentRuntime,
    task: AgentTask,
) -> RuntimeExecution:
    """Run an async runtime from legacy synchronous orchestration safely.

    A dedicated daemon thread owns the event loop so this remains valid when
    the synchronous caller itself is reached from an already-running async
    loop. If cancellation cannot stop that thread, interpreter shutdown does
    not wait for it.
    """

    loop_state: dict[str, object] = {}
    loop_ready = threading.Event()

    async def _execute() -> RuntimeExecution:
        loop_state["loop"] = asyncio.get_running_loop()
        loop_state["task"] = asyncio.current_task()
        loop_ready.set()
        return await execute_agent_task(runtime, task)

    future: concurrent.futures.Future[RuntimeExecution] = (
        concurrent.futures.Future()
    )

    def _run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            result = asyncio.run(_execute())
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

    worker = threading.Thread(
        target=_run,
        name="agent-runtime-execution",
        daemon=True,
    )
    worker.start()
    interrupted = False
    try:
        try:
            return future.result()
        except KeyboardInterrupt:
            interrupted = True
            future.cancel()
            deadline = time.monotonic() + INTERRUPT_CLEANUP_WAIT_S
            loop_ready.wait(
                timeout=min(
                    INTERRUPT_LOOP_READY_WAIT_S,
                    max(0.0, deadline - time.monotonic()),
                )
            )
            loop = loop_state.get("loop")
            inner_task = loop_state.get("task")
            if isinstance(loop, asyncio.AbstractEventLoop) and isinstance(
                inner_task, asyncio.Task
            ):
                try:
                    loop.call_soon_threadsafe(inner_task.cancel)
                except RuntimeError:
                    pass
            done, _pending = concurrent.futures.wait(
                (future,),
                timeout=max(0.0, deadline - time.monotonic()),
            )
            if not done:
                log.error(
                    "agent runtime cancellation unconfirmed %.1fs after "
                    "KeyboardInterrupt; abandoning runtime thread: "
                    "task_id=%s",
                    INTERRUPT_CLEANUP_WAIT_S,
                    task.task_id,
                )
            raise
    finally:
        if not interrupted:
            worker.join()


def runtime_task_runner(factory: RuntimeFactory) -> RuntimeTaskRunner:
    """Create a fresh runtime per synchronous invocation."""

    def _run(task: AgentTask) -> RuntimeExecution:
        return execute_agent_task_blocking(factory(), task)

    return _run


__all__ = [
    "RuntimeExecution",
    "RuntimeFactory",
    "RuntimeTaskRunner",
    "execute_agent_task",
    "execute_agent_task_blocking",
    "runtime_task_runner",
]

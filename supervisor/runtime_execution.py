"""Shared execution helpers for synchronous code using ``AgentRuntime``."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from .agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentRuntime,
    AgentTask,
    RuntimeEvent,
)
from .runtime_cleanup import cancel_runtime_after_failure


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

    A dedicated thread owns the event loop so this remains valid when the
    synchronous caller itself is reached from an already-running async loop.
    """

    with ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="agent-runtime-execution",
    ) as pool:
        return pool.submit(
            asyncio.run,
            execute_agent_task(runtime, task),
        ).result()


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

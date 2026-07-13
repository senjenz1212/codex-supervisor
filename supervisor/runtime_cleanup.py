"""Failure-path helpers for provider-neutral agent runtimes."""
from __future__ import annotations

import asyncio
import logging

from .agent_runtime import AgentRunHandle, AgentRuntime


async def cancel_runtime_after_failure(
    runtime: AgentRuntime,
    handle: AgentRunHandle,
    *,
    logger: logging.Logger,
) -> None:
    """Finish runtime cancellation without masking the caller's exception.

    Callers use this helper from an active ``except BaseException`` block and
    then re-raise.  Shielding keeps a cancelled caller from cancelling the
    cleanup task itself; the loop also waits through a second cancellation so
    workspace teardown cannot race a still-running agent.
    """

    cleanup = asyncio.create_task(runtime.cancel(handle))
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    try:
        cleanup.result()
    except BaseException:
        logger.warning(
            "runtime cancellation cleanup failed: run_id=%s",
            handle.run_id,
            exc_info=True,
        )

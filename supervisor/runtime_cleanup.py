"""Failure-path helpers for provider-neutral agent runtimes."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .agent_runtime import AgentRunHandle, AgentRuntime


CANCEL_CLEANUP_DEADLINE_S = 30.0
_BACKGROUND_CLEANUPS: set[asyncio.Task[None]] = set()


@dataclass(frozen=True)
class RuntimeCleanupResult:
    confirmed: bool
    reason: str


def _finish_background_cleanup(
    task: asyncio.Task[None],
    *,
    logger: logging.Logger,
    run_id: str,
) -> None:
    _BACKGROUND_CLEANUPS.discard(task)
    try:
        task.result()
    except BaseException:
        logger.error(
            "background runtime containment cleanup failed: run_id=%s",
            run_id,
            exc_info=True,
        )
    else:
        logger.info(
            "background runtime containment cleanup completed: run_id=%s",
            run_id,
        )


async def cancel_runtime_after_failure(
    runtime: AgentRuntime,
    handle: AgentRunHandle,
    *,
    logger: logging.Logger,
    deadline_s: float = CANCEL_CLEANUP_DEADLINE_S,
) -> RuntimeCleanupResult:
    """Finish runtime cancellation without masking the caller's exception.

    Callers use this helper from an active ``except BaseException`` block and
    then re-raise. A separately owned cleanup task survives caller
    cancellation; the loop also waits through repeated cancellation so a
    confirmed result means the runtime's cancellation contract completed.
    The hard deadline returns an explicit unconfirmed result while the owned
    cleanup task continues in the background. Callers that own a workspace
    must quarantine it instead of deleting or reusing it when unconfirmed.
    """

    cleanup = asyncio.create_task(runtime.cancel(handle))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(deadline_s))
    while not cleanup.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            _BACKGROUND_CLEANUPS.add(cleanup)
            cleanup.add_done_callback(
                lambda task: _finish_background_cleanup(
                    task,
                    logger=logger,
                    run_id=handle.run_id,
                )
            )
            logger.error(
                "runtime cancellation cleanup is unconfirmed after %.3fs; "
                "background containment reaper retained: run_id=%s",
                deadline_s,
                handle.run_id,
            )
            return RuntimeCleanupResult(
                confirmed=False,
                reason="cleanup_deadline_exceeded",
            )
        try:
            done, _pending = await asyncio.wait(
                (cleanup,),
                timeout=remaining,
            )
            if not done:
                continue
        except asyncio.CancelledError:
            continue
    try:
        cleanup.result()
    except BaseException:
        logger.warning(
            "runtime cancellation cleanup failed: run_id=%s",
            handle.run_id,
            exc_info=True,
        )
        return RuntimeCleanupResult(
            confirmed=False,
            reason="cleanup_failed",
        )
    return RuntimeCleanupResult(
        confirmed=True,
        reason="containment_reap_confirmed",
    )

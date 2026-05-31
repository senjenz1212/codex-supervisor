"""Runtime health helpers for long-running supervisor subsystems."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .state import State


SUPERVISOR_RUNTIME_RUN_ID = "supervisor_runtime"
log = logging.getLogger(__name__)


def record_subsystem_health(
    state: State,
    *,
    subsystem: str,
    status: str,
    reason: str,
    run_id: str = SUPERVISOR_RUNTIME_RUN_ID,
    details: dict[str, Any] | None = None,
    tail_path: str | None = None,
    tail_offset: int | None = None,
) -> int | None:
    """Record runtime health without letting audit failure kill the subsystem."""
    payload = {
        "subsystem": subsystem,
        "status": status,
        "reason": reason,
        "occurred_at": int(time.time()),
        "details": details or {},
    }
    try:
        if tail_path is not None and tail_offset is not None:
            return state.write_event_and_tail_offset(
                run_id=run_id,
                source="supervisor_runtime",
                kind="supervisor_subsystem_health",
                payload=payload,
                path=tail_path,
                byte_offset=tail_offset,
            )
        return state.write_event(
            run_id=run_id,
            source="supervisor_runtime",
            kind="supervisor_subsystem_health",
            payload=payload,
        )
    except Exception:
        log.exception("failed to write subsystem health event for %s", subsystem)
        return None


async def run_fatal_subsystem(
    subsystem: str,
    factory: Callable[[], Awaitable[None]],
    *,
    state: State,
    run_id: str = SUPERVISOR_RUNTIME_RUN_ID,
) -> None:
    """Run a subsystem that should bring the daemon down if it exits."""
    try:
        await factory()
    except asyncio.CancelledError:
        record_subsystem_health(
            state,
            subsystem=subsystem,
            status="stopped",
            reason="cancelled",
            run_id=run_id,
        )
        raise
    except Exception as e:
        log.exception("fatal subsystem %s failed: %s", subsystem, e)
        record_subsystem_health(
            state,
            subsystem=subsystem,
            status="failed",
            reason="runtime_exception",
            run_id=run_id,
            details={
                "exception_type": type(e).__name__,
                "error": str(e),
            },
        )
        raise

    record_subsystem_health(
        state,
        subsystem=subsystem,
        status="failed",
        reason="returned_unexpectedly",
        run_id=run_id,
    )
    raise RuntimeError(f"fatal subsystem {subsystem} returned unexpectedly")


async def run_restartable_subsystem(
    subsystem: str,
    factory: Callable[[], Awaitable[None]],
    *,
    state: State,
    run_id: str = SUPERVISOR_RUNTIME_RUN_ID,
    initial_backoff_s: float = 1.0,
    max_backoff_s: float = 30.0,
) -> None:
    """Run a long-lived subsystem forever, recording and restarting failures."""
    backoff = max(0.0, initial_backoff_s)
    max_backoff = max(0.0, max_backoff_s)
    while True:
        try:
            await factory()
            record_subsystem_health(
                state,
                subsystem=subsystem,
                status="degraded",
                reason="returned_unexpectedly",
                run_id=run_id,
            )
        except asyncio.CancelledError:
            record_subsystem_health(
                state,
                subsystem=subsystem,
                status="stopped",
                reason="cancelled",
                run_id=run_id,
            )
            raise
        except Exception as e:
            log.exception("restartable subsystem %s failed: %s", subsystem, e)
            record_subsystem_health(
                state,
                subsystem=subsystem,
                status="degraded",
                reason="runtime_exception",
                run_id=run_id,
                details={
                    "exception_type": type(e).__name__,
                    "error": str(e),
                },
            )

        record_subsystem_health(
            state,
            subsystem=subsystem,
            status="restarting",
            reason="bounded_backoff",
            run_id=run_id,
            details={"backoff_s": backoff},
        )
        if backoff:
            await asyncio.sleep(backoff)
        backoff = min(max_backoff, max(1.0, backoff * 2 if backoff else 1.0))

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from supervisor.state import State


@pytest.mark.asyncio
async def test_restartable_subsystem_records_failure_and_restarts(tmp_path):
    from supervisor.runtime_health import run_restartable_subsystem

    state = State(str(tmp_path / "state.db"))
    attempts = 0
    restarted = asyncio.Event()

    async def subsystem():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary rollout transport failure")
        restarted.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        run_restartable_subsystem(
            "rollout_watcher",
            subsystem,
            state=state,
            run_id="supervisor-runtime-test",
            initial_backoff_s=0,
            max_backoff_s=0,
        )
    )
    try:
        await asyncio.wait_for(restarted.wait(), timeout=1)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert attempts == 2
    events = [
        json.loads(row["payload_json"])
        for row in state._conn.execute(
            "SELECT kind, payload_json FROM events WHERE kind='supervisor_subsystem_health'"
        )
    ]
    assert any(
        event["subsystem"] == "rollout_watcher"
        and event["status"] == "degraded"
        and event["reason"] == "runtime_exception"
        for event in events
    )
    assert any(
        event["subsystem"] == "rollout_watcher"
        and event["status"] == "restarting"
        for event in events
    )


@pytest.mark.asyncio
async def test_fatal_subsystem_records_clean_return_as_failed(tmp_path):
    from supervisor.runtime_health import run_fatal_subsystem

    state = State(str(tmp_path / "state.db"))

    async def subsystem():
        return None

    with pytest.raises(RuntimeError, match="hook_server returned unexpectedly"):
        await run_fatal_subsystem(
            "hook_server",
            subsystem,
            state=state,
            run_id="supervisor-runtime-test",
        )

    payload = json.loads(state._conn.execute(
        "SELECT payload_json FROM events WHERE kind='supervisor_subsystem_health'"
    ).fetchone()["payload_json"])
    assert payload["subsystem"] == "hook_server"
    assert payload["status"] == "failed"
    assert payload["reason"] == "returned_unexpectedly"


@pytest.mark.asyncio
async def test_fatal_subsystem_records_exception_before_reraising(tmp_path):
    from supervisor.runtime_health import run_fatal_subsystem

    state = State(str(tmp_path / "state.db"))

    async def subsystem():
        raise RuntimeError("port already in use")

    with pytest.raises(RuntimeError, match="port already in use"):
        await run_fatal_subsystem(
            "hook_server",
            subsystem,
            state=state,
            run_id="supervisor-runtime-test",
        )

    payload = json.loads(state._conn.execute(
        "SELECT payload_json FROM events WHERE kind='supervisor_subsystem_health'"
    ).fetchone()["payload_json"])
    assert payload["subsystem"] == "hook_server"
    assert payload["status"] == "failed"
    assert payload["reason"] == "runtime_exception"
    assert payload["details"]["error"] == "port already in use"

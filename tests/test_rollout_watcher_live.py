"""Codex rollout watcher live-ingestion tests.

This is the Codex Desktop monitoring boundary: a rollout JSONL file appears or
grows under the sessions root, and the supervisor records exactly-once events,
an immutable run snapshot, and the durable tail offset used after daemon restart.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

import pytest

from supervisor.rollout_watcher import RolloutWatcher
from supervisor.state import State


@pytest.mark.asyncio
async def test_rollout_watcher_registers_snapshot_and_persists_offsets(tmp_path):
    db_path = str(tmp_path / "state.db")
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "05" / "19"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "123e4567-e89b-12d3-a456-426614174000"
    rollout = rollout_dir / f"rollout-2026-05-19T10-00-00-{session_id}.jsonl"
    registry = registry_dir / f"{session_id}.json"
    registry.write_text(json.dumps({
        "task": "Refactor auth login",
        "scope_contract": {
            "allowed_paths": ["src/auth/"],
            "related_paths": ["tests/auth/"],
            "protected_paths": ["src/payments/"],
            "never_touch_patterns": ["**/.env*"],
        },
    }))
    rollout.write_text(json.dumps({
        "type": "file_change",
        "path": "src/auth/login.py",
    }) + "\n")

    state1 = State(db_path)
    watcher1 = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state1,
    )
    await watcher1._drain_file(rollout)

    run = state1.get_run_by_session(session_id)
    assert run is not None
    snapshot = state1.get_run_snapshot(run["run_id"])
    assert snapshot is not None
    scope = json.loads(snapshot["scope_contract_json"])
    assert scope["allowed_paths"] == ["src/auth/"]
    assert scope["protected_paths"] == ["src/payments/"]
    assert state1.get_tail_offset(str(rollout)) == rollout.stat().st_size
    assert state1._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1

    # Simulate daemon restart: new State + new watcher should resume from the
    # durable offset instead of re-ingesting the same rollout line.
    del state1
    state2 = State(db_path)
    watcher2 = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state2,
    )
    await watcher2._drain_file(rollout)

    assert state2._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_rollout_watcher_startup_skips_old_unseen_history(tmp_path):
    """First startup on an existing machine must not import every old rollout.

    Old unseen files get their durable tail offset advanced to EOF, so future
    appends are still observed without flooding SQLite with historical runs.
    """
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "05" / "01"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "ffffffff-1111-2222-3333-444444444444"
    rollout = rollout_dir / f"rollout-2026-05-01T10-00-00-{session_id}.jsonl"
    rollout.write_text(json.dumps({"type": "message", "text": "old run"}) + "\n")
    old_ts = time.time() - 86_400
    os.utime(rollout, (old_ts, old_ts))

    state = State(str(tmp_path / "state.db"))
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
        startup_backfill_s=300,
    )

    await watcher._initial_backfill()

    assert state._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert state._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert state.get_tail_offset(str(rollout)) == rollout.stat().st_size


@pytest.mark.asyncio
async def test_rollout_watcher_sweep_drains_known_file_growth_without_watch_event(tmp_path):
    """A missed fs event must not leave a known active rollout permanently stale."""
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "05" / "19"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "aaaaaaaa-1111-2222-3333-444444444444"
    rollout = rollout_dir / f"rollout-2026-05-19T10-00-00-{session_id}.jsonl"
    first_line = json.dumps({"type": "message", "text": "already ingested"}) + "\n"
    second_line = json.dumps({"type": "task_complete", "text": "missed append"}) + "\n"
    rollout.write_text(first_line)

    state = State(str(tmp_path / "state.db"))
    state.set_tail_offset(str(rollout), len(first_line))
    with rollout.open("a") as f:
        f.write(second_line)

    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher.sweep_once()

    assert state._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    row = state._conn.execute("SELECT kind, payload_json FROM events").fetchone()
    assert row["kind"] == "task_complete"
    assert "missed append" in row["payload_json"]
    assert state.get_tail_offset(str(rollout)) == rollout.stat().st_size


@pytest.mark.asyncio
async def test_rollout_watcher_callback_failure_records_health_without_replaying_line(tmp_path):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "05" / "19"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "bbbbbbbb-1111-2222-3333-444444444444"
    rollout = rollout_dir / f"rollout-2026-05-19T10-00-00-{session_id}.jsonl"
    rollout.write_text(json.dumps({"type": "message", "text": "callback fails"}) + "\n")

    state = State(str(tmp_path / "state.db"))

    async def failing_callback(run_id: str, event: dict):
        raise RuntimeError("telegram progress transport closed")

    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
        on_event=failing_callback,
    )

    await watcher._drain_file(rollout)
    await watcher._drain_file(rollout)

    events = state._conn.execute(
        "SELECT kind, payload_json FROM events ORDER BY event_id ASC"
    ).fetchall()
    assert [row["kind"] for row in events].count("message") == 1
    assert state.get_tail_offset(str(rollout)) == rollout.stat().st_size
    health = [
        json.loads(row["payload_json"])
        for row in events
        if row["kind"] == "supervisor_subsystem_health"
    ]
    assert health
    assert health[0]["subsystem"] == "rollout_watcher.on_event"
    assert health[0]["status"] == "degraded"
    assert health[0]["reason"] == "callback_exception"


@pytest.mark.asyncio
async def test_rollout_watcher_guarded_sweep_records_failure_and_continues(tmp_path):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    sessions_root.mkdir()
    registry_dir.mkdir()
    state = State(str(tmp_path / "state.db"))
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    async def failing_sweep_once():
        raise RuntimeError("watchfiles backend dropped")

    watcher.sweep_once = failing_sweep_once  # type: ignore[method-assign]

    await watcher.guarded_sweep_once()

    row = state._conn.execute(
        "SELECT payload_json FROM events WHERE kind='supervisor_subsystem_health'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["subsystem"] == "rollout_watcher.sweep"
    assert payload["status"] == "degraded"
    assert payload["reason"] == "sweep_exception"


@pytest.mark.asyncio
async def test_rollout_watcher_malformed_json_records_health_and_advances_offset(tmp_path):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "05" / "19"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "cccccccc-1111-2222-3333-444444444444"
    rollout = rollout_dir / f"rollout-2026-05-19T10-00-00-{session_id}.jsonl"
    rollout.write_text("{not valid json}\n")

    state = State(str(tmp_path / "state.db"))
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)
    await watcher._drain_file(rollout)

    rows = state._conn.execute(
        "SELECT kind, payload_json FROM events ORDER BY event_id ASC"
    ).fetchall()
    assert [row["kind"] for row in rows] == ["supervisor_subsystem_health"]
    payload = json.loads(rows[0]["payload_json"])
    assert payload["subsystem"] == "rollout_watcher.parse"
    assert payload["status"] == "degraded"
    assert payload["reason"] == "json_decode_exception"
    assert state.get_tail_offset(str(rollout)) == rollout.stat().st_size


@pytest.mark.asyncio
async def test_rollout_watcher_read_failure_records_health(tmp_path, monkeypatch):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "05" / "19"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "dddddddd-1111-2222-3333-444444444444"
    rollout = rollout_dir / f"rollout-2026-05-19T10-00-00-{session_id}.jsonl"
    rollout.write_text(json.dumps({"type": "message"}) + "\n")

    state = State(str(tmp_path / "state.db"))
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )
    real_open = open

    def failing_open(path, *args, **kwargs):
        if Path(path) == rollout:
            raise OSError("temporary filesystem transport failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    await watcher._drain_file(rollout)

    row = state._conn.execute(
        "SELECT payload_json FROM events WHERE kind='supervisor_subsystem_health'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["subsystem"] == "rollout_watcher.drain"
    assert payload["status"] == "degraded"
    assert payload["reason"] == "read_exception"
    assert state.get_tail_offset(str(rollout)) == 0

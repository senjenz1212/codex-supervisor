"""Codex rollout watcher live-ingestion tests.

This is the Codex Desktop monitoring boundary: a rollout JSONL file appears or
grows under the sessions root, and the supervisor records exactly-once events,
an immutable run snapshot, and the durable tail offset used after daemon restart.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
from pathlib import Path
import uuid

import pytest

from supervisor.run_registry import (
    PENDING_SESSION_SOURCE,
    REUSABLE_SESSION_COMPLETION_POLICY,
    SINGLE_TURN_COMPLETION_POLICY,
    WORKFLOW_AGGREGATE_COMPLETION_POLICY,
    register_submitted_workflow,
    register_workflow_runtime_session,
)
from supervisor.rollout_watcher import RolloutWatcher
from supervisor.state import State
from supervisor.target.types import ScopeContract


def _register_rollout(
    *,
    state: State,
    registry_dir: Path,
    session_id: str,
    cwd: Path,
    task: str = "Monitor registered rollout",
    scope: ScopeContract | None = None,
) -> str:
    run_id = f"workflow-{session_id}"
    register_submitted_workflow(
        state=state,
        registry_dir=registry_dir,
        workflow_run_id=run_id,
        target_session_id=session_id,
        task_id=f"task-{session_id}",
        task=task,
        target_kind="codex",
        cwd=cwd,
        session_id_source="test",
        scope_contract=scope,
    )
    return run_id


def _watcher(tmp_path: Path) -> RolloutWatcher:
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    sessions_root.mkdir(exist_ok=True)
    registry_dir.mkdir(exist_ok=True)
    return RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=State(str(tmp_path / "state.db")),
    )


@pytest.mark.asyncio
async def test_deleted_path_prunes_idle_drain_lock(tmp_path):
    watcher = _watcher(tmp_path)
    path = tmp_path / "sessions" / "rollout-idle.jsonl"

    async with watcher._drain_path_lock(path):
        assert path in watcher._drain_locks
    assert path not in watcher._drain_locks

    watcher._forget_deleted_path(path)

    assert path not in watcher._drain_locks


@pytest.mark.asyncio
async def test_deleted_path_prunes_lock_after_active_drain_finishes(tmp_path):
    watcher = _watcher(tmp_path)
    path = tmp_path / "sessions" / "rollout-active.jsonl"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock() -> None:
        async with watcher._drain_path_lock(path):
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold_lock())
    await entered.wait()

    watcher._forget_deleted_path(path)
    assert path in watcher._drain_locks

    release.set()
    await task

    assert path not in watcher._drain_locks


@pytest.mark.asyncio
async def test_delete_recreate_keeps_one_serialization_lock(tmp_path):
    watcher = _watcher(tmp_path)
    path = tmp_path / "sessions" / "rollout-recreated.jsonl"
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_drain() -> None:
        async with watcher._drain_path_lock(path):
            first_entered.set()
            await release_first.wait()

    async def recreated_drain() -> None:
        async with watcher._drain_path_lock(path):
            second_entered.set()

    first = asyncio.create_task(first_drain())
    await first_entered.wait()
    watcher._forget_deleted_path(path)
    second = asyncio.create_task(recreated_drain())
    await asyncio.sleep(0)

    assert not second_entered.is_set()

    release_first.set()
    await asyncio.gather(first, second)

    assert second_entered.is_set()
    assert path not in watcher._drain_locks


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
    state1 = State(db_path)
    _register_rollout(
        state=state1,
        registry_dir=registry_dir,
        session_id=session_id,
        cwd=tmp_path,
        task="Refactor auth login",
        scope=ScopeContract(
            allowed_paths=("src/auth/",),
            related_paths=("tests/auth/",),
            protected_paths=("src/payments/",),
            never_touch_patterns=("**/.env*",),
        ),
    )
    rollout.write_text(json.dumps({
        "type": "file_change",
        "path": "src/auth/login.py",
    }) + "\n")

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
    _register_rollout(
        state=state,
        registry_dir=registry_dir,
        session_id=session_id,
        cwd=tmp_path,
    )
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
    assert row["kind"] == "turn.completed"
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
    _register_rollout(
        state=state,
        registry_dir=registry_dir,
        session_id=session_id,
        cwd=tmp_path,
    )

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
    _register_rollout(
        state=state,
        registry_dir=registry_dir,
        session_id=session_id,
        cwd=tmp_path,
    )
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completion_policy", "terminal_kind", "expected_status"),
    tuple(
        (
            completion_policy,
            terminal_kind,
            (
                terminal_status
                if completion_policy == SINGLE_TURN_COMPLETION_POLICY
                else "running"
            ),
        )
        for completion_policy in (
            SINGLE_TURN_COMPLETION_POLICY,
            REUSABLE_SESSION_COMPLETION_POLICY,
            WORKFLOW_AGGREGATE_COMPLETION_POLICY,
        )
        for terminal_kind, terminal_status in (
            ("turn.completed", "completed"),
            ("turn.failed", "failed"),
            ("run.completed", "completed"),
            ("run.failed", "failed"),
            ("run.cancelled", "cancelled"),
        )
    ),
)
async def test_completion_policy_controls_every_rollout_terminal(
    tmp_path,
    completion_policy,
    terminal_kind,
    expected_status,
):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "07" / "13"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()
    session_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{completion_policy}:{terminal_kind}",
        )
    )
    workflow_run_id = f"workflow-{session_id}"
    state = State(str(tmp_path / "state.db"))
    register_submitted_workflow(
        state=state,
        registry_dir=registry_dir,
        workflow_run_id=workflow_run_id,
        target_session_id=session_id,
        task_id=f"task-{session_id}",
        task="Respect the registered completion policy.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source="controlled_test",
        completion_policy=completion_policy,
    )
    rollout = rollout_dir / f"rollout-2026-07-13T10-00-00-{session_id}.jsonl"
    terminal_line = json.dumps({"type": terminal_kind}) + "\n"
    rollout.write_text(terminal_line + terminal_line, encoding="utf-8")
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)

    assert state.get_run(workflow_run_id)["status"] == expected_status
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE run_id=? AND kind=?",
        (workflow_run_id, terminal_kind),
    ).fetchone()[0] == 2
    assert state._conn.execute(
        """SELECT COUNT(*) FROM decision_outbox
             WHERE run_id=? AND kind='evaluate_run'""",
        (workflow_run_id,),
    ).fetchone()[0] == (
        1 if completion_policy == SINGLE_TURN_COMPLETION_POLICY else 0
    )


@pytest.mark.asyncio
async def test_corrupt_quarantine_payload_reconciles_offsets_and_stops_retrying(
    tmp_path,
):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "07" / "13"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "eeeeeeee-1111-2222-3333-444444444444"
    rollout = rollout_dir / f"rollout-2026-07-13T10-00-00-{session_id}.jsonl"
    raw_line = (json.dumps({"type": "message", "text": "quarantine me"}) + "\n").encode()
    rollout.write_bytes(raw_line)
    state = State(str(tmp_path / "state.db"))
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)

    quarantine_path = next((registry_dir / ".rollout-quarantine").glob("*.json"))
    records = quarantine_path.read_text(encoding="utf-8").splitlines()
    corrupt_chunk = json.loads(records[1])
    corrupt_chunk["raw_bytes_b64"] = "eA=="
    quarantine_path.write_text(
        records[0] + "\n" + json.dumps(corrupt_chunk) + "\n",
        encoding="utf-8",
    )
    _register_rollout(
        state=state,
        registry_dir=registry_dir,
        session_id=session_id,
        cwd=tmp_path,
    )
    rollout.unlink()
    # Model a crash window where memory advanced beyond both the durable tail
    # and the corrupt sidecar's advertised end. Recovery must never move back.
    durable_before_replay = len(raw_line) + 7
    in_memory_before_replay = durable_before_replay + 11
    state.set_tail_offset(str(rollout), durable_before_replay)

    restarted = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )
    restarted.offsets[rollout] = in_memory_before_replay
    await restarted.sweep_once()

    assert not quarantine_path.exists()
    assert state.get_tail_offset(str(rollout)) == in_memory_before_replay
    assert restarted.offsets[rollout] == in_memory_before_replay
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE source='rollout'"
    ).fetchone()[0] == 0
    health_count = state._conn.execute(
        """SELECT COUNT(*) FROM events
             WHERE kind='supervisor_subsystem_health'
               AND payload_json LIKE '%quarantine_payload_corrupt%'"""
    ).fetchone()[0]
    assert health_count == 1

    await restarted.sweep_once()

    assert state._conn.execute(
        """SELECT COUNT(*) FROM events
             WHERE kind='supervisor_subsystem_health'
               AND payload_json LIKE '%quarantine_payload_corrupt%'"""
    ).fetchone()[0] == health_count


@pytest.mark.asyncio
async def test_torn_final_quarantine_append_salvages_all_intact_chunks(tmp_path):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "07" / "13"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "ffffffff-1111-2222-3333-444444444444"
    rollout = rollout_dir / f"rollout-2026-07-13T10-00-00-{session_id}.jsonl"
    first_line = (
        json.dumps({"type": "message", "text": "first durable chunk"}) + "\n"
    ).encode()
    second_line = (
        json.dumps({"type": "message", "text": "second durable chunk"}) + "\n"
    ).encode()
    rollout.write_bytes(first_line)
    state = State(str(tmp_path / "state.db"))
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)
    with rollout.open("ab") as handle:
        handle.write(second_line)
    await watcher._drain_file(rollout)

    quarantine_path = next((registry_dir / ".rollout-quarantine").glob("*.json"))
    assert len(quarantine_path.read_text(encoding="utf-8").splitlines()) == 3
    with quarantine_path.open("ab") as handle:
        handle.write(b'{"end_offset":')
    _register_rollout(
        state=state,
        registry_dir=registry_dir,
        session_id=session_id,
        cwd=tmp_path,
    )
    rollout.unlink()

    restarted = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )
    await restarted.sweep_once()

    events = state._conn.execute(
        """SELECT kind, payload_json
             FROM events
            WHERE source='rollout'
            ORDER BY event_id"""
    ).fetchall()
    assert [row["kind"] for row in events] == ["message", "message"]
    assert [
        json.loads(row["payload_json"])["text"]
        for row in events
    ] == ["first durable chunk", "second durable chunk"]
    assert state.get_tail_offset(str(rollout)) == len(first_line) + len(second_line)
    assert restarted.offsets[rollout] == len(first_line) + len(second_line)
    assert not quarantine_path.exists()


@pytest.mark.asyncio
async def test_registration_identity_overrides_conflicting_rollout_fields(
    tmp_path,
):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "07" / "13"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "abababab-1111-2222-3333-444444444444"
    rollout = rollout_dir / f"rollout-2026-07-13T10-00-00-{session_id}.jsonl"
    rollout.write_text(
        json.dumps({
            "type": "message",
            "workflow_run_id": "raw-workflow",
            "task_id": "raw-task",
            "run_id": "raw-run",
            "target_session_id": "raw-session",
        }) + "\n",
        encoding="utf-8",
    )
    state = State(str(tmp_path / "state.db"))
    authoritative_run_id = _register_rollout(
        state=state,
        registry_dir=registry_dir,
        session_id=session_id,
        cwd=tmp_path,
    )
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)

    row = state._conn.execute(
        """SELECT run_id, payload_json
             FROM events
            WHERE source='rollout'"""
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert row["run_id"] == authoritative_run_id
    assert payload["workflow_run_id"] == authoritative_run_id
    assert payload["task_id"] == f"task-{session_id}"
    assert payload["run_id"] == authoritative_run_id
    assert payload["target_session_id"] == session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("preexisting_state_binding", (False, True))
async def test_strictly_rejected_registration_sidecar_stays_quarantined(
    tmp_path,
    preexisting_state_binding,
):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "07" / "13"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = (
        "cdcdcdcd-1111-2222-3333-444444444444"
        if preexisting_state_binding
        else "cececece-1111-2222-3333-444444444444"
    )
    rollout = rollout_dir / f"rollout-2026-07-13T10-00-00-{session_id}.jsonl"
    rollout.write_text(
        json.dumps({"type": "message", "text": "do not ingest"}) + "\n",
        encoding="utf-8",
    )
    rejected_run_id = f"workflow-rejected-{session_id}"
    (registry_dir / f"{session_id}.json").write_text(
        json.dumps({
            "workflow_run_id": rejected_run_id,
            "run_id": rejected_run_id,
            "target_session_id": "different-session",
            "session_id": "different-session",
            "task_id": "rejected-task",
            "task": "Rejected registration",
            "target_kind": "codex",
            "config_snapshot": {"cwd": str(tmp_path.resolve())},
        }) + "\n",
        encoding="utf-8",
    )
    state = State(str(tmp_path / "state.db"))
    if preexisting_state_binding:
        state.register_run(
            run_id="existing-state-run",
            session_id=session_id,
            rollout_path=str(rollout),
            task="Existing state binding",
            scope=ScopeContract(),
            target_kind="codex",
        )
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)
    await watcher._drain_file(rollout)

    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE source='rollout'"
    ).fetchone()[0] == 0
    assert state.get_tail_offset(str(rollout)) == 0
    assert not state.get_run(rejected_run_id)
    quarantines = list(
        (registry_dir / ".rollout-quarantine").glob("*.json")
    )
    assert len(quarantines) == 1
    assert quarantines[0].is_file()


@pytest.mark.asyncio
async def test_runtime_owned_rollout_requires_initial_cwd_provenance(tmp_path):
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "07" / "13"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    workflow_run_id = "workflow-runtime-cwd"
    session_id = "dededede-1111-2222-3333-444444444444"
    state = State(str(tmp_path / "state.db"))
    register_submitted_workflow(
        state=state,
        registry_dir=registry_dir,
        workflow_run_id=workflow_run_id,
        target_session_id="",
        task_id="task-runtime-cwd",
        task="Require runtime cwd provenance.",
        target_kind="codex",
        cwd=workspace,
        session_id_source=PENDING_SESSION_SOURCE,
    )
    runtime_registration = register_workflow_runtime_session(
        state=state,
        registry_dir=registry_dir,
        workflow_run_id=workflow_run_id,
        target_session_id=session_id,
        task_id="task-runtime-cwd",
        task="Require runtime cwd provenance.",
        target_kind="codex",
        cwd=workspace,
        gate="execution",
        runtime_run_id="runtime-cwd-run",
        runtime_result_hash="1" * 64,
    )
    rollout = rollout_dir / f"rollout-2026-07-13T10-00-00-{session_id}.jsonl"
    rollout.write_text(
        json.dumps({
            "type": "event_msg",
            "payload": {"type": "task_complete"},
        }) + "\n",
        encoding="utf-8",
    )
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)

    target_run_id = str(runtime_registration["target_run_id"])
    assert state.get_run(target_run_id)["status"] == "running"
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE source='rollout'"
    ).fetchone()[0] == 0
    assert state.get_tail_offset(str(rollout)) == 0
    assert len(
        list((registry_dir / ".rollout-quarantine").glob("*.json"))
    ) == 1

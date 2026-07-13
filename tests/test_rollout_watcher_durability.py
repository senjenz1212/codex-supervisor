from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from supervisor.rollout_watcher import RolloutWatcher
from supervisor.run_registry import register_submitted_workflow
from supervisor.state import State
from supervisor.target.types import ScopeContract


def _registered_watcher(
    tmp_path: Path,
    *,
    session_id: str,
) -> tuple[State, RolloutWatcher, Path]:
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "07" / "13"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()
    rollout = (
        rollout_dir
        / f"rollout-2026-07-13T10-00-00-{session_id}.jsonl"
    )
    state = State(str(tmp_path / "state.db"))
    register_submitted_workflow(
        state=state,
        registry_dir=registry_dir,
        workflow_run_id=f"workflow-{session_id}",
        target_session_id=session_id,
        task_id=f"task-{session_id}",
        task="Exercise durable rollout ingestion.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source="test",
        scope_contract=ScopeContract(),
    )
    return (
        state,
        RolloutWatcher(
            sessions_root=str(sessions_root),
            registry_dir=str(registry_dir),
            state=state,
        ),
        rollout,
    )


@pytest.mark.asyncio
async def test_turn_terminal_does_not_close_multi_turn_run(
    tmp_path: Path,
) -> None:
    session_id = "11111111-2222-3333-4444-555555555555"
    state, watcher, rollout = _registered_watcher(
        tmp_path,
        session_id=session_id,
    )
    rollout.write_text(
        json.dumps({"type": "task_complete"}) + "\n",
        encoding="utf-8",
    )

    await watcher._drain_file(rollout)

    run = state.get_run_by_session(session_id)
    assert run is not None
    assert run["status"] == "running"
    assert state.list_decision_outbox() == []
    assert [
        row["kind"]
        for row in state._conn.execute(
            "SELECT kind FROM events WHERE run_id=? ORDER BY event_id",
            (run["run_id"],),
        )
    ] == ["turn.completed"]


@pytest.mark.asyncio
async def test_run_terminal_atomically_closes_and_enqueues_once_across_retry(
    tmp_path: Path,
) -> None:
    session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    state, watcher, rollout = _registered_watcher(
        tmp_path,
        session_id=session_id,
    )
    raw_line = (json.dumps({"type": "run_completed"}) + "\n").encode()
    rollout.write_bytes(raw_line)

    await watcher._drain_file(rollout)
    await watcher._drain_file(rollout)

    run = state.get_run_by_session(session_id)
    assert run is not None
    assert run["status"] == "completed"
    assert state.get_tail_offset(str(rollout)) == len(raw_line)
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE run_id=? AND kind='run.completed'",
        (run["run_id"],),
    ).fetchone()[0] == 1
    outbox = state.list_decision_outbox()
    assert len(outbox) == 1
    assert outbox[0]["status"] == "pending"
    assert outbox[0]["payload"] == {
        "final_event_kind": "run.completed",
        "final_status": "completed",
    }

    restarted = State(state.db_path)
    assert len(restarted.list_decision_outbox()) == 1
    assert restarted.get_run(run["run_id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_multi_event_source_line_rolls_back_as_one_unit(
    tmp_path: Path,
) -> None:
    session_id = "12345678-aaaa-bbbb-cccc-123456789012"
    state, watcher, rollout = _registered_watcher(
        tmp_path,
        session_id=session_id,
    )
    raw_line = (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "done"}],
                },
            }
        )
        + "\n"
    ).encode()
    rollout.write_bytes(raw_line)
    original_insert = state._insert_event_unlocked
    calls = 0

    def fail_on_second_event(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second-event crash")
        return original_insert(**kwargs)

    state._insert_event_unlocked = fail_on_second_event  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="second-event crash"):
        await watcher._drain_file(rollout)

    assert state._conn.execute(
        "SELECT COUNT(*) FROM events"
    ).fetchone()[0] == 0
    assert state._conn.execute(
        "SELECT COUNT(*) FROM source_line_ingestions"
    ).fetchone()[0] == 0
    assert state.get_tail_offset(str(rollout)) == 0

    state._insert_event_unlocked = original_insert  # type: ignore[method-assign]
    await watcher._drain_file(rollout)

    assert [
        row["kind"]
        for row in state._conn.execute(
            "SELECT kind FROM events ORDER BY event_id"
        )
    ] == ["agent.message", "turn.completed"]
    assert state.get_tail_offset(str(rollout)) == len(raw_line)
    assert state.get_run_by_session(session_id)["status"] == "running"


@pytest.mark.asyncio
async def test_post_commit_crash_recovery_does_not_duplicate_terminal_work(
    tmp_path: Path,
) -> None:
    session_id = "99999999-8888-7777-6666-555555555555"
    state, watcher, rollout = _registered_watcher(
        tmp_path,
        session_id=session_id,
    )
    raw_line = (json.dumps({"type": "run_completed"}) + "\n").encode()
    rollout.write_bytes(raw_line)
    original_coordinate = state._coordinate_committed_event
    injected = False

    def fail_after_commit(**kwargs):
        nonlocal injected
        if not injected:
            injected = True
            raise RuntimeError("injected crash after commit")
        return original_coordinate(**kwargs)

    state._coordinate_committed_event = fail_after_commit  # type: ignore[method-assign]
    await watcher._drain_file_guarded(rollout)

    assert state.get_tail_offset(str(rollout)) == len(raw_line)
    assert len(state.list_decision_outbox()) == 1
    state._coordinate_committed_event = original_coordinate  # type: ignore[method-assign]
    restarted = RolloutWatcher(
        sessions_root=str(watcher.sessions_root),
        registry_dir=str(watcher.registry_dir),
        state=State(state.db_path),
    )
    await restarted._drain_file(rollout)

    recovered = restarted.state
    assert recovered._conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind='run.completed'"
    ).fetchone()[0] == 1
    assert len(recovered.list_decision_outbox()) == 1


@pytest.mark.asyncio
async def test_malformed_source_line_is_durably_dead_lettered(
    tmp_path: Path,
) -> None:
    session_id = "deadbeef-1111-2222-3333-444444444444"
    state, watcher, rollout = _registered_watcher(
        tmp_path,
        session_id=session_id,
    )
    raw_line = b"{not-json}\n"
    rollout.write_bytes(raw_line)

    await watcher._drain_file(rollout)

    row = state._conn.execute(
        "SELECT * FROM source_line_ingestions"
    ).fetchone()
    assert row is not None
    assert row["status"] == "dead_letter"
    assert row["raw_sha256"] == hashlib.sha256(raw_line).hexdigest()
    assert base64.b64decode(row["raw_line_b64"]) == raw_line
    assert json.loads(row["error_json"])["reason"] == (
        "json_decode_exception"
    )
    assert state.get_tail_offset(str(rollout)) == len(raw_line)

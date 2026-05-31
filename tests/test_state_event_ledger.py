from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from supervisor.state import State


def test_state_write_event_serializes_concurrent_parent_writes(tmp_path):
    state = State(str(tmp_path / "state.db"))

    def append(index: int) -> int:
        return state.write_event(
            run_id="parallel-run",
            source="dual_agent",
            kind="dual_agent_reviewer_result",
            payload={"task_id": "parallel-task", "index": index},
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        event_ids = list(pool.map(append, range(200)))

    assert len(event_ids) == 200
    assert len(set(event_ids)) == 200
    rows = state._conn.execute(
        "SELECT event_id, payload_json FROM events WHERE run_id='parallel-run'"
    ).fetchall()
    assert len(rows) == 200
    indices = {
        json.loads(row["payload_json"])["index"]
        for row in rows
    }
    assert indices == set(range(200))


def test_state_can_commit_event_and_tail_offset_together(tmp_path):
    state = State(str(tmp_path / "state.db"))
    rollout_path = str(tmp_path / "rollout.jsonl")

    event_id = state.write_event_and_tail_offset(
        run_id="run-session",
        source="rollout",
        kind="thread.completed",
        payload={"type": "thread.completed"},
        path=rollout_path,
        byte_offset=123,
    )

    row = state._conn.execute(
        "SELECT event_id, kind, payload_json FROM events WHERE run_id='run-session'"
    ).fetchone()
    assert row["event_id"] == event_id
    assert row["kind"] == "thread.completed"
    assert json.loads(row["payload_json"])["type"] == "thread.completed"
    assert state.get_tail_offset(rollout_path) == 123

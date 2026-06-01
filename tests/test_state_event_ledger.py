from __future__ import annotations

import json
import sqlite3
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


def test_read_events_since_returns_ascending_tail_after_cursor(tmp_path):
    state = State(str(tmp_path / "state.db"))
    other_id = state.write_event(
        run_id="other-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "other"},
    )
    first_id = state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "first"},
    )
    second_id = state.write_event(
        run_id="tail-run",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={"type": "second"},
    )
    third_id = state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "third"},
    )

    tail = state.read_events_since("tail-run", after_event_id=first_id, limit=2)

    assert [event["event_id"] for event in tail] == [second_id, third_id]
    assert other_id not in [event["event_id"] for event in tail]
    assert tail[0]["source"] == "dual_agent"
    assert tail[0]["kind"] == "dual_agent_gate_result"
    assert tail[0]["payload"]["type"] == "second"


def test_read_events_since_starts_from_beginning_and_empty_tail(tmp_path):
    state = State(str(tmp_path / "state.db"))
    first_id = state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "first"},
    )
    second_id = state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "second"},
    )

    assert [
        event["event_id"]
        for event in state.read_events_since("tail-run", after_event_id=0, limit=10)
    ] == [first_id, second_id]
    assert [
        event["event_id"]
        for event in state.read_events_since("tail-run", after_event_id=None, limit=10)
    ] == [first_id, second_id]
    assert state.read_events_since("tail-run", after_event_id=second_id, limit=10) == []
    assert state.read_events_since("tail-run", after_event_id=0, limit=0) == []


def test_read_events_since_tolerates_non_contiguous_event_ids(tmp_path):
    state = State(str(tmp_path / "state.db"))
    first_id = state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "first"},
    )
    gap_id = state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "gap"},
    )
    later_id = state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "later"},
    )
    state._conn.execute(
        "DELETE FROM events WHERE run_id=? AND event_id=?",
        ("tail-run", gap_id),
    )
    state._conn.commit()

    tail = state.read_events_since("tail-run", after_event_id=first_id, limit=10)

    assert gap_id not in [event["event_id"] for event in tail]
    assert [event["event_id"] for event in tail] == [later_id]
    assert tail[0]["payload"]["type"] == "later"


def test_events_run_event_index_exists_and_serves_tail_query(tmp_path):
    state = State(str(tmp_path / "state.db"))
    for index in range(30):
        state.write_event(
            run_id="tail-run" if index % 2 else "other-run",
            source="rollout",
            kind="event_msg",
            payload={"index": index},
        )

    index_names = {
        row["name"]
        for row in state._conn.execute("PRAGMA index_list(events)").fetchall()
    }
    assert "idx_events_run" in index_names
    assert "idx_events_run_event" in index_names

    plan_rows = state._conn.execute(
        """EXPLAIN QUERY PLAN
           SELECT event_id, ts, source, kind, payload_json
           FROM events
           WHERE run_id=? AND event_id > ?
           ORDER BY event_id ASC
           LIMIT ?""",
        ("tail-run", 0, 10),
    ).fetchall()
    plan = " ".join(str(row["detail"]) for row in plan_rows)

    assert "SCAN events" not in plan
    assert "idx_events_run_event" in plan


def test_state_constructor_adds_tail_index_to_existing_database(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE events (
             event_id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             ts INTEGER NOT NULL,
             source TEXT NOT NULL,
             kind TEXT NOT NULL,
             payload_json TEXT NOT NULL
           )"""
    )
    conn.execute("CREATE INDEX idx_events_run ON events(run_id, ts)")
    conn.execute(
        "INSERT INTO events(run_id, ts, source, kind, payload_json) VALUES(?, ?, ?, ?, ?)",
        ("tail-run", 1, "rollout", "event_msg", json.dumps({"type": "seed"})),
    )
    conn.commit()
    conn.close()

    state = State(str(db_path))

    index_names = {
        row["name"]
        for row in state._conn.execute("PRAGMA index_list(events)").fetchall()
    }
    assert "idx_events_run" in index_names
    assert "idx_events_run_event" in index_names
    assert state.read_events_since("tail-run", after_event_id=0, limit=10)[0]["payload"][
        "type"
    ] == "seed"


def test_event_tail_consumer_catches_up_after_disconnect_once(tmp_path):
    state = State(str(tmp_path / "state.db"))

    class TailConsumer:
        def __init__(self) -> None:
            self.cursor = 0
            self.delivered: list[int] = []

        def poll_until_empty(self, *, limit: int) -> list[int]:
            batch_ids: list[int] = []
            while True:
                page = state.read_events_since(
                    "tail-run",
                    after_event_id=self.cursor,
                    limit=limit,
                )
                if not page:
                    return batch_ids
                for event in page:
                    self.cursor = event["event_id"]
                    self.delivered.append(event["event_id"])
                    batch_ids.append(event["event_id"])

    first_id = state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "connected"},
    )
    consumer = TailConsumer()
    assert consumer.poll_until_empty(limit=2) == [first_id]

    offline_ids = [
        state.write_event(
            run_id="tail-run",
            source="rollout",
            kind="event_msg",
            payload={"type": "offline", "index": index},
        )
        for index in range(5)
    ]

    assert consumer.poll_until_empty(limit=2) == offline_ids
    assert consumer.poll_until_empty(limit=2) == []
    assert consumer.delivered == [first_id, *offline_ids]
    assert len(consumer.delivered) == len(set(consumer.delivered))


def test_existing_event_reads_keep_behavior(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.write_event(
        run_id="tail-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "first", "secret": "sk-super-secret"},
    )
    first_gate_id = state.write_event(
        run_id="tail-run",
        source="dual_agent",
        kind="dual_agent_gate_round",
        payload={"gate": "execution", "round_index": 1},
    )
    second_gate_id = state.write_event(
        run_id="tail-run",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={"gate": "execution", "status": "accepted"},
    )

    recent = state.recent_events("tail-run", n=2)
    assert [event["id"] for event in recent] == [first_gate_id, second_gate_id]
    assert "payload" not in recent[0]
    assert recent[0]["gate"] == "execution"

    gate_rows = state.read_dual_agent_gate_events("tail-run")
    assert [row["event_id"] for row in gate_rows] == [first_gate_id, second_gate_id]

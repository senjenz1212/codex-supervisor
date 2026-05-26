from __future__ import annotations

import sqlite3

import pytest

from supervisor.schema_migrations import applied_migrations, run_forward_migrations
from supervisor.state import State


def test_forward_migration_adds_resume_requested_at_to_old_actions_table(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE actions (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             action_type TEXT NOT NULL,
             requested_by TEXT NOT NULL,
             status TEXT NOT NULL,
             payload_json TEXT NOT NULL,
             created_at INTEGER NOT NULL,
             completed_at INTEGER
           )"""
    )

    run_forward_migrations(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(actions)").fetchall()}
    assert "resume_requested_at" in columns
    assert applied_migrations(conn) == [{"version": 1, "name": "actions.resume_requested_at"}]


def test_forward_migrations_are_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, resume_requested_at INTEGER)")

    run_forward_migrations(conn)
    run_forward_migrations(conn)

    assert applied_migrations(conn) == [{"version": 1, "name": "actions.resume_requested_at"}]


def test_forward_migration_name_mismatch_fails_closed(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, resume_requested_at INTEGER)")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES(1, 'different', 1)"
    )

    with pytest.raises(RuntimeError, match="schema migration mismatch"):
        run_forward_migrations(conn)


def test_forward_migration_unknown_future_version_fails_closed(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, resume_requested_at INTEGER)")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES(99, 'future.change', 1)"
    )

    with pytest.raises(RuntimeError, match="unknown future schema migration"):
        run_forward_migrations(conn)


def test_state_constructor_applies_forward_migration_to_old_db(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE actions (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             action_type TEXT NOT NULL,
             requested_by TEXT NOT NULL,
             status TEXT NOT NULL,
             payload_json TEXT NOT NULL,
             created_at INTEGER NOT NULL,
             completed_at INTEGER
           )"""
    )
    conn.commit()
    conn.close()

    state = State(str(db_path))

    columns = {
        row["name"]
        for row in state._conn.execute("PRAGMA table_info(actions)").fetchall()
    }
    assert "resume_requested_at" in columns
    assert applied_migrations(state._conn) == [{"version": 1, "name": "actions.resume_requested_at"}]

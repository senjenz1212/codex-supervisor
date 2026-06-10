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
    assert applied_migrations(conn) == [
        {"version": 1, "name": "actions.resume_requested_at"},
        {"version": 2, "name": "dual_agent_workflow_jobs.idempotency_token"},
        {"version": 3, "name": "dual_agent_workflow_jobs.terminal_outcome"},
        {"version": 4, "name": "dual_agent_workflow_jobs.recovery_points"},
        {"version": 5, "name": "dual_agent_workflow_jobs.recovery_claims"},
        {"version": 6, "name": "dual_agent_workflow_jobs.dispatcher_leases"},
        {"version": 7, "name": "supervisor_lessons"},
    ]


def test_forward_migrations_are_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, resume_requested_at INTEGER)")

    run_forward_migrations(conn)
    run_forward_migrations(conn)

    assert applied_migrations(conn) == [
        {"version": 1, "name": "actions.resume_requested_at"},
        {"version": 2, "name": "dual_agent_workflow_jobs.idempotency_token"},
        {"version": 3, "name": "dual_agent_workflow_jobs.terminal_outcome"},
        {"version": 4, "name": "dual_agent_workflow_jobs.recovery_points"},
        {"version": 5, "name": "dual_agent_workflow_jobs.recovery_claims"},
        {"version": 6, "name": "dual_agent_workflow_jobs.dispatcher_leases"},
        {"version": 7, "name": "supervisor_lessons"},
    ]


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
    assert applied_migrations(state._conn) == [
        {"version": 1, "name": "actions.resume_requested_at"},
        {"version": 2, "name": "dual_agent_workflow_jobs.idempotency_token"},
        {"version": 3, "name": "dual_agent_workflow_jobs.terminal_outcome"},
        {"version": 4, "name": "dual_agent_workflow_jobs.recovery_points"},
        {"version": 5, "name": "dual_agent_workflow_jobs.recovery_claims"},
        {"version": 6, "name": "dual_agent_workflow_jobs.dispatcher_leases"},
        {"version": 7, "name": "supervisor_lessons"},
    ]


def test_forward_migration_adds_workflow_job_idempotency(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE dual_agent_workflow_jobs (
             job_id TEXT PRIMARY KEY,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             cwd TEXT NOT NULL,
             status TEXT NOT NULL,
             pid INTEGER,
             request_path TEXT NOT NULL,
             result_path TEXT NOT NULL,
             log_path TEXT NOT NULL,
             returncode INTEGER,
             error TEXT,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    assert "idempotency_token" in columns
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(dual_agent_workflow_jobs)").fetchall()
    }
    assert "idx_dual_agent_workflow_jobs_active_idempotency_token" in indexes


def test_forward_migration_adds_workflow_job_dispatcher_leases(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE dual_agent_workflow_jobs (
             job_id TEXT PRIMARY KEY,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             cwd TEXT NOT NULL,
             status TEXT NOT NULL,
             pid INTEGER,
             request_path TEXT NOT NULL,
             result_path TEXT NOT NULL,
             log_path TEXT NOT NULL,
             idempotency_token TEXT,
             recovery_point TEXT NOT NULL DEFAULT 'reserved',
             recovery_claim_token TEXT,
             recovery_claimed_at INTEGER,
             request_payload_json TEXT,
             config_path TEXT,
             terminal_status TEXT,
             terminal_outcome_json TEXT,
             terminal_outcome_recorded_at INTEGER,
             returncode INTEGER,
             error TEXT,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    assert {
        "leased_by",
        "lease_expires_at",
        "heartbeat_at",
        "dispatch_attempts",
        "next_dispatch_at",
        "parked_reason",
    } <= columns
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(dual_agent_workflow_jobs)").fetchall()
    }
    assert "idx_dual_agent_workflow_jobs_dispatchable" in indexes
    assert applied_migrations(conn)[-1] == {
        "version": 7,
        "name": "supervisor_lessons",
    }
    conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, request_path, result_path,
             log_path, idempotency_token, created_at, updated_at)
           VALUES('job-1', 'run', 'task', '.', 'running', 'req', 'res', 'log', 'token', 1, 1)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO dual_agent_workflow_jobs(
                 job_id, run_id, task_id, cwd, status, request_path, result_path,
                 log_path, idempotency_token, created_at, updated_at)
               VALUES('job-2', 'run', 'task', '.', 'running', 'req', 'res', 'log', 'token', 1, 1)"""
        )
    conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, request_path, result_path,
             log_path, idempotency_token, recovery_point, created_at, updated_at)
           VALUES('job-terminal', 'run', 'task', '.', 'accepted', 'req', 'res', 'log', 'done-token', 'terminal', 1, 1)"""
    )
    conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, request_path, result_path,
             log_path, idempotency_token, recovery_point, created_at, updated_at)
           VALUES('job-active-after-terminal', 'run', 'task', '.', 'submitted', 'req', 'res', 'log', 'done-token', 'reserved', 1, 1)"""
    )


def test_forward_migration_adds_supervisor_lessons(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supervisor_lessons)").fetchall()
    }
    assert {
        "lesson_id",
        "task_class",
        "gate",
        "taxonomy_code",
        "root_cause",
        "remediation",
        "source_run_id",
        "created_at",
    } <= columns
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(supervisor_lessons)").fetchall()
    }
    assert "idx_supervisor_lessons_task_gate" in indexes
    assert applied_migrations(conn)[-1] == {
        "version": 7,
        "name": "supervisor_lessons",
    }


def test_state_constructor_adds_workflow_job_idempotency_to_existing_db(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE dual_agent_workflow_jobs (
             job_id TEXT PRIMARY KEY,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             cwd TEXT NOT NULL,
             status TEXT NOT NULL,
             pid INTEGER,
             request_path TEXT NOT NULL,
             result_path TEXT NOT NULL,
             log_path TEXT NOT NULL,
             returncode INTEGER,
             error TEXT,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )
    conn.commit()
    conn.close()

    state = State(str(db_path))

    columns = {
        row["name"]
        for row in state._conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    assert "idempotency_token" in columns
    indexes = {
        row["name"]
        for row in state._conn.execute("PRAGMA index_list(dual_agent_workflow_jobs)").fetchall()
    }
    assert "idx_dual_agent_workflow_jobs_active_idempotency_token" in indexes


def test_forward_migration_adds_workflow_job_terminal_outcome_fields(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE dual_agent_workflow_jobs (
             job_id TEXT PRIMARY KEY,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             cwd TEXT NOT NULL,
             status TEXT NOT NULL,
             pid INTEGER,
             request_path TEXT NOT NULL,
             result_path TEXT NOT NULL,
             log_path TEXT NOT NULL,
             idempotency_token TEXT,
             returncode INTEGER,
             error TEXT,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )

    run_forward_migrations(conn)
    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    assert {"terminal_status", "terminal_outcome_json", "terminal_outcome_recorded_at"} <= columns
    assert applied_migrations(conn) == [
        {"version": 1, "name": "actions.resume_requested_at"},
        {"version": 2, "name": "dual_agent_workflow_jobs.idempotency_token"},
        {"version": 3, "name": "dual_agent_workflow_jobs.terminal_outcome"},
        {"version": 4, "name": "dual_agent_workflow_jobs.recovery_points"},
        {"version": 5, "name": "dual_agent_workflow_jobs.recovery_claims"},
        {"version": 6, "name": "dual_agent_workflow_jobs.dispatcher_leases"},
        {"version": 7, "name": "supervisor_lessons"},
    ]


def test_forward_migration_adds_workflow_job_recovery_points(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE dual_agent_workflow_jobs (
             job_id TEXT PRIMARY KEY,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             cwd TEXT NOT NULL,
             status TEXT NOT NULL,
             pid INTEGER,
             request_path TEXT NOT NULL,
             result_path TEXT NOT NULL,
             log_path TEXT NOT NULL,
             idempotency_token TEXT,
             terminal_status TEXT,
             terminal_outcome_json TEXT,
             terminal_outcome_recorded_at INTEGER,
             returncode INTEGER,
             error TEXT,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, pid, request_path, result_path,
             log_path, idempotency_token, created_at, updated_at)
           VALUES('job-running', 'run', 'task', '.', 'running', 123, 'req', 'res', 'log', 'token-a', 1, 1)"""
    )
    conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, request_path, result_path,
             log_path, idempotency_token, terminal_outcome_json, created_at, updated_at)
           VALUES('job-terminal', 'run', 'task', '.', 'accepted', 'req', 'res', 'log', 'token-b', '{}', 1, 1)"""
    )

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    assert {
        "recovery_point",
        "recovery_claim_token",
        "recovery_claimed_at",
        "request_payload_json",
        "config_path",
    } <= columns
    rows = {
        row["job_id"]: row["recovery_point"]
        for row in conn.execute("SELECT job_id, recovery_point FROM dual_agent_workflow_jobs")
    }
    assert rows["job-running"] == "spawned"
    assert rows["job-terminal"] == "terminal"
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(dual_agent_workflow_jobs)").fetchall()
    }
    assert "idx_dual_agent_workflow_jobs_active_idempotency_token" in indexes

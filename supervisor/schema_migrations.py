"""Forward-only SQLite schema migrations for the supervisor state DB."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable


MigrationFn = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    name: str
    apply: MigrationFn


def run_forward_migrations(conn: sqlite3.Connection) -> None:
    """Apply known migrations once and fail closed on version/name drift."""
    _ensure_migration_table(conn)
    applied = {
        _row_int(row, "version", 0): _row_str(row, "name", 1)
        for row in conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version ASC"
        ).fetchall()
    }
    known_versions = {migration.version for migration in MIGRATIONS}
    unknown_versions = sorted(version for version in applied if version not in known_versions)
    if unknown_versions:
        raise RuntimeError(
            "unknown future schema migration: "
            + ", ".join(str(version) for version in unknown_versions)
        )
    now = _sqlite_now_s(conn)
    for migration in MIGRATIONS:
        existing_name = applied.get(migration.version)
        if existing_name is not None:
            if existing_name != migration.name:
                raise RuntimeError(
                    "schema migration mismatch: "
                    f"version={migration.version} expected={migration.name} observed={existing_name}"
                )
            continue
        migration.apply(conn)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES(?, ?, ?)",
            (migration.version, migration.name, now),
        )
    conn.commit()


def applied_migrations(conn: sqlite3.Connection) -> list[dict[str, int | str]]:
    _ensure_migration_table(conn)
    rows = conn.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version ASC"
    ).fetchall()
    return [
        {"version": _row_int(row, "version", 0), "name": _row_str(row, "name", 1)}
        for row in rows
    ]


def _ensure_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
             version INTEGER PRIMARY KEY,
             name TEXT NOT NULL,
             applied_at INTEGER NOT NULL
           )"""
    )


def _migration_actions_resume_requested_at(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "actions"):
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(actions)").fetchall()
    }
    if "resume_requested_at" not in columns:
        conn.execute("ALTER TABLE actions ADD COLUMN resume_requested_at INTEGER")


def _migration_workflow_job_idempotency_token(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "dual_agent_workflow_jobs"):
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    if "idempotency_token" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN idempotency_token TEXT")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_idempotency_token
           ON dual_agent_workflow_jobs(idempotency_token)
           WHERE idempotency_token IS NOT NULL"""
    )


def _migration_workflow_job_terminal_outcome(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "dual_agent_workflow_jobs"):
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    if "terminal_status" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN terminal_status TEXT")
    if "terminal_outcome_json" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN terminal_outcome_json TEXT")
    if "terminal_outcome_recorded_at" not in columns:
        conn.execute(
            "ALTER TABLE dual_agent_workflow_jobs ADD COLUMN terminal_outcome_recorded_at INTEGER"
        )


def _migration_workflow_job_recovery_points(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "dual_agent_workflow_jobs"):
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    if "recovery_point" not in columns:
        conn.execute(
            "ALTER TABLE dual_agent_workflow_jobs ADD COLUMN recovery_point TEXT NOT NULL DEFAULT 'reserved'"
        )
    if "request_payload_json" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN request_payload_json TEXT")
    if "config_path" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN config_path TEXT")
    conn.execute(
        """UPDATE dual_agent_workflow_jobs
              SET recovery_point = CASE
                    WHEN terminal_outcome_json IS NOT NULL
                      OR status IN ('accepted', 'blocked', 'cancelled', 'completed', 'denied', 'failed')
                      THEN 'terminal'
                    WHEN pid IS NOT NULL THEN 'spawned'
                    ELSE recovery_point
                  END"""
    )
    conn.execute("DROP INDEX IF EXISTS idx_dual_agent_workflow_jobs_idempotency_token")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_active_idempotency_token
           ON dual_agent_workflow_jobs(idempotency_token)
           WHERE idempotency_token IS NOT NULL AND recovery_point != 'terminal'"""
    )


def _migration_workflow_job_recovery_claims(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "dual_agent_workflow_jobs"):
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    if "recovery_claim_token" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN recovery_claim_token TEXT")
    if "recovery_claimed_at" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN recovery_claimed_at INTEGER")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _sqlite_now_s(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT CAST(strftime('%s', 'now') AS INTEGER)").fetchone()
    if row is None:
        return 0
    return int(row[0])


def _row_int(row: sqlite3.Row | tuple, key: str, index: int) -> int:
    try:
        return int(row[key])  # type: ignore[index]
    except (TypeError, IndexError):
        return int(row[index])


def _row_str(row: sqlite3.Row | tuple, key: str, index: int) -> str:
    try:
        return str(row[key])  # type: ignore[index]
    except (TypeError, IndexError):
        return str(row[index])


MIGRATIONS: tuple[SchemaMigration, ...] = (
    SchemaMigration(1, "actions.resume_requested_at", _migration_actions_resume_requested_at),
    SchemaMigration(
        2,
        "dual_agent_workflow_jobs.idempotency_token",
        _migration_workflow_job_idempotency_token,
    ),
    SchemaMigration(
        3,
        "dual_agent_workflow_jobs.terminal_outcome",
        _migration_workflow_job_terminal_outcome,
    ),
    SchemaMigration(
        4,
        "dual_agent_workflow_jobs.recovery_points",
        _migration_workflow_job_recovery_points,
    ),
    SchemaMigration(
        5,
        "dual_agent_workflow_jobs.recovery_claims",
        _migration_workflow_job_recovery_claims,
    ),
)

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


def _migration_workflow_job_dispatcher_leases(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "dual_agent_workflow_jobs"):
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dual_agent_workflow_jobs)").fetchall()
    }
    if "leased_by" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN leased_by TEXT")
    if "lease_expires_at" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN lease_expires_at INTEGER")
    if "heartbeat_at" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN heartbeat_at INTEGER")
    if "dispatch_attempts" not in columns:
        conn.execute(
            "ALTER TABLE dual_agent_workflow_jobs ADD COLUMN dispatch_attempts INTEGER NOT NULL DEFAULT 0"
        )
    if "next_dispatch_at" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN next_dispatch_at INTEGER")
    if "parked_reason" not in columns:
        conn.execute("ALTER TABLE dual_agent_workflow_jobs ADD COLUMN parked_reason TEXT")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_dispatchable
           ON dual_agent_workflow_jobs(status, recovery_point, next_dispatch_at, lease_expires_at)
           WHERE recovery_point IN ('reserved', 'request_written')"""
    )


def _migration_supervisor_lessons(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS supervisor_lessons (
             lesson_id     TEXT PRIMARY KEY,
             task_class    TEXT NOT NULL,
             gate          TEXT NOT NULL,
             taxonomy_code TEXT NOT NULL,
             root_cause    TEXT NOT NULL,
             remediation   TEXT NOT NULL,
             source_run_id TEXT NOT NULL,
             normalized_key TEXT NOT NULL DEFAULT '',
             observed_count INTEGER NOT NULL DEFAULT 1,
             injection_count INTEGER NOT NULL DEFAULT 0,
             recurrence_count INTEGER NOT NULL DEFAULT 0,
             retired_at INTEGER,
             created_at    INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_supervisor_lessons_task_gate
           ON supervisor_lessons(task_class, gate, created_at)"""
    )


def _migration_supervisor_quality_trends(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS supervisor_quality_trends (
             id                             INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id                         TEXT NOT NULL,
             task_id                        TEXT NOT NULL,
             task_class                     TEXT NOT NULL,
             gate                           TEXT NOT NULL,
             accepted                       INTEGER NOT NULL,
             first_pass_accepted            INTEGER NOT NULL,
             revision_rounds                INTEGER NOT NULL,
             time_to_accepted_outcome_s     REAL,
             p11_audit_sample_size          INTEGER NOT NULL DEFAULT 0,
             false_accept_count             INTEGER NOT NULL DEFAULT 0,
             false_accept_denominator       INTEGER NOT NULL DEFAULT 0,
             false_accept_rate              REAL NOT NULL DEFAULT 0.0,
             policy_overlay_hash            TEXT NOT NULL DEFAULT '',
             policy_proposal_id             TEXT NOT NULL DEFAULT '',
             details_json                   TEXT NOT NULL DEFAULT '{}',
             computed_at                    INTEGER NOT NULL,
             UNIQUE(run_id, gate)
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_supervisor_quality_trends_task_gate
           ON supervisor_quality_trends(task_class, gate, computed_at)"""
    )


def _migration_autoresearch_experiment_queue(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS supervisor_autoresearch_experiments (
             experiment_id        TEXT PRIMARY KEY,
             signal_key           TEXT NOT NULL UNIQUE,
             status               TEXT NOT NULL,
             task_class           TEXT NOT NULL,
             gate                 TEXT NOT NULL,
             taxonomy_code        TEXT NOT NULL,
             experiment_json      TEXT NOT NULL,
             attempt_json         TEXT NOT NULL,
             provenance_json      TEXT NOT NULL,
             report_only_reason   TEXT NOT NULL DEFAULT '',
             proposal_pointer_json TEXT NOT NULL DEFAULT '{}',
             report_ref           TEXT NOT NULL DEFAULT '',
             report_sha256        TEXT NOT NULL DEFAULT '',
             last_run_id          TEXT NOT NULL DEFAULT '',
             last_run_started_at  INTEGER,
             created_at           INTEGER NOT NULL,
             updated_at           INTEGER NOT NULL,
             activated_at         INTEGER,
             activated_by         TEXT,
             activation_channel   TEXT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_supervisor_autoresearch_experiments_status
           ON supervisor_autoresearch_experiments(status, updated_at)"""
    )


def _migration_policy_overlay_trend_columns(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "supervisor_quality_trends")
    if "policy_overlay_hash" not in columns:
        conn.execute(
            "ALTER TABLE supervisor_quality_trends ADD COLUMN policy_overlay_hash TEXT NOT NULL DEFAULT ''"
        )
    if "policy_proposal_id" not in columns:
        conn.execute(
            "ALTER TABLE supervisor_quality_trends ADD COLUMN policy_proposal_id TEXT NOT NULL DEFAULT ''"
        )
    lesson_columns = _columns(conn, "supervisor_lessons")
    lesson_additions = {
        "normalized_key": "TEXT NOT NULL DEFAULT ''",
        "observed_count": "INTEGER NOT NULL DEFAULT 1",
        "injection_count": "INTEGER NOT NULL DEFAULT 0",
        "recurrence_count": "INTEGER NOT NULL DEFAULT 0",
        "retired_at": "INTEGER",
    }
    for column, ddl in lesson_additions.items():
        if column not in lesson_columns:
            conn.execute(f"ALTER TABLE supervisor_lessons ADD COLUMN {column} {ddl}")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


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
    SchemaMigration(
        6,
        "dual_agent_workflow_jobs.dispatcher_leases",
        _migration_workflow_job_dispatcher_leases,
    ),
    SchemaMigration(
        7,
        "supervisor_lessons",
        _migration_supervisor_lessons,
    ),
    SchemaMigration(
        8,
        "supervisor_quality_trends",
        _migration_supervisor_quality_trends,
    ),
    SchemaMigration(
        9,
        "supervisor_autoresearch_experiments",
        _migration_autoresearch_experiment_queue,
    ),
    SchemaMigration(
        10,
        "supervisor_quality_trends.policy_overlay_columns",
        _migration_policy_overlay_trend_columns,
    ),
)

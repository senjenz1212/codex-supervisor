"""Forward-only SQLite schema migrations for the supervisor state DB."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .evidence_ledger import (
    EVENT_HASH_SCHEMA_VERSION,
    GENESIS_KINDS,
    LEGACY_IMPORT_GENESIS,
    build_legacy_import_ledger_fields,
    build_ledger_fields,
    event_hash_schema_transition_allowed,
    prepare_event_payload,
    strict_json_object_loads,
    supported_event_hash_schema_versions,
)
from .quality_projection import (
    QUALITY_TREND_PROJECTION_EVENT,
    canonical_quality_trend_projection_row,
    quality_trend_projection_event_payload,
)


MigrationFn = Callable[[sqlite3.Connection], None]
MAX_STARTUP_LEGACY_EVENT_BACKFILL = 10_000
EVENT_LEDGER_MIGRATION_VERSION = 12


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    name: str
    apply: MigrationFn


class LegacyEventLedgerBackfillRequired(RuntimeError):
    """Normal startup found historical events that need an offline import."""


def run_forward_migrations(conn: sqlite3.Connection) -> None:
    """Apply startup-safe migrations and repair required integrity objects."""
    _run_forward_migrations(conn, allow_legacy_event_backfill=False)


def migrate_legacy_event_ledger_offline(db_path: str | Path) -> None:
    """Import legacy events exactly once while holding an exclusive DB lock.

    Stop every supervisor process that can access ``db_path`` before invoking
    this helper. Historical payload JSON is preserved; only ledger metadata is
    added, and each imported chain is marked with ``legacy-import`` genesis.
    """
    path = str(Path(db_path).expanduser())
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN EXCLUSIVE")
        _run_forward_migrations(conn, allow_legacy_event_backfill=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _run_forward_migrations(
    conn: sqlite3.Connection,
    *,
    allow_legacy_event_backfill: bool,
) -> None:
    try:
        conn.execute("PRAGMA recursive_triggers = ON")
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        _ensure_migration_table(conn)
        applied = {
            _row_int(row, "version", 0): _row_str(row, "name", 1)
            for row in conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version ASC"
            ).fetchall()
        }
        known_versions = {migration.version for migration in MIGRATIONS}
        unknown_versions = sorted(
            version for version in applied if version not in known_versions
        )
        if unknown_versions:
            raise RuntimeError(
                "unknown future schema migration: "
                + ", ".join(str(version) for version in unknown_versions)
            )
        for migration in MIGRATIONS:
            existing_name = applied.get(migration.version)
            if (
                existing_name is not None
                and existing_name != migration.name
            ):
                raise RuntimeError(
                    "schema migration mismatch: "
                    f"version={migration.version} "
                    f"expected={migration.name} observed={existing_name}"
                )

        should_check_legacy_events = _should_check_legacy_event_backfill(
            conn,
            applied,
        )
        if (
            should_check_legacy_events
            and EVENT_LEDGER_MIGRATION_VERSION not in applied
            and _table_exists(conn, "events")
            and _EVENT_LEDGER_REQUIRED_COLUMNS
            & _columns(conn, "events")
        ):
            _ensure_event_ledger_columns(conn)
            _assert_prepopulated_event_ledger_metadata_matches(conn)
        affected_event_count = (
            _legacy_event_backfill_affected_event_count(conn)
            if should_check_legacy_events
            else 0
        )
        if affected_event_count:
            if (
                not allow_legacy_event_backfill
                and affected_event_count
                > MAX_STARTUP_LEGACY_EVENT_BACKFILL
            ):
                raise LegacyEventLedgerBackfillRequired(
                    _legacy_event_backfill_message(
                        conn,
                        affected_event_count=affected_event_count,
                    )
                )
            _ensure_event_ledger_columns(conn)
            _backfill_event_ledger_single_pass(conn)

        now = _sqlite_now_s(conn)
        for migration in MIGRATIONS:
            existing_name = applied.get(migration.version)
            if existing_name is not None:
                continue
            migration.apply(conn)
            conn.execute(
                """INSERT INTO schema_migrations(version, name, applied_at)
                   VALUES(?, ?, ?)""",
                (migration.version, migration.name, now),
            )
        _migration_workflow_job_process_identity(conn)
        _repair_required_integrity_objects(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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


def _migration_workflow_job_process_identity(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "dual_agent_workflow_jobs"):
        return
    columns = _columns(conn, "dual_agent_workflow_jobs")
    identity_columns = {
        "worker_pgid",
        "worker_started_at",
        "worker_containment_id",
        "worker_reaped_at",
    }
    if not identity_columns <= columns and "pid" in columns:
        predicates = ["pid IS NOT NULL"]
        if "terminal_outcome_json" in columns:
            predicates.append("terminal_outcome_json IS NULL")
        if "recovery_point" in columns:
            predicates.append("recovery_point != 'terminal'")
        if "status" in columns:
            predicates.append(
                "status NOT IN "
                "('accepted', 'blocked', 'cancelled', 'completed', "
                "'denied', 'failed')"
            )
        row = conn.execute(
            """SELECT job_id, pid
                 FROM dual_agent_workflow_jobs
                WHERE """
            + " AND ".join(predicates)
            + " ORDER BY job_id ASC LIMIT 1"
        ).fetchone()
        if row is not None:
            raise RuntimeError(
                "workflow process-identity migration requires quiescence: "
                f"job_id={_row_str(row, 'job_id', 0)} "
                f"pid={_row_int(row, 'pid', 1)} is potentially live; "
                "stop/reap pre-identity workers and clear pid or record a "
                "terminal outcome before retrying"
            )
    if "worker_pgid" not in columns:
        conn.execute(
            "ALTER TABLE dual_agent_workflow_jobs ADD COLUMN worker_pgid INTEGER"
        )
    if "worker_started_at" not in columns:
        conn.execute(
            "ALTER TABLE dual_agent_workflow_jobs ADD COLUMN worker_started_at REAL"
        )
    if "worker_containment_id" not in columns:
        conn.execute(
            "ALTER TABLE dual_agent_workflow_jobs "
            "ADD COLUMN worker_containment_id TEXT"
        )
    if "worker_reaped_at" not in columns:
        conn.execute(
            "ALTER TABLE dual_agent_workflow_jobs ADD COLUMN worker_reaped_at INTEGER"
        )


def _migration_historical_operation_claims(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS historical_operation_claims (
             operation_id  TEXT PRIMARY KEY,
             request_hash  TEXT NOT NULL,
             operation     TEXT NOT NULL
               CHECK(operation IN ('rerun', 'regrade', 'replay')),
             status        TEXT NOT NULL
               CHECK(status IN ('running', 'completed', 'failed')),
             terminal_event_id INTEGER,
             created_at    INTEGER NOT NULL,
             updated_at    INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_historical_operation_claims_status
           ON historical_operation_claims(status, updated_at)"""
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


def _migration_quality_trend_audits(conn: sqlite3.Connection) -> None:
    _ensure_quality_trend_audit_table(conn)
    _backfill_quality_trend_audits(conn)


def _ensure_quality_trend_audit_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS quality_trend_audits (
             run_id                         TEXT NOT NULL,
             gate                           TEXT NOT NULL,
             sample_size                    INTEGER NOT NULL,
             false_accept_count             INTEGER NOT NULL,
             false_accept_denominator       INTEGER NOT NULL,
             false_accept_rate              REAL NOT NULL,
             audit_details_json             TEXT NOT NULL DEFAULT '{}',
             computed_at                    INTEGER NOT NULL,
             PRIMARY KEY(run_id, gate, computed_at),
             CHECK (
                  sample_size >= 0
              AND false_accept_count >= 0
              AND false_accept_denominator >= 0
              AND false_accept_count <= false_accept_denominator
              AND false_accept_denominator <= sample_size
              AND false_accept_rate >= 0.0
              AND false_accept_rate <= 1.0
              AND false_accept_rate = CASE
                    WHEN false_accept_denominator = 0 THEN 0.0
                    ELSE CAST(false_accept_count AS REAL)
                         / CAST(false_accept_denominator AS REAL)
                  END
             )
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_quality_trend_audits_run_gate
           ON quality_trend_audits(run_id, gate, computed_at)"""
    )


def _backfill_quality_trend_audits(conn: sqlite3.Connection) -> None:
    trend_columns = _columns(conn, "supervisor_quality_trends")
    required_columns = {
        "run_id",
        "gate",
        "p11_audit_sample_size",
        "false_accept_count",
        "false_accept_denominator",
        "false_accept_rate",
        "details_json",
        "computed_at",
    }
    if not required_columns <= trend_columns:
        return
    rows = conn.execute(
        """SELECT run_id, gate, p11_audit_sample_size,
                  false_accept_count, false_accept_denominator,
                  false_accept_rate, details_json, computed_at
             FROM supervisor_quality_trends"""
    ).fetchall()
    for row in rows:
        try:
            details = json.loads(_row_str(row, "details_json", 6) or "{}")
        except json.JSONDecodeError:
            details = {}
        has_audit_details = (
            isinstance(details, dict)
            and "p11_audit" in details
            and isinstance(details.get("p11_audit"), dict)
        )
        audit_details = details.get("p11_audit") if has_audit_details else {}
        sample_size = _row_int(row, "p11_audit_sample_size", 2)
        false_accept_count = _row_int(row, "false_accept_count", 3)
        false_accept_denominator = _row_int(row, "false_accept_denominator", 4)
        if (
            sample_size == 0
            and false_accept_count == 0
            and false_accept_denominator == 0
            and not has_audit_details
        ):
            continue
        if (
            sample_size < 0
            or false_accept_count < 0
            or false_accept_denominator < 0
            or false_accept_count > false_accept_denominator
            or false_accept_denominator > sample_size
        ):
            raise RuntimeError(
                "invalid legacy quality audit counts: "
                f"run_id={_row_str(row, 'run_id', 0)} "
                f"gate={_row_str(row, 'gate', 1)}"
            )
        false_accept_rate = (
            false_accept_count / false_accept_denominator
            if false_accept_denominator
            else 0.0
        )
        run_id = _row_str(row, "run_id", 0)
        gate = _row_str(row, "gate", 1)
        computed_at = _row_int(row, "computed_at", 7)
        existing = conn.execute(
            """SELECT sample_size, false_accept_count,
                      false_accept_denominator, false_accept_rate,
                      audit_details_json
                 FROM quality_trend_audits
                WHERE run_id=? AND gate=? AND computed_at=?""",
            (run_id, gate, computed_at),
        ).fetchone()
        if existing is not None:
            try:
                existing_details = json.loads(
                    _row_str(existing, "audit_details_json", 4) or "{}"
                )
            except json.JSONDecodeError:
                existing_details = None
            if (
                _row_int(existing, "sample_size", 0) != sample_size
                or _row_int(existing, "false_accept_count", 1)
                != false_accept_count
                or _row_int(existing, "false_accept_denominator", 2)
                != false_accept_denominator
                or _row_float(existing, "false_accept_rate", 3)
                != false_accept_rate
                or existing_details != audit_details
            ):
                raise RuntimeError(
                    "conflicting immutable quality audit history: "
                    f"run_id={run_id} gate={gate} "
                    f"computed_at={computed_at}"
                )
            continue
        conn.execute(
            """INSERT INTO quality_trend_audits(
                 run_id, gate, sample_size, false_accept_count,
                 false_accept_denominator, false_accept_rate,
                 audit_details_json, computed_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                gate,
                sample_size,
                false_accept_count,
                false_accept_denominator,
                false_accept_rate,
                json.dumps(audit_details, sort_keys=True),
                computed_at,
            ),
        )


def _migration_evidence_ledger(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "events"):
        return
    _ensure_event_ledger_columns(conn)
    affected_event_count = _legacy_event_backfill_affected_event_count(conn)
    if affected_event_count:
        raise LegacyEventLedgerBackfillRequired(
            _legacy_event_backfill_message(
                conn,
                affected_event_count=affected_event_count,
            )
        )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_hash
           ON events(event_hash)"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_events_run_sequence
           ON events(run_id, event_sequence)"""
    )


def _migration_storage_integrity_v2(conn: sqlite3.Connection) -> None:
    _backfill_canonical_quality_projection_evidence(conn)
    _repair_required_integrity_objects(conn)


_EVENT_LEDGER_COLUMN_DDL = {
    "event_sequence": "INTEGER",
    "previous_event_hash": "TEXT",
    "event_hash": "TEXT",
    "canonical_payload_hash": "TEXT",
    "artifact_manifest_hash": "TEXT",
    "ledger_genesis_kind": "TEXT",
}
_EVENT_LEDGER_REQUIRED_COLUMNS = frozenset(_EVENT_LEDGER_COLUMN_DDL)


def _ensure_event_ledger_columns(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "events"):
        return
    columns = _columns(conn, "events")
    for column, ddl in _EVENT_LEDGER_COLUMN_DDL.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE events ADD COLUMN {column} {ddl}")


def _legacy_event_backfill_predicate() -> str:
    return """
           event_sequence IS NULL
        OR event_sequence <= 0
        OR event_hash IS NULL
        OR canonical_payload_hash IS NULL
        OR artifact_manifest_hash IS NULL
        OR (
             event_sequence = 1
         AND (
                  previous_event_hash IS NOT NULL
               OR ledger_genesis_kind IS NULL
               OR ledger_genesis_kind NOT IN ('native', 'legacy-import')
             )
           )
        OR (
             event_sequence > 1
         AND (
                  previous_event_hash IS NULL
               OR ledger_genesis_kind IS NOT NULL
             )
           )
    """


def _should_check_legacy_event_backfill(
    conn: sqlite3.Connection,
    applied: dict[int, str],
) -> bool:
    if not _table_exists(conn, "events"):
        return False
    if EVENT_LEDGER_MIGRATION_VERSION not in applied:
        return True
    if not _EVENT_LEDGER_REQUIRED_COLUMNS <= _columns(conn, "events"):
        return True
    if not _index_exists(conn, "idx_events_event_hash"):
        return True
    return conn.execute(
        "SELECT 1 FROM events WHERE event_hash IS NULL LIMIT 1"
    ).fetchone() is not None


def _legacy_event_backfill_affected_event_count(
    conn: sqlite3.Connection,
) -> int:
    if not _table_exists(conn, "events"):
        return 0
    total_row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
    total = int(total_row[0]) if total_row is not None else 0
    if total == 0:
        return 0
    if not _EVENT_LEDGER_REQUIRED_COLUMNS <= _columns(conn, "events"):
        return total
    row = conn.execute(
        f"""SELECT COUNT(*)
              FROM events
             WHERE run_id IN (
                   SELECT DISTINCT run_id
                     FROM events
                    WHERE {_legacy_event_backfill_predicate()}
             )"""
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _load_legacy_event_payload(
    raw_payload_text: str,
    *,
    run_id: str,
    event_id: int,
) -> dict[str, object]:
    try:
        return strict_json_object_loads(raw_payload_text)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "event payload is invalid or ambiguous JSON during ledger "
            f"migration: run_id={run_id} event_id={event_id}: {exc}"
        ) from exc


def _ledger_fields_for_event_row(
    *,
    run_id: str,
    event_sequence: int,
    ts: int,
    source: str,
    kind: str,
    payload: dict[str, object],
    raw_payload_text: str,
    previous_event_hash: str | None,
    genesis_kind: str | None,
    use_legacy_raw_commitment: bool,
    observed_event_hash: str | None,
):
    def build_for_schema(schema_version: str):
        if use_legacy_raw_commitment:
            return build_legacy_import_ledger_fields(
                run_id=run_id,
                event_sequence=event_sequence,
                ts=ts,
                source=source,
                kind=kind,
                payload=payload,
                raw_payload_json=raw_payload_text,
                previous_event_hash=previous_event_hash,
                ledger_genesis_kind=genesis_kind,
                event_hash_schema_version=schema_version,
            )
        return build_ledger_fields(
            run_id=run_id,
            event_sequence=event_sequence,
            ts=ts,
            source=source,
            kind=kind,
            payload=payload,
            previous_event_hash=previous_event_hash,
            ledger_genesis_kind=genesis_kind,
            event_hash_schema_version=schema_version,
        )

    if observed_event_hash is not None:
        matches = [
            (schema_version, fields)
            for schema_version in supported_event_hash_schema_versions()
            for fields in (build_for_schema(schema_version),)
            if fields.event_hash == observed_event_hash
        ]
        if len(matches) == 1:
            return matches[0]
    return (
        EVENT_HASH_SCHEMA_VERSION,
        build_for_schema(EVENT_HASH_SCHEMA_VERSION),
    )


def _assert_prepopulated_event_ledger_metadata_matches(
    conn: sqlite3.Connection,
) -> None:
    rows = conn.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events
            ORDER BY run_id ASC, event_id ASC"""
    ).fetchall()
    current_run_id: str | None = None
    event_sequence = 0
    previous_event_hash: str | None = None
    previous_event_hash_schema_version: str | None = None
    legacy_import_run = False
    for row in rows:
        run_id = _row_str(row, "run_id", 1)
        if run_id != current_run_id:
            current_run_id = run_id
            event_sequence = 0
            previous_event_hash = None
            previous_event_hash_schema_version = None
            legacy_import_run = False
        event_sequence += 1
        raw_payload_text = _row_str(row, "payload_json", 6)
        payload = _load_legacy_event_payload(
            raw_payload_text,
            run_id=run_id,
            event_id=_row_int(row, "event_id", 0),
        )
        observed_genesis = _row_optional_str(
            row,
            "ledger_genesis_kind",
            11,
        )
        genesis_kind = (
            observed_genesis
            if event_sequence == 1 and observed_genesis in GENESIS_KINDS
            else LEGACY_IMPORT_GENESIS if event_sequence == 1 else None
        )
        if event_sequence == 1:
            legacy_import_run = genesis_kind == LEGACY_IMPORT_GENESIS
        event_hash_schema_version, fields = _ledger_fields_for_event_row(
            run_id=run_id,
            event_sequence=event_sequence,
            ts=_row_int(row, "ts", 3),
            source=_row_str(row, "source", 4),
            kind=_row_str(row, "kind", 5),
            payload=payload,
            raw_payload_text=raw_payload_text,
            previous_event_hash=previous_event_hash,
            genesis_kind=genesis_kind,
            use_legacy_raw_commitment=legacy_import_run,
            observed_event_hash=_row_optional_str(row, "event_hash", 8),
        )
        if (
            previous_event_hash_schema_version is not None
            and not event_hash_schema_transition_allowed(
                previous_event_hash_schema_version,
                event_hash_schema_version,
            )
        ):
            raise RuntimeError(
                "pre-populated event ledger has a disallowed event-hash "
                "schema transition: "
                f"run_id={run_id} "
                f"event_id={_row_int(row, 'event_id', 0)} "
                f"{previous_event_hash_schema_version} -> "
                f"{event_hash_schema_version}"
            )
        normalized_payload = prepare_event_payload(
            run_id=run_id,
            source=_row_str(row, "source", 4),
            kind=_row_str(row, "kind", 5),
            payload=payload,
            event_hash_schema_version=event_hash_schema_version,
        )
        if normalized_payload != payload:
            raise RuntimeError(
                "legacy event payload requires redaction or trace "
                "normalization; refusing to rewrite historical evidence: "
                f"run_id={run_id} event_id={_row_int(row, 'event_id', 0)}"
            )
        expected = {
            "event_sequence": fields.event_sequence,
            "previous_event_hash": fields.previous_event_hash,
            "event_hash": fields.event_hash,
            "canonical_payload_hash": fields.canonical_payload_hash,
            "artifact_manifest_hash": fields.artifact_manifest_hash,
            "ledger_genesis_kind": fields.ledger_genesis_kind,
        }
        field_indexes = {
            "event_sequence": 2,
            "previous_event_hash": 7,
            "event_hash": 8,
            "canonical_payload_hash": 9,
            "artifact_manifest_hash": 10,
            "ledger_genesis_kind": 11,
        }
        for field, expected_value in expected.items():
            try:
                observed_value = row[field]  # type: ignore[index]
            except (TypeError, IndexError):
                observed_value = row[field_indexes[field]]
            if observed_value is None:
                continue
            if field == "event_sequence":
                observed_value = int(observed_value)
            else:
                observed_value = str(observed_value)
            if observed_value != expected_value:
                raise RuntimeError(
                    "conflicting pre-populated event ledger metadata: "
                    f"run_id={run_id} "
                    f"event_id={_row_int(row, 'event_id', 0)} "
                    f"field={field}"
                )
        previous_event_hash = fields.event_hash
        previous_event_hash_schema_version = event_hash_schema_version


def _backfill_event_ledger_single_pass(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "events"):
        return
    _ensure_event_ledger_columns(conn)
    if _legacy_event_backfill_affected_event_count(conn) == 0:
        return
    conn.execute("DROP TRIGGER IF EXISTS events_no_update")
    rows = conn.execute(
        f"""SELECT e.event_id, e.run_id, e.ts, e.source, e.kind,
                   e.payload_json, e.event_hash, e.ledger_genesis_kind
              FROM events AS e
              JOIN (
                    SELECT DISTINCT run_id
                      FROM events
                     WHERE {_legacy_event_backfill_predicate()}
                   ) AS affected
                ON affected.run_id = e.run_id
             ORDER BY e.run_id ASC, e.event_id ASC"""
    ).fetchall()
    current_run_id: str | None = None
    event_sequence = 0
    previous_event_hash: str | None = None
    previous_event_hash_schema_version: str | None = None
    first_genesis = LEGACY_IMPORT_GENESIS
    for row in rows:
        run_id = _row_str(row, "run_id", 1)
        if run_id != current_run_id:
            current_run_id = run_id
            event_sequence = 0
            previous_event_hash = None
            previous_event_hash_schema_version = None
            observed_genesis = _row_optional_str(
                row,
                "ledger_genesis_kind",
                7,
            )
            first_genesis = (
                observed_genesis
                if observed_genesis in GENESIS_KINDS
                else LEGACY_IMPORT_GENESIS
            )
        event_sequence += 1
        raw_payload_text = _row_str(row, "payload_json", 5)
        payload = _load_legacy_event_payload(
            raw_payload_text,
            run_id=run_id,
            event_id=_row_int(row, "event_id", 0),
        )
        genesis_kind = first_genesis if event_sequence == 1 else None
        event_hash_schema_version, fields = _ledger_fields_for_event_row(
            run_id=run_id,
            event_sequence=event_sequence,
            ts=_row_int(row, "ts", 2),
            source=_row_str(row, "source", 3),
            kind=_row_str(row, "kind", 4),
            payload=payload,
            raw_payload_text=raw_payload_text,
            previous_event_hash=previous_event_hash,
            genesis_kind=genesis_kind,
            use_legacy_raw_commitment=(
                first_genesis == LEGACY_IMPORT_GENESIS
            ),
            observed_event_hash=_row_optional_str(row, "event_hash", 6),
        )
        if (
            previous_event_hash_schema_version is not None
            and not event_hash_schema_transition_allowed(
                previous_event_hash_schema_version,
                event_hash_schema_version,
            )
        ):
            raise RuntimeError(
                "event ledger backfill would create a disallowed "
                "event-hash schema transition: "
                f"run_id={run_id} "
                f"event_id={_row_int(row, 'event_id', 0)} "
                f"{previous_event_hash_schema_version} -> "
                f"{event_hash_schema_version}"
            )
        normalized_payload = prepare_event_payload(
            run_id=run_id,
            source=_row_str(row, "source", 3),
            kind=_row_str(row, "kind", 4),
            payload=payload,
            event_hash_schema_version=event_hash_schema_version,
        )
        if normalized_payload != payload:
            raise RuntimeError(
                "legacy event payload requires redaction or trace "
                "normalization; refusing to rewrite historical evidence: "
                f"run_id={run_id} event_id={_row_int(row, 'event_id', 0)}"
            )
        conn.execute(
            """UPDATE events
                  SET event_sequence=?,
                      previous_event_hash=?,
                      event_hash=?,
                      canonical_payload_hash=?,
                      artifact_manifest_hash=?,
                      ledger_genesis_kind=?
                WHERE event_id=?""",
            (
                fields.event_sequence,
                fields.previous_event_hash,
                fields.event_hash,
                fields.canonical_payload_hash,
                fields.artifact_manifest_hash,
                fields.ledger_genesis_kind,
                _row_int(row, "event_id", 0),
            ),
        )
        previous_event_hash = fields.event_hash
        previous_event_hash_schema_version = event_hash_schema_version
    remaining = _legacy_event_backfill_affected_event_count(conn)
    if remaining:
        raise RuntimeError(
            "legacy event ledger backfill did not converge: "
            f"affected_event_count={remaining}"
        )


def _legacy_event_backfill_message(
    conn: sqlite3.Connection,
    *,
    affected_event_count: int,
) -> str:
    database_path = _sqlite_main_database_path(conn)
    target = repr(database_path or "/path/to/state.db")
    return (
        "legacy event ledger backfill requires offline maintenance: "
        f"{affected_event_count} historical events need ledger metadata; "
        "normal State startup will not rewrite historical evidence. "
        "Stop all supervisor processes, then run "
        "`uv run python -c \"from supervisor.schema_migrations import "
        "migrate_legacy_event_ledger_offline; "
        f"migrate_legacy_event_ledger_offline({target})\"`. "
        "The offline import preserves payload_json and marks historical "
        "chain genesis as legacy-import."
    )


def _sqlite_main_database_path(conn: sqlite3.Connection) -> str:
    for row in conn.execute("PRAGMA database_list").fetchall():
        name = _row_str(row, "name", 1)
        if name == "main":
            return _row_str(row, "file", 2)
    return ""


def _backfill_canonical_quality_projection_evidence(
    conn: sqlite3.Connection,
) -> None:
    if not _table_exists(conn, "events"):
        return
    required_trend_columns = {
        "run_id",
        "task_id",
        "task_class",
        "gate",
        "accepted",
        "first_pass_accepted",
        "revision_rounds",
        "time_to_accepted_outcome_s",
        "p11_audit_sample_size",
        "false_accept_count",
        "false_accept_denominator",
        "false_accept_rate",
        "policy_overlay_hash",
        "policy_proposal_id",
        "details_json",
        "computed_at",
    }
    if not required_trend_columns <= _columns(
        conn,
        "supervisor_quality_trends",
    ):
        return
    _ensure_event_ledger_columns(conn)
    rows = conn.execute(
        """SELECT run_id, task_id, task_class, gate, accepted,
                  first_pass_accepted, revision_rounds,
                  time_to_accepted_outcome_s, p11_audit_sample_size,
                  false_accept_count, false_accept_denominator,
                  false_accept_rate, policy_overlay_hash,
                  policy_proposal_id, details_json, computed_at
             FROM supervisor_quality_trends
            ORDER BY run_id ASC, gate ASC"""
    ).fetchall()
    for row in rows:
        try:
            details = json.loads(_row_str(row, "details_json", 14) or "{}")
        except json.JSONDecodeError:
            details = {}
        projection_row = canonical_quality_trend_projection_row(
            {
                "run_id": _row_str(row, "run_id", 0),
                "task_id": _row_str(row, "task_id", 1),
                "task_class": _row_str(row, "task_class", 2),
                "gate": _row_str(row, "gate", 3),
                "accepted": bool(_row_int(row, "accepted", 4)),
                "first_pass_accepted": bool(
                    _row_int(row, "first_pass_accepted", 5)
                ),
                "revision_rounds": _row_int(row, "revision_rounds", 6),
                "time_to_accepted_outcome_s": _row_optional_float(
                    row,
                    "time_to_accepted_outcome_s",
                    7,
                ),
                "p11_audit_sample_size": _row_int(
                    row,
                    "p11_audit_sample_size",
                    8,
                ),
                "false_accept_count": _row_int(
                    row,
                    "false_accept_count",
                    9,
                ),
                "false_accept_denominator": _row_int(
                    row,
                    "false_accept_denominator",
                    10,
                ),
                "false_accept_rate": _row_float(
                    row,
                    "false_accept_rate",
                    11,
                ),
                "policy_overlay_hash": _row_str(
                    row,
                    "policy_overlay_hash",
                    12,
                ),
                "policy_proposal_id": _row_str(
                    row,
                    "policy_proposal_id",
                    13,
                ),
                "details": details if isinstance(details, dict) else {},
                "computed_at": _row_int(row, "computed_at", 15),
            }
        )
        payload = prepare_event_payload(
            run_id=projection_row["run_id"],
            source="schema_migration",
            kind=QUALITY_TREND_PROJECTION_EVENT,
            payload=quality_trend_projection_event_payload(projection_row),
        )
        if _quality_projection_evidence_is_current(
            conn,
            run_id=projection_row["run_id"],
            gate=projection_row["gate"],
            payload=payload,
        ):
            continue
        _append_legacy_quality_projection_event(
            conn,
            run_id=projection_row["run_id"],
            payload=payload,
        )


def _quality_projection_evidence_is_current(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    gate: str,
    payload: dict[str, object],
) -> bool:
    expected = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    rows = conn.execute(
        """SELECT payload_json
             FROM events
            WHERE run_id=? AND kind=?
            ORDER BY event_sequence DESC""",
        (run_id, QUALITY_TREND_PROJECTION_EVENT),
    ).fetchall()
    for row in rows:
        try:
            observed = json.loads(_row_str(row, "payload_json", 0))
        except json.JSONDecodeError:
            continue
        projection_row = (
            observed.get("projection_row")
            if isinstance(observed, dict)
            else None
        )
        if (
            not isinstance(projection_row, dict)
            or str(projection_row.get("gate") or "") != gate
        ):
            continue
        canonical_observed = json.dumps(
            observed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return canonical_observed == expected
    return False


def _append_legacy_quality_projection_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    payload: dict[str, object],
) -> None:
    prepared_payload = prepare_event_payload(
        run_id=run_id,
        source="schema_migration",
        kind=QUALITY_TREND_PROJECTION_EVENT,
        payload=payload,
    )
    head = conn.execute(
        """SELECT event_sequence, event_hash
             FROM events
            WHERE run_id=?
            ORDER BY event_sequence DESC
            LIMIT 1""",
        (run_id,),
    ).fetchone()
    event_sequence = (
        _row_int(head, "event_sequence", 0) + 1
        if head is not None
        else 1
    )
    previous_event_hash = (
        _row_str(head, "event_hash", 1)
        if head is not None
        else None
    )
    event_ts = _sqlite_now_s(conn)
    fields = build_ledger_fields(
        run_id=run_id,
        event_sequence=event_sequence,
        ts=event_ts,
        source="schema_migration",
        kind=QUALITY_TREND_PROJECTION_EVENT,
        payload=prepared_payload,
        previous_event_hash=previous_event_hash,
        ledger_genesis_kind=(
            LEGACY_IMPORT_GENESIS if previous_event_hash is None else None
        ),
    )
    conn.execute(
        """INSERT INTO events(
             run_id, event_sequence, ts, source, kind, payload_json,
             previous_event_hash, event_hash, canonical_payload_hash,
             artifact_manifest_hash, ledger_genesis_kind)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            event_sequence,
            event_ts,
            "schema_migration",
            QUALITY_TREND_PROJECTION_EVENT,
            json.dumps(
                prepared_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
            fields.previous_event_hash,
            fields.event_hash,
            fields.canonical_payload_hash,
            fields.artifact_manifest_hash,
            fields.ledger_genesis_kind,
        ),
    )


def _validate_quality_trend_audits(conn: sqlite3.Connection) -> None:
    invalid_audit = conn.execute(
        """SELECT run_id, gate
             FROM quality_trend_audits
            WHERE sample_size < 0
               OR false_accept_count < 0
               OR false_accept_denominator < 0
               OR false_accept_count > false_accept_denominator
               OR false_accept_denominator > sample_size
               OR false_accept_rate < 0.0
               OR false_accept_rate > 1.0
               OR false_accept_rate != CASE
                    WHEN false_accept_denominator = 0 THEN 0.0
                    ELSE CAST(false_accept_count AS REAL)
                         / CAST(false_accept_denominator AS REAL)
                  END
            LIMIT 1"""
    ).fetchone()
    if invalid_audit is not None:
        raise RuntimeError(
            "invalid legacy quality audit counts or derived rate: "
            f"run_id={_row_str(invalid_audit, 'run_id', 0)} "
            f"gate={_row_str(invalid_audit, 'gate', 1)}"
        )


def _repair_required_integrity_objects(conn: sqlite3.Connection) -> None:
    _migration_event_idempotency_claims(conn)
    if _table_exists(conn, "events"):
        _ensure_event_ledger_columns(conn)
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_hash
               ON events(event_hash)"""
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_events_run_sequence
               ON events(run_id, event_sequence)"""
        )
        for trigger_name in (
            "events_no_replace",
            "events_require_ledger_fields",
            "events_no_update",
            "events_no_delete",
        ):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
        conn.execute(
            """CREATE TRIGGER events_no_replace
               BEFORE INSERT ON events
               WHEN EXISTS (
                    SELECT 1
                      FROM events
                     WHERE event_id = NEW.event_id
                        OR event_hash = NEW.event_hash
                        OR (
                             run_id = NEW.run_id
                         AND event_sequence = NEW.event_sequence
                           )
               )
               BEGIN
                 SELECT RAISE(ABORT, 'events are append-only');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER events_require_ledger_fields
               BEFORE INSERT ON events
               WHEN NEW.event_sequence IS NULL
                 OR NEW.event_sequence <= 0
                 OR NEW.event_hash IS NULL
                 OR NEW.canonical_payload_hash IS NULL
                 OR NEW.artifact_manifest_hash IS NULL
                 OR (
                      NEW.event_sequence = 1
                      AND (
                           NEW.previous_event_hash IS NOT NULL
                           OR NEW.ledger_genesis_kind IS NULL
                           OR NEW.ledger_genesis_kind
                              NOT IN ('native', 'legacy-import')
                      )
                    )
                 OR (
                      NEW.event_sequence > 1
                      AND (
                           NEW.previous_event_hash IS NULL
                           OR NEW.ledger_genesis_kind IS NOT NULL
                      )
                    )
               BEGIN
                 SELECT RAISE(ABORT, 'event ledger fields are required');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER events_no_update
               BEFORE UPDATE ON events
               BEGIN
                 SELECT RAISE(ABORT, 'events are append-only');
               END"""
        )
        conn.execute(
            """CREATE TRIGGER events_no_delete
               BEFORE DELETE ON events
               BEGIN
                 SELECT RAISE(ABORT, 'events are append-only');
               END"""
        )

    _ensure_quality_trend_audit_table(conn)
    _validate_quality_trend_audits(conn)
    for trigger_name in (
        "quality_trend_audits_no_replace",
        "quality_trend_audits_validate_insert",
        "quality_trend_audits_no_update",
        "quality_trend_audits_no_delete",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    conn.execute(
        """CREATE TRIGGER quality_trend_audits_no_replace
           BEFORE INSERT ON quality_trend_audits
           WHEN EXISTS (
                SELECT 1
                  FROM quality_trend_audits
                 WHERE run_id = NEW.run_id
                   AND gate = NEW.gate
                   AND computed_at = NEW.computed_at
           )
           BEGIN
             SELECT RAISE(ABORT, 'quality trend audits are immutable');
           END"""
    )
    conn.execute(
        """CREATE TRIGGER quality_trend_audits_validate_insert
           BEFORE INSERT ON quality_trend_audits
           WHEN NEW.sample_size < 0
             OR NEW.false_accept_count < 0
             OR NEW.false_accept_denominator < 0
             OR NEW.false_accept_count > NEW.false_accept_denominator
             OR NEW.false_accept_denominator > NEW.sample_size
             OR NEW.false_accept_rate < 0.0
             OR NEW.false_accept_rate > 1.0
             OR NEW.false_accept_rate != CASE
                  WHEN NEW.false_accept_denominator = 0 THEN 0.0
                  ELSE CAST(NEW.false_accept_count AS REAL)
                       / CAST(NEW.false_accept_denominator AS REAL)
                END
           BEGIN
             SELECT RAISE(ABORT, 'invalid quality audit counts or rate');
           END"""
    )
    conn.execute(
        """CREATE TRIGGER quality_trend_audits_no_update
           BEFORE UPDATE ON quality_trend_audits
           BEGIN
             SELECT RAISE(ABORT, 'quality trend audits are immutable');
           END"""
    )
    conn.execute(
        """CREATE TRIGGER quality_trend_audits_no_delete
           BEFORE DELETE ON quality_trend_audits
           BEGIN
             SELECT RAISE(ABORT, 'quality trend audits are immutable');
           END"""
    )

    if _table_exists(conn, "dual_agent_workflow_jobs"):
        required_columns = {
            "job_id",
            "run_id",
            "task_id",
            "result_path",
            "status",
            "recovery_point",
            "terminal_status",
            "terminal_outcome_json",
            "terminal_outcome_recorded_at",
            "returncode",
            "error",
            "pid",
            "worker_pgid",
            "worker_started_at",
            "worker_containment_id",
            "worker_reaped_at",
        }
        if required_columns <= _columns(conn, "dual_agent_workflow_jobs"):
            conn.execute(
                "DROP TRIGGER IF EXISTS "
                "dual_agent_workflow_jobs_terminal_freeze"
            )
            conn.execute(
                "DROP TRIGGER IF EXISTS "
                "dual_agent_workflow_jobs_terminal_no_delete"
            )
            conn.execute(
                "DROP TRIGGER IF EXISTS "
                "dual_agent_workflow_jobs_worker_reaped_once"
            )
            conn.execute(
                """CREATE TRIGGER dual_agent_workflow_jobs_terminal_freeze
                   BEFORE UPDATE ON dual_agent_workflow_jobs
                   WHEN OLD.terminal_outcome_json IS NOT NULL
                    AND (
                         NEW.job_id IS NOT OLD.job_id
                      OR NEW.run_id IS NOT OLD.run_id
                      OR NEW.task_id IS NOT OLD.task_id
                      OR NEW.result_path IS NOT OLD.result_path
                      OR NEW.status IS NOT OLD.status
                      OR NEW.recovery_point IS NOT OLD.recovery_point
                      OR NEW.terminal_status IS NOT OLD.terminal_status
                      OR NEW.terminal_outcome_json IS NOT OLD.terminal_outcome_json
                      OR NEW.terminal_outcome_recorded_at
                           IS NOT OLD.terminal_outcome_recorded_at
                      OR NEW.returncode IS NOT OLD.returncode
                      OR NEW.error IS NOT OLD.error
                      OR NEW.pid IS NOT OLD.pid
                      OR NEW.worker_pgid IS NOT OLD.worker_pgid
                      OR NEW.worker_started_at IS NOT OLD.worker_started_at
                      OR NEW.worker_containment_id
                           IS NOT OLD.worker_containment_id
                    )
                   BEGIN
                     SELECT RAISE(
                       ABORT,
                       'terminal workflow job fields are immutable'
                     );
                   END"""
            )
            conn.execute(
                """CREATE TRIGGER dual_agent_workflow_jobs_terminal_no_delete
                   BEFORE DELETE ON dual_agent_workflow_jobs
                   WHEN OLD.terminal_outcome_json IS NOT NULL
                     OR OLD.recovery_point = 'terminal'
                     OR OLD.status IN (
                          'accepted', 'blocked', 'cancelled', 'completed',
                          'denied', 'failed'
                        )
                   BEGIN
                     SELECT RAISE(
                       ABORT,
                       'terminal workflow job is immutable'
                     );
                   END"""
            )
            conn.execute(
                """CREATE TRIGGER dual_agent_workflow_jobs_worker_reaped_once
                   BEFORE UPDATE ON dual_agent_workflow_jobs
                   WHEN OLD.worker_reaped_at IS NOT NULL
                    AND NEW.worker_reaped_at IS NOT OLD.worker_reaped_at
                   BEGIN
                     SELECT RAISE(
                       ABORT,
                       'worker_reaped_at is immutable once recorded'
                     );
                   END"""
    )


def _migration_event_idempotency_claims(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS event_idempotency_claims (
             run_id          TEXT NOT NULL,
             kind            TEXT NOT NULL,
             idempotency_key TEXT NOT NULL,
             event_id        INTEGER NOT NULL,
             source          TEXT NOT NULL,
             payload_sha256  TEXT NOT NULL,
             created_at      INTEGER NOT NULL,
             PRIMARY KEY(run_id, kind, idempotency_key),
             UNIQUE(event_id),
             CHECK(event_id > 0)
           )"""
    )
    if not _table_exists(conn, "events"):
        return
    rows = conn.execute(
        """SELECT event_id, run_id, source, kind, payload_json,
                  canonical_payload_hash, ts
             FROM events
            WHERE kind='dual_agent_production_trace_recorded'
            ORDER BY event_id ASC"""
    ).fetchall()
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        try:
            payload = strict_json_object_loads(
                _row_str(row, "payload_json", 4)
            )
        except ValueError as exc:
            raise RuntimeError(
                "cannot backfill event idempotency from invalid payload JSON"
            ) from exc
        source_event_hash = str(
            payload.get("source_event_hash") or ""
        ).strip()
        if not source_event_hash:
            raise RuntimeError(
                "production trace event lacks source_event_hash for "
                "idempotency backfill"
            )
        identity = (
            _row_str(row, "run_id", 1),
            _row_str(row, "kind", 3),
            f"production-trace:{source_event_hash}",
        )
        if identity in seen:
            raise RuntimeError(
                "duplicate production trace events require manual repair "
                "before idempotency migration"
            )
        seen.add(identity)
        expected = (
            _row_int(row, "event_id", 0),
            _row_str(row, "source", 2),
            _row_str(row, "canonical_payload_hash", 5),
        )
        existing = conn.execute(
            """SELECT event_id, source, payload_sha256
                 FROM event_idempotency_claims
                WHERE run_id=? AND kind=? AND idempotency_key=?""",
            identity,
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO event_idempotency_claims(
                     run_id, kind, idempotency_key, event_id, source,
                     payload_sha256, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (
                    identity[0],
                    identity[1],
                    identity[2],
                    *expected,
                    _row_int(row, "ts", 6),
                ),
            )
        elif (
            _row_int(existing, "event_id", 0),
            _row_str(existing, "source", 1),
            _row_str(existing, "payload_sha256", 2),
        ) != expected:
            raise RuntimeError(
                "event idempotency claim conflicts with the immutable event"
            )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS event_idempotency_claims_no_update
           BEFORE UPDATE ON event_idempotency_claims
           BEGIN
             SELECT RAISE(
               ABORT,
               'event idempotency claims are immutable'
             );
           END"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS event_idempotency_claims_no_delete
           BEFORE DELETE ON event_idempotency_claims
           BEGIN
             SELECT RAISE(
               ABORT,
               'event idempotency claims are immutable'
             );
           END"""
    )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        _row_str(row, "name", 1)
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


def _row_optional_str(
    row: sqlite3.Row | tuple,
    key: str,
    index: int,
) -> str | None:
    try:
        value = row[key]  # type: ignore[index]
    except (TypeError, IndexError):
        value = row[index]
    return None if value is None else str(value)


def _row_float(row: sqlite3.Row | tuple, key: str, index: int) -> float:
    try:
        return float(row[key])  # type: ignore[index]
    except (TypeError, IndexError):
        return float(row[index])


def _row_optional_float(
    row: sqlite3.Row | tuple,
    key: str,
    index: int,
) -> float | None:
    try:
        value = row[key]  # type: ignore[index]
    except (TypeError, IndexError):
        value = row[index]
    return None if value is None else float(value)


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
    SchemaMigration(
        11,
        "quality_trend_audits",
        _migration_quality_trend_audits,
    ),
    SchemaMigration(
        12,
        "events.tamper_evident_ledger",
        _migration_evidence_ledger,
    ),
    SchemaMigration(
        13,
        "storage.integrity_v2",
        _migration_storage_integrity_v2,
    ),
    SchemaMigration(
        14,
        "dual_agent_workflow_jobs.process_identity",
        _migration_workflow_job_process_identity,
    ),
    SchemaMigration(
        15,
        "historical_operation_claims",
        _migration_historical_operation_claims,
    ),
    SchemaMigration(
        16,
        "event_idempotency_claims",
        _migration_event_idempotency_claims,
    ),
)

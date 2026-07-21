from __future__ import annotations

import json
import runpy
import sqlite3
from pathlib import Path

import pytest

import supervisor.schema_migrations as schema_migrations
from supervisor.evidence_ledger import (
    LEGACY_EVENT_HASH_SCHEMA_VERSION,
    NATIVE_GENESIS,
    build_ledger_fields,
    verify_event_chain_structure,
)
from supervisor.quality_projection import (
    QUALITY_TREND_PROJECTION_EVENT,
    quality_trend_projection_event_payload,
    rebuild_quality_trend_projection,
)
from supervisor.schema_migrations import (
    LegacyEventLedgerBackfillRequired,
    QualityProjectionBackfillRequired,
    applied_migrations,
    migrate_legacy_event_ledger_offline,
    migrate_quality_projection_evidence_offline,
    run_forward_migrations,
)
from supervisor.state import State


EXPECTED_MIGRATIONS = [
    {"version": 1, "name": "actions.resume_requested_at"},
    {"version": 2, "name": "dual_agent_workflow_jobs.idempotency_token"},
    {"version": 3, "name": "dual_agent_workflow_jobs.terminal_outcome"},
    {"version": 4, "name": "dual_agent_workflow_jobs.recovery_points"},
    {"version": 5, "name": "dual_agent_workflow_jobs.recovery_claims"},
    {"version": 6, "name": "dual_agent_workflow_jobs.dispatcher_leases"},
    {"version": 7, "name": "supervisor_lessons"},
    {"version": 8, "name": "supervisor_quality_trends"},
    {"version": 9, "name": "supervisor_autoresearch_experiments"},
    {"version": 10, "name": "supervisor_quality_trends.policy_overlay_columns"},
    {"version": 11, "name": "quality_trend_audits"},
    {"version": 12, "name": "events.tamper_evident_ledger"},
    {"version": 13, "name": "storage.integrity_v2"},
    {"version": 14, "name": "dual_agent_workflow_jobs.process_identity"},
    {"version": 15, "name": "historical_operation_claims"},
    {"version": 16, "name": "event_idempotency_claims"},
    {"version": 17, "name": "dual_agent_workflow_jobs.spawn_ownership"},
    {
        "version": 18,
        "name": "historical_operation_claims.execution_ownership",
    },
    {"version": 19, "name": "verdicts.decision_id"},
]


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
    assert applied_migrations(conn) == EXPECTED_MIGRATIONS


def test_forward_migrations_are_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, resume_requested_at INTEGER)")

    run_forward_migrations(conn)
    run_forward_migrations(conn)

    assert applied_migrations(conn) == EXPECTED_MIGRATIONS
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1


def test_forward_migration_adds_unique_decision_binding_to_legacy_verdicts(
    tmp_path,
) -> None:
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE verdicts (
             verdict_id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             phase TEXT NOT NULL,
             model TEXT NOT NULL,
             output_json TEXT NOT NULL,
             created_at INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO verdicts(
             run_id, phase, model, output_json, created_at)
           VALUES('legacy-run', 'legacy-phase', 'legacy-model', '{}', 1)"""
    )

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(verdicts)").fetchall()
    }
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(verdicts)").fetchall()
    }
    assert "decision_id" in columns
    assert "idx_verdicts_decision_id" in indexes
    assert conn.execute(
        "SELECT decision_id FROM verdicts"
    ).fetchone()[0] is None
    conn.execute(
        """INSERT INTO verdicts(
             decision_id, run_id, phase, model, output_json, created_at)
           VALUES('decision-1', 'run', 'phase', 'model', '{}', 2)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO verdicts(
                 decision_id, run_id, phase, model, output_json, created_at)
               VALUES('decision-1', 'run', 'phase', 'model', '{}', 3)"""
        )


def test_state_startup_migrates_legacy_verdicts_before_creating_index(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE verdicts (
             verdict_id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             event_id INTEGER,
             phase TEXT NOT NULL,
             layer TEXT,
             model TEXT NOT NULL,
             output_json TEXT NOT NULL,
             latency_ms INTEGER,
             mode TEXT,
             created_at INTEGER NOT NULL
           )"""
    )
    conn.commit()
    conn.close()

    state = State(str(db_path))

    columns = {
        row["name"]
        for row in state._conn.execute(
            "PRAGMA table_info(verdicts)"
        ).fetchall()
    }
    indexes = {
        row["name"]
        for row in state._conn.execute(
            "PRAGMA index_list(verdicts)"
        ).fetchall()
    }
    assert "decision_id" in columns
    assert "idx_verdicts_decision_id" in indexes


def test_forward_migrations_roll_back_all_ddl_after_late_failure(
    tmp_path,
    monkeypatch,
):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row

    def fail_after_ddl(connection):
        connection.execute("CREATE TABLE injected_failure_probe (id INTEGER)")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(
        schema_migrations,
        "MIGRATIONS",
        (
            *schema_migrations.MIGRATIONS,
            schema_migrations.SchemaMigration(
                16,
                "test.injected_failure",
                fail_after_ddl,
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="injected migration failure"):
        run_forward_migrations(conn)

    persisted_objects = {
        row["name"]
        for row in conn.execute(
            """SELECT name
                 FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'"""
        ).fetchall()
    }
    assert persisted_objects == set()
    assert conn.in_transaction is False


def test_legacy_interleaved_events_backfill_contiguous_per_run_sequences(
    tmp_path,
    monkeypatch,
):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
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
    conn.execute("CREATE TABLE event_update_count (count INTEGER NOT NULL)")
    conn.execute("INSERT INTO event_update_count(count) VALUES(0)")
    conn.execute(
        """CREATE TRIGGER count_legacy_event_updates
           AFTER UPDATE ON events
           BEGIN
             UPDATE event_update_count SET count=count + 1;
           END"""
    )
    conn.executemany(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES(?, ?, 'test', 'event_msg', ?)""",
        (
            (
                "run-a",
                101,
                '{"index": 1, "token": "[REDACTED_API_KEY]"}',
            ),
            ("run-b", 102, '{"index":1}'),
            ("run-a", 103, '{"index":2}'),
        ),
    )
    original_payloads = [
        row["payload_json"]
        for row in conn.execute(
            "SELECT payload_json FROM events ORDER BY event_id"
        ).fetchall()
    ]

    run_forward_migrations(conn)

    run_a = conn.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events
            WHERE run_id='run-a'
            ORDER BY event_id ASC"""
    ).fetchall()
    run_b = conn.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events
            WHERE run_id='run-b'
            ORDER BY event_id ASC"""
    ).fetchall()

    assert [row["event_id"] for row in run_a] == [1, 3]
    assert [row["event_sequence"] for row in run_a] == [1, 2]
    assert [row["event_sequence"] for row in run_b] == [1]
    assert run_a[0]["ledger_genesis_kind"] == "legacy-import"
    assert run_b[0]["ledger_genesis_kind"] == "legacy-import"
    assert (
        verify_event_chain_structure(run_a, expected_run_id="run-a").valid
        is True
    )
    assert (
        verify_event_chain_structure(run_b, expected_run_id="run-b").valid
        is True
    )
    assert conn.execute(
        "SELECT count FROM event_update_count"
    ).fetchone()["count"] == 3
    assert [
        row["payload_json"]
        for row in conn.execute(
            "SELECT payload_json FROM events ORDER BY event_id"
        ).fetchall()
    ] == original_payloads
    monkeypatch.setattr(
        schema_migrations,
        "_legacy_event_backfill_affected_event_count",
        lambda _conn: pytest.fail(
            "completed ledger must not be rescanned on normal startup"
        ),
    )
    run_forward_migrations(conn)
    assert conn.execute(
        "SELECT count FROM event_update_count"
    ).fetchone()["count"] == 3


def test_legacy_event_import_authenticates_exact_payload_json_bytes(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
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
    original_payload_json = '{"value": 1}'
    conn.execute(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES('legacy-run', 101, 'test', 'event_msg', ?)""",
        (original_payload_json,),
    )

    run_forward_migrations(conn)

    [persisted] = conn.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events"""
    ).fetchall()
    assert persisted["payload_json"] == original_payload_json
    assert verify_event_chain_structure(
        [persisted],
        expected_run_id="legacy-run",
    ).valid is True

    tampered = dict(persisted)
    tampered["payload_json"] = '{"value":1}'
    verification = verify_event_chain_structure(
        [tampered],
        expected_run_id="legacy-run",
    )

    assert verification.valid is False
    assert verification.failure_code == "legacy_raw_payload_commitment_mismatch"


def test_legacy_event_import_rejects_duplicate_payload_keys(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
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
    payload_json = (
        '{"token":"sk-proj-super-secret-value","token":"safe"}'
    )
    conn.execute(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES('legacy-run', 101, 'test', 'event_msg', ?)""",
        (payload_json,),
    )
    conn.commit()

    with pytest.raises(
        RuntimeError,
        match="duplicate JSON object key: 'token'",
    ):
        run_forward_migrations(conn)

    assert conn.execute(
        "SELECT payload_json FROM events"
    ).fetchone()["payload_json"] == payload_json
    assert {
        row["name"]
        for row in conn.execute("PRAGMA table_info(events)").fetchall()
    } == {
        "event_id",
        "run_id",
        "ts",
        "source",
        "kind",
        "payload_json",
    }


def test_large_legacy_event_import_fails_fast_then_runs_offline(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    payload_json = '{"token": "[REDACTED_API_KEY]", "value": 1}'
    conn.execute(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES('legacy-run', 101, 'test', 'event_msg', ?)""",
        (payload_json,),
    )
    conn.commit()
    monkeypatch.setattr(
        schema_migrations,
        "MAX_STARTUP_LEGACY_EVENT_BACKFILL",
        0,
    )

    with pytest.raises(
        LegacyEventLedgerBackfillRequired,
        match="migrate_legacy_event_ledger_offline",
    ):
        run_forward_migrations(conn)
    conn.close()

    migrate_legacy_event_ledger_offline(db_path)

    migrated = sqlite3.connect(db_path)
    migrated.row_factory = sqlite3.Row
    [row] = migrated.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events"""
    ).fetchall()
    assert row["payload_json"] == payload_json
    assert row["ledger_genesis_kind"] == "legacy-import"
    assert verify_event_chain_structure(
        [row],
        expected_run_id="legacy-run",
    ).valid is True
    assert applied_migrations(migrated) == EXPECTED_MIGRATIONS


def test_offline_backfill_rejects_unreproducible_prepopulated_event_hash(
    tmp_path,
):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE events (
             event_id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             ts INTEGER NOT NULL,
             source TEXT NOT NULL,
             kind TEXT NOT NULL,
             payload_json TEXT NOT NULL,
             event_sequence INTEGER,
             previous_event_hash TEXT,
             event_hash TEXT,
             canonical_payload_hash TEXT,
             artifact_manifest_hash TEXT,
             ledger_genesis_kind TEXT
           )"""
    )
    conn.execute(
        """INSERT INTO events(
             run_id, ts, source, kind, payload_json, event_hash)
           VALUES('legacy-run', 101, 'test', 'event_msg',
                  '{"value":1}', ?)""",
        ("f" * 64,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(
        RuntimeError,
        match="conflicting pre-populated event ledger metadata",
    ):
        migrate_legacy_event_ledger_offline(db_path)


def test_legacy_event_import_refuses_to_re_redact_historical_payloads(
    tmp_path,
):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
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
    payload_json = '{"token":"sk-proj-not-a-real-key"}'
    conn.execute(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES('legacy-run', 101, 'test', 'event_msg', ?)""",
        (payload_json,),
    )
    conn.commit()

    with pytest.raises(
        RuntimeError,
        match="refusing to rewrite historical evidence",
    ):
        run_forward_migrations(conn)

    assert conn.execute(
        "SELECT payload_json FROM events"
    ).fetchone()["payload_json"] == payload_json
    assert {
        row["name"]
        for row in conn.execute("PRAGMA table_info(events)").fetchall()
    } == {
        "event_id",
        "run_id",
        "ts",
        "source",
        "kind",
        "payload_json",
    }


def test_legacy_import_tolerates_structural_stamping_for_legacy_runs(
    tmp_path,
):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    accepted_gate_json = json.dumps({
        "status": "accepted",
        "task_id": "legacy-task",
        "gate": "outcome_review",
        "handoff_packet_path": "",
    })
    unstamped_progress_json = json.dumps({
        "milestone": "planning",
        "task_id": "legacy-task",
    })
    conn.execute(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES('legacy-run', 101, 'dual_agent',
                  'dual_agent_gate_result', ?)""",
        (accepted_gate_json,),
    )
    conn.execute(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES('legacy-run', 102, 'dual_agent',
                  'dual_agent_progress', ?)""",
        (unstamped_progress_json,),
    )
    conn.commit()
    conn.close()

    migrate_legacy_event_ledger_offline(db_path)

    migrated = sqlite3.connect(db_path)
    migrated.row_factory = sqlite3.Row
    rows = migrated.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events ORDER BY event_id"""
    ).fetchall()
    assert [row["payload_json"] for row in rows] == [
        accepted_gate_json,
        unstamped_progress_json,
    ]
    assert rows[0]["ledger_genesis_kind"] == "legacy-import"
    assert all(row["event_hash"] for row in rows)
    assert verify_event_chain_structure(
        rows,
        expected_run_id="legacy-run",
    ).valid is True
    assert applied_migrations(migrated) == EXPECTED_MIGRATIONS


def test_offline_import_still_refuses_redaction_deltas_in_legacy_runs(
    tmp_path,
):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    payload_json = '{"token":"sk-proj-not-a-real-key"}'
    conn.execute(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES('legacy-run', 101, 'test', 'event_msg', ?)""",
        (payload_json,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(
        RuntimeError,
        match="refusing to rewrite historical evidence",
    ):
        migrate_legacy_event_ledger_offline(db_path)

    unchanged = sqlite3.connect(db_path)
    assert unchanged.execute(
        "SELECT payload_json FROM events"
    ).fetchone()[0] == payload_json


def test_offline_import_refuses_structural_delta_for_native_chains(
    tmp_path,
):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE events (
             event_id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             event_sequence INTEGER,
             ts INTEGER NOT NULL,
             source TEXT NOT NULL,
             kind TEXT NOT NULL,
             payload_json TEXT NOT NULL,
             previous_event_hash TEXT,
             event_hash TEXT,
             canonical_payload_hash TEXT,
             artifact_manifest_hash TEXT,
             ledger_genesis_kind TEXT
           )"""
    )
    conn.execute(
        """INSERT INTO events(
             run_id, ts, source, kind, payload_json, ledger_genesis_kind)
           VALUES('native-run', 101, 'dual_agent',
                  'dual_agent_gate_result', ?, ?)""",
        (
            json.dumps({
                "status": "accepted",
                "task_id": "native-task",
                "gate": "outcome_review",
                "handoff_packet_path": "",
            }),
            NATIVE_GENESIS,
        ),
    )
    conn.commit()
    conn.close()

    with pytest.raises(
        RuntimeError,
        match="requires redaction or trace normalization",
    ):
        migrate_legacy_event_ledger_offline(db_path)


def test_ledger_migration_rejects_forged_prepopulated_hashes(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE events (
             event_id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             event_sequence INTEGER,
             ts INTEGER NOT NULL,
             source TEXT NOT NULL,
             kind TEXT NOT NULL,
             payload_json TEXT NOT NULL,
             previous_event_hash TEXT,
             event_hash TEXT,
             canonical_payload_hash TEXT,
             artifact_manifest_hash TEXT,
             ledger_genesis_kind TEXT
           )"""
    )
    forged_hash = "f" * 64
    conn.execute(
        """INSERT INTO events(
             run_id, event_sequence, ts, source, kind, payload_json,
             previous_event_hash, event_hash, canonical_payload_hash,
             artifact_manifest_hash, ledger_genesis_kind)
           VALUES(
             'forged-run', 1, 101, 'test', 'event_msg', '{"value":1}',
             NULL, ?, ?, ?, 'legacy-import'
           )""",
        (forged_hash, forged_hash, forged_hash),
    )
    conn.commit()

    with pytest.raises(
        RuntimeError,
        match="conflicting pre-populated event ledger metadata",
    ):
        run_forward_migrations(conn)

    row = conn.execute(
        """SELECT event_hash, canonical_payload_hash,
                  artifact_manifest_hash
             FROM events"""
    ).fetchone()
    assert dict(row) == {
        "event_hash": forged_hash,
        "canonical_payload_hash": forged_hash,
        "artifact_manifest_hash": forged_hash,
    }


def test_prepopulated_ledger_verification_over_bound_punts_to_offline(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE events (
             event_id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             event_sequence INTEGER,
             ts INTEGER NOT NULL,
             source TEXT NOT NULL,
             kind TEXT NOT NULL,
             payload_json TEXT NOT NULL,
             previous_event_hash TEXT,
             event_hash TEXT,
             canonical_payload_hash TEXT,
             artifact_manifest_hash TEXT,
             ledger_genesis_kind TEXT
           )"""
    )
    fields = build_ledger_fields(
        run_id="v2-run",
        event_sequence=1,
        ts=101,
        source="test",
        kind="event_msg",
        payload={"value": 1},
        previous_event_hash=None,
        ledger_genesis_kind=NATIVE_GENESIS,
        event_hash_schema_version=LEGACY_EVENT_HASH_SCHEMA_VERSION,
    )
    conn.execute(
        """INSERT INTO events(
             run_id, event_sequence, ts, source, kind, payload_json,
             previous_event_hash, event_hash, canonical_payload_hash,
             artifact_manifest_hash, ledger_genesis_kind)
           VALUES(
             'v2-run', 1, 101, 'test', 'event_msg', '{"value":1}',
             NULL, ?, ?, ?, ?
           )""",
        (
            fields.event_hash,
            fields.canonical_payload_hash,
            fields.artifact_manifest_hash,
            fields.ledger_genesis_kind,
        ),
    )
    conn.commit()
    monkeypatch.setattr(
        schema_migrations,
        "MAX_STARTUP_LEGACY_EVENT_BACKFILL",
        0,
    )

    with pytest.raises(
        LegacyEventLedgerBackfillRequired,
        match="migrate_legacy_event_ledger_offline",
    ):
        run_forward_migrations(conn)
    conn.close()

    migrate_legacy_event_ledger_offline(db_path)

    migrated = sqlite3.connect(db_path)
    migrated.row_factory = sqlite3.Row
    [event] = migrated.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events"""
    ).fetchall()
    assert event["event_hash"] == fields.event_hash
    assert applied_migrations(migrated) == EXPECTED_MIGRATIONS


def test_ledger_migration_preserves_valid_prepopulated_v2_hashes(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE events (
             event_id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             event_sequence INTEGER,
             ts INTEGER NOT NULL,
             source TEXT NOT NULL,
             kind TEXT NOT NULL,
             payload_json TEXT NOT NULL,
             previous_event_hash TEXT,
             event_hash TEXT,
             canonical_payload_hash TEXT,
             artifact_manifest_hash TEXT,
             ledger_genesis_kind TEXT
           )"""
    )
    payload = {"value": 1}
    fields = build_ledger_fields(
        run_id="v2-run",
        event_sequence=1,
        ts=101,
        source="test",
        kind="event_msg",
        payload=payload,
        previous_event_hash=None,
        ledger_genesis_kind=NATIVE_GENESIS,
        event_hash_schema_version=LEGACY_EVENT_HASH_SCHEMA_VERSION,
    )
    conn.execute(
        """INSERT INTO events(
             run_id, event_sequence, ts, source, kind, payload_json,
             previous_event_hash, event_hash, canonical_payload_hash,
             artifact_manifest_hash, ledger_genesis_kind)
           VALUES(
             'v2-run', 1, 101, 'test', 'event_msg', '{"value":1}',
             NULL, ?, ?, ?, ?
           )""",
        (
            fields.event_hash,
            fields.canonical_payload_hash,
            fields.artifact_manifest_hash,
            fields.ledger_genesis_kind,
        ),
    )
    conn.commit()

    run_forward_migrations(conn)

    [event] = conn.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events"""
    ).fetchall()
    assert event["event_hash"] == fields.event_hash
    assert verify_event_chain_structure(
        [event],
        expected_run_id="v2-run",
    ).valid is True


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
    assert applied_migrations(state._conn) == EXPECTED_MIGRATIONS


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
             worker_pgid INTEGER,
             worker_started_at REAL,
             worker_containment_id TEXT,
             worker_reaped_at INTEGER,
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
             worker_pgid INTEGER,
             worker_started_at REAL,
             worker_reaped_at INTEGER,
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
    assert applied_migrations(conn) == EXPECTED_MIGRATIONS
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


def test_forward_migration_adds_workflow_job_process_identity(tmp_path):
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
             returncode INTEGER,
             error TEXT,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(dual_agent_workflow_jobs)"
        ).fetchall()
    }
    assert {
        "worker_pgid",
        "worker_started_at",
        "worker_containment_id",
        "worker_reaped_at",
    } <= columns
    assert applied_migrations(conn) == EXPECTED_MIGRATIONS


def test_forward_migration_adds_workflow_job_spawn_ownership(tmp_path):
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
             returncode INTEGER,
             error TEXT,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, request_path,
             result_path, log_path, created_at, updated_at)
           VALUES(
             'retryable-job', 'run', 'task', '.', 'submitted',
             'req', 'res', 'log', 1, 1
           )"""
    )

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(dual_agent_workflow_jobs)"
        ).fetchall()
    }
    assert {
        "worker_prepared_at",
        "cleanup_attempts",
        "cleanup_escalated_at",
    } <= columns
    row = conn.execute(
        """SELECT cleanup_attempts
             FROM dual_agent_workflow_jobs
            WHERE job_id='retryable-job'"""
    ).fetchone()
    assert row["cleanup_attempts"] == 0
    index = conn.execute(
        """SELECT sql
             FROM sqlite_master
            WHERE type='index'
              AND name='idx_dual_agent_workflow_jobs_dispatchable'"""
    ).fetchone()
    assert index is not None
    normalized = " ".join(str(index["sql"]).split())
    assert "terminal_outcome_json IS NULL" in normalized
    assert "(pid IS NULL OR worker_reaped_at IS NOT NULL)" in normalized
    assert applied_migrations(conn) == EXPECTED_MIGRATIONS


def test_forward_migration_adds_historical_execution_ownership(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE historical_operation_claims (
             operation_id TEXT PRIMARY KEY,
             request_hash TEXT NOT NULL,
             operation TEXT NOT NULL,
             status TEXT NOT NULL,
             terminal_event_id INTEGER,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO historical_operation_claims(
             operation_id, request_hash, operation, status,
             terminal_event_id, created_at, updated_at)
           VALUES('historical-old', ?, 'replay', 'failed', 1, 1, 1)""",
        ("a" * 64,),
    )

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(historical_operation_claims)"
        ).fetchall()
    }
    assert {
        "execution_owner_token",
        "execution_generation",
        "execution_heartbeat_at",
    } <= columns
    row = conn.execute(
        """SELECT execution_owner_token, execution_generation,
                  execution_heartbeat_at
             FROM historical_operation_claims
            WHERE operation_id='historical-old'"""
    ).fetchone()
    assert dict(row) == {
        "execution_owner_token": None,
        "execution_generation": 0,
        "execution_heartbeat_at": None,
    }
    index = conn.execute(
        """SELECT sql
             FROM sqlite_master
            WHERE type='index'
              AND name='idx_historical_operation_claims_execution_owner'"""
    ).fetchone()
    assert index is not None
    normalized = " ".join(str(index["sql"]).split())
    assert "CREATE UNIQUE INDEX" in normalized
    assert "execution_owner_token IS NOT NULL" in normalized
    conn.execute(
        """UPDATE historical_operation_claims
              SET execution_owner_token='unique-owner'
            WHERE operation_id='historical-old'"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO historical_operation_claims(
                 operation_id, request_hash, operation, status,
                 terminal_event_id, execution_owner_token,
                 created_at, updated_at)
               VALUES(
                 'historical-conflict', ?, 'replay', 'running', NULL,
                 'unique-owner', 2, 2
               )""",
            ("b" * 64,),
        )
    assert applied_migrations(conn) == EXPECTED_MIGRATIONS


def test_historical_execution_ownership_migration_requires_quiescence(
    tmp_path,
):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE historical_operation_claims (
             operation_id TEXT PRIMARY KEY,
             request_hash TEXT NOT NULL,
             operation TEXT NOT NULL,
             status TEXT NOT NULL,
             terminal_event_id INTEGER,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO historical_operation_claims(
             operation_id, request_hash, operation, status,
             terminal_event_id, created_at, updated_at)
           VALUES('historical-live', ?, 'replay', 'running', NULL, 1, 1)""",
        ("c" * 64,),
    )
    conn.commit()
    original_columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(historical_operation_claims)"
        ).fetchall()
    ]

    with pytest.raises(
        RuntimeError,
        match="execution-ownership migration requires quiescence",
    ):
        run_forward_migrations(conn)

    assert [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(historical_operation_claims)"
        ).fetchall()
    ] == original_columns
    migration_table = conn.execute(
        """SELECT 1
             FROM sqlite_master
            WHERE type='table' AND name='schema_migrations'"""
    ).fetchone()
    if migration_table is not None:
        assert conn.execute(
            """SELECT 1
                 FROM schema_migrations
                WHERE version=18"""
        ).fetchone() is None
    claim = conn.execute(
        """SELECT status
             FROM historical_operation_claims
            WHERE operation_id='historical-live'"""
    ).fetchone()
    assert claim["status"] == "running"


def test_process_identity_migration_requires_quiescent_pre_identity_jobs(
    tmp_path,
):
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
             worker_pgid INTEGER,
             worker_started_at REAL,
             worker_reaped_at INTEGER,
             request_path TEXT NOT NULL,
             result_path TEXT NOT NULL,
             log_path TEXT NOT NULL,
             returncode INTEGER,
             error TEXT,
             created_at INTEGER NOT NULL,
             updated_at INTEGER NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, pid, request_path,
             result_path, log_path, created_at, updated_at)
           VALUES(
             'live-job', 'run', 'task', '.', 'running', 4242,
             'req', 'res', 'log', 1, 1
           )"""
    )
    conn.commit()

    with pytest.raises(
        RuntimeError,
        match="process-identity migration requires quiescence",
    ):
        run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(dual_agent_workflow_jobs)"
        ).fetchall()
    }
    assert "worker_containment_id" not in columns

    conn.execute(
        """UPDATE dual_agent_workflow_jobs
              SET pid=NULL, status='failed'"""
    )
    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(dual_agent_workflow_jobs)"
        ).fetchall()
    }
    assert {
        "worker_pgid",
        "worker_started_at",
        "worker_containment_id",
        "worker_reaped_at",
    } <= columns


@pytest.mark.parametrize(
    ("column", "replacement"),
    (
        ("pid", 22),
        ("worker_pgid", 23),
        ("worker_started_at", 2.5),
        ("worker_prepared_at", 2.0),
        ("worker_containment_id", "containment-2"),
        ("worker_reaped_at", 200),
        ("cleanup_attempts", 2),
        ("cleanup_escalated_at", 200),
    ),
)
def test_terminal_freeze_includes_process_identity_fields(
    tmp_path,
    column,
    replacement,
):
    state = State(str(tmp_path / "state.db"))
    state._conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, pid, worker_pgid,
             worker_started_at, worker_containment_id, worker_reaped_at,
             request_path, result_path, log_path, recovery_point,
             terminal_status, terminal_outcome_json,
             terminal_outcome_recorded_at, returncode, error,
             created_at, updated_at)
           VALUES(
             'terminal-job', 'run', 'task', '.', 'completed', 11, 12,
             1.5, 'containment-1', 100, 'req', 'res', 'log', 'terminal',
             'completed', '{}', 100, 0, NULL, 1, 1
           )"""
    )
    state._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        state._conn.execute(
            f"""UPDATE dual_agent_workflow_jobs
                   SET {column}=?
                 WHERE job_id='terminal-job'""",
            (replacement,),
        )


def test_terminal_workflow_job_cannot_be_deleted(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state._conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, request_path,
             result_path, log_path, recovery_point, terminal_status,
             terminal_outcome_json, terminal_outcome_recorded_at,
             returncode, created_at, updated_at)
           VALUES(
             'terminal-job', 'run', 'task', '.', 'completed', 'req',
             'res', 'log', 'terminal', 'completed', '{}', 100, 0, 1, 1
           )"""
    )
    state._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        state._conn.execute(
            """DELETE FROM dual_agent_workflow_jobs
                WHERE job_id='terminal-job'"""
        )

    assert state.get_dual_agent_workflow_job(job_id="terminal-job") is not None


def test_legacy_completed_workflow_job_cannot_be_deleted(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state._conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, request_path,
             result_path, log_path, recovery_point, created_at, updated_at)
           VALUES(
             'legacy-terminal-job', 'run', 'task', '.', 'completed',
             'req', 'res', 'log', 'terminal', 1, 1
           )"""
    )
    state._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        state._conn.execute(
            """DELETE FROM dual_agent_workflow_jobs
                WHERE job_id='legacy-terminal-job'"""
        )


def test_worker_reaped_at_is_write_once_before_terminal(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state._conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, pid, worker_pgid,
             worker_started_at, worker_containment_id, request_path,
             result_path, log_path, recovery_point, created_at, updated_at)
           VALUES(
             'running-job', 'run', 'task', '.', 'running', 11, 12,
             1.5, 'containment-1', 'req', 'res', 'log', 'spawned', 1, 1
           )"""
    )
    state._conn.execute(
        """UPDATE dual_agent_workflow_jobs
              SET worker_reaped_at=100
            WHERE job_id='running-job'"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable once recorded"):
        state._conn.execute(
            """UPDATE dual_agent_workflow_jobs
                  SET worker_reaped_at=101
            WHERE job_id='running-job'"""
        )


def test_worker_reaped_at_is_write_once_after_terminal(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state._conn.execute(
        """INSERT INTO dual_agent_workflow_jobs(
             job_id, run_id, task_id, cwd, status, pid, worker_pgid,
             worker_started_at, worker_containment_id, worker_reaped_at,
             request_path, result_path, log_path, recovery_point,
             terminal_status, terminal_outcome_json,
             terminal_outcome_recorded_at, returncode, error,
             created_at, updated_at)
           VALUES(
             'terminal-pending-reap', 'run', 'task', '.', 'completed', 11, 12,
             1.5, 'containment-1', NULL, 'req', 'res', 'log', 'terminal',
             'completed', '{}', 100, 0, NULL, 1, 1
           )"""
    )
    state._conn.execute(
        """UPDATE dual_agent_workflow_jobs
              SET worker_reaped_at=100
            WHERE job_id='terminal-pending-reap'"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable once recorded"):
        state._conn.execute(
            """UPDATE dual_agent_workflow_jobs
                  SET worker_reaped_at=101
                WHERE job_id='terminal-pending-reap'"""
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
    migrations = applied_migrations(conn)
    assert {"version": 7, "name": "supervisor_lessons"} in migrations
    assert {"version": 8, "name": "supervisor_quality_trends"} in migrations


def test_forward_migration_adds_supervisor_quality_trends(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supervisor_quality_trends)").fetchall()
    }
    assert {
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
        "details_json",
        "computed_at",
    } <= columns
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(supervisor_quality_trends)").fetchall()
    }
    assert "idx_supervisor_quality_trends_task_gate" in indexes
    assert {
        "version": 8,
        "name": "supervisor_quality_trends",
    } in applied_migrations(conn)


def test_forward_migration_adds_autoresearch_experiment_queue(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supervisor_autoresearch_experiments)").fetchall()
    }
    assert {
        "experiment_id",
        "signal_key",
        "status",
        "task_class",
        "gate",
        "taxonomy_code",
        "experiment_json",
        "attempt_json",
        "provenance_json",
        "report_only_reason",
        "proposal_pointer_json",
        "last_run_id",
        "last_run_started_at",
        "activated_at",
        "activated_by",
        "activation_channel",
    } <= columns
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(supervisor_autoresearch_experiments)").fetchall()
    }
    assert "idx_supervisor_autoresearch_experiments_status" in indexes
    assert {
        "version": 9,
        "name": "supervisor_autoresearch_experiments",
    } in applied_migrations(conn)


def test_forward_migration_adds_policy_overlay_and_lesson_hygiene_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row

    run_forward_migrations(conn)

    trend_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supervisor_quality_trends)").fetchall()
    }
    lesson_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supervisor_lessons)").fetchall()
    }
    assert {"policy_overlay_hash", "policy_proposal_id"} <= trend_columns
    assert {
        "normalized_key",
        "observed_count",
        "injection_count",
        "recurrence_count",
        "retired_at",
    } <= lesson_columns
    assert {
        "version": 10,
        "name": "supervisor_quality_trends.policy_overlay_columns",
    } in applied_migrations(conn)


def test_forward_migration_adds_immutable_quality_trend_audits(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row

    run_forward_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(quality_trend_audits)").fetchall()
    }
    assert {
        "run_id",
        "gate",
        "sample_size",
        "false_accept_count",
        "false_accept_denominator",
        "false_accept_rate",
        "audit_details_json",
        "computed_at",
    } <= columns
    primary_key = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(quality_trend_audits)").fetchall()
        if int(row["pk"])
    ]
    assert primary_key == ["run_id", "gate", "computed_at"]
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(quality_trend_audits)").fetchall()
    }
    assert "idx_quality_trend_audits_run_gate" in indexes
    assert {
        "version": 11,
        "name": "quality_trend_audits",
    } in applied_migrations(conn)


def test_quality_audit_backfill_rejects_conflicting_immutable_history(
    tmp_path,
):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE supervisor_quality_trends (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             task_class TEXT NOT NULL,
             gate TEXT NOT NULL,
             accepted INTEGER NOT NULL,
             first_pass_accepted INTEGER NOT NULL,
             revision_rounds INTEGER NOT NULL,
             time_to_accepted_outcome_s REAL,
             p11_audit_sample_size INTEGER NOT NULL DEFAULT 0,
             false_accept_count INTEGER NOT NULL DEFAULT 0,
             false_accept_denominator INTEGER NOT NULL DEFAULT 0,
             false_accept_rate REAL NOT NULL DEFAULT 0.0,
             details_json TEXT NOT NULL DEFAULT '{}',
             computed_at INTEGER NOT NULL,
             UNIQUE(run_id, gate)
           )"""
    )
    conn.execute(
        """INSERT INTO supervisor_quality_trends(
             run_id, task_id, task_class, gate, accepted,
             first_pass_accepted, revision_rounds,
             time_to_accepted_outcome_s, p11_audit_sample_size,
             false_accept_count, false_accept_denominator,
             false_accept_rate, details_json, computed_at)
           VALUES(
             'audit-run', 'task', 'source_change', 'outcome_review',
             1, 1, 0, 1.0, 3, 1, 3, ?, '{"p11_audit":{"source":"trend"}}',
             123
           )""",
        (1 / 3,),
    )
    conn.execute(
        """CREATE TABLE quality_trend_audits (
             run_id TEXT NOT NULL,
             gate TEXT NOT NULL,
             sample_size INTEGER NOT NULL,
             false_accept_count INTEGER NOT NULL,
             false_accept_denominator INTEGER NOT NULL,
             false_accept_rate REAL NOT NULL,
             audit_details_json TEXT NOT NULL DEFAULT '{}',
             computed_at INTEGER NOT NULL,
             PRIMARY KEY(run_id, gate, computed_at)
           )"""
    )
    conn.execute(
        """INSERT INTO quality_trend_audits(
             run_id, gate, sample_size, false_accept_count,
             false_accept_denominator, false_accept_rate,
             audit_details_json, computed_at)
           VALUES(
             'audit-run', 'outcome_review', 4, 0, 4, 0.0,
             '{"source":"existing"}', 123
           )"""
    )
    conn.commit()

    with pytest.raises(
        RuntimeError,
        match="conflicting immutable quality audit history",
    ):
        run_forward_migrations(conn)

    row = conn.execute(
        """SELECT sample_size, false_accept_count,
                  false_accept_denominator, audit_details_json
             FROM quality_trend_audits
            WHERE run_id='audit-run'
              AND gate='outcome_review'
              AND computed_at=123"""
    ).fetchone()
    assert dict(row) == {
        "sample_size": 4,
        "false_accept_count": 0,
        "false_accept_denominator": 4,
        "audit_details_json": '{"source":"existing"}',
    }


def test_migration_backfills_canonical_quality_projection_evidence(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    conn.execute(
        """INSERT INTO events(run_id, ts, source, kind, payload_json)
           VALUES(
             'legacy-quality-run', 100, 'test', 'event_msg', '{"seed":true}'
           )"""
    )
    conn.execute(
        """CREATE TABLE supervisor_quality_trends (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             task_class TEXT NOT NULL,
             gate TEXT NOT NULL,
             accepted INTEGER NOT NULL,
             first_pass_accepted INTEGER NOT NULL,
             revision_rounds INTEGER NOT NULL,
             time_to_accepted_outcome_s REAL,
             p11_audit_sample_size INTEGER NOT NULL DEFAULT 0,
             false_accept_count INTEGER NOT NULL DEFAULT 0,
             false_accept_denominator INTEGER NOT NULL DEFAULT 0,
             false_accept_rate REAL NOT NULL DEFAULT 0.0,
             details_json TEXT NOT NULL DEFAULT '{}',
             computed_at INTEGER NOT NULL,
             UNIQUE(run_id, gate)
           )"""
    )
    conn.execute(
        """INSERT INTO supervisor_quality_trends(
             run_id, task_id, task_class, gate, accepted,
             first_pass_accepted, revision_rounds,
             time_to_accepted_outcome_s, p11_audit_sample_size,
             false_accept_count, false_accept_denominator,
             false_accept_rate, details_json, computed_at)
           VALUES(
             'legacy-quality-run', 'task', 'source_change',
             'outcome_review', 1, 0, 2, 12.5, 3, 1, 3, ?,
             '{"p11_audit":{"source":"legacy"}}', 123
           )""",
        (1 / 3,),
    )

    run_forward_migrations(conn)

    events = conn.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events
            WHERE run_id='legacy-quality-run'
            ORDER BY event_sequence"""
    ).fetchall()
    assert len(events) == 2
    assert events[1]["kind"] == QUALITY_TREND_PROJECTION_EVENT
    assert events[1]["source"] == "schema_migration"
    projection_payload = json.loads(events[1]["payload_json"])
    assert projection_payload["projection_row"]["gate"] == "outcome_review"
    assert projection_payload["projection_row"]["false_accept_rate"] == pytest.approx(
        1 / 3
    )
    assert verify_event_chain_structure(
        events,
        expected_run_id="legacy-quality-run",
    ).valid is True
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE events SET source='tampered' WHERE event_id=1"
        )
    conn.close()

    state = State(str(db_path))
    projection_events = [
        {
            "run_id": row["run_id"],
            "kind": row["kind"],
            "source": row["source"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in state._conn.execute(
            """SELECT run_id, source, kind, payload_json
                 FROM events
                WHERE kind=?
                ORDER BY run_id, event_sequence""",
            (QUALITY_TREND_PROJECTION_EVENT,),
        ).fetchall()
    ]
    assert rebuild_quality_trend_projection(
        projection_events
    ) == state.quality_trend_projection_snapshot()


def test_large_quality_projection_backfill_requires_offline_migration(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    conn.execute(
        """CREATE TABLE supervisor_quality_trends (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             task_class TEXT NOT NULL,
             gate TEXT NOT NULL,
             accepted INTEGER NOT NULL,
             first_pass_accepted INTEGER NOT NULL,
             revision_rounds INTEGER NOT NULL,
             time_to_accepted_outcome_s REAL,
             p11_audit_sample_size INTEGER NOT NULL DEFAULT 0,
             false_accept_count INTEGER NOT NULL DEFAULT 0,
             false_accept_denominator INTEGER NOT NULL DEFAULT 0,
             false_accept_rate REAL NOT NULL DEFAULT 0.0,
             policy_overlay_hash TEXT,
             policy_proposal_id TEXT,
             details_json TEXT NOT NULL DEFAULT '{}',
             computed_at INTEGER NOT NULL,
             UNIQUE(run_id, gate)
           )"""
    )
    conn.execute(
        """INSERT INTO supervisor_quality_trends(
             run_id, task_id, task_class, gate, accepted,
             first_pass_accepted, revision_rounds,
             time_to_accepted_outcome_s, p11_audit_sample_size,
             false_accept_count, false_accept_denominator,
             false_accept_rate, policy_overlay_hash,
             policy_proposal_id, details_json, computed_at)
           VALUES(
             'large-quality-run', 'task', 'source_change',
             'outcome_review', 1, 1, 0, 1.0, 0, 0, 0, 0.0,
             NULL, NULL, '{}', 123
           )"""
    )
    conn.commit()
    monkeypatch.setattr(
        schema_migrations,
        "MAX_STARTUP_QUALITY_PROJECTION_AFFECTED_ROWS",
        0,
    )

    with pytest.raises(
        QualityProjectionBackfillRequired,
        match="migrate_quality_projection_evidence_offline",
    ):
        run_forward_migrations(conn)

    assert conn.execute(
        """SELECT COUNT(*)
             FROM events
            WHERE kind=?""",
        (QUALITY_TREND_PROJECTION_EVENT,),
    ).fetchone()[0] == 0
    conn.close()

    migrate_quality_projection_evidence_offline(db_path)

    migrated = sqlite3.connect(db_path)
    assert migrated.execute(
        """SELECT COUNT(*)
             FROM events
            WHERE kind=?""",
        (QUALITY_TREND_PROJECTION_EVENT,),
    ).fetchone()[0] == 1
    assert {
        row["version"]: row["name"]
        for row in applied_migrations(migrated)
    }[13] == "storage.integrity_v2"
    migrated.close()


def test_quality_projection_backfill_bounds_total_event_scan(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.write_event(
        run_id="unrelated-run",
        source="test",
        kind="unrelated.event",
        payload={"status": "recorded"},
    )
    state._conn.execute(
        """INSERT INTO supervisor_quality_trends(
             run_id, task_id, task_class, gate, accepted,
             first_pass_accepted, revision_rounds,
             time_to_accepted_outcome_s, p11_audit_sample_size,
             false_accept_count, false_accept_denominator,
             false_accept_rate, policy_overlay_hash,
             policy_proposal_id, details_json, computed_at)
           VALUES(
             'quality-run', 'task', 'source_change', 'outcome_review',
             1, 1, 0, 1.0, 0, 0, 0, 0.0, '', '', '{}', 100
           )"""
    )
    state._conn.commit()

    with pytest.raises(
        QualityProjectionBackfillRequired,
        match="event_scan_limit_exceeded",
    ):
        schema_migrations._backfill_canonical_quality_projection_evidence(
            state._conn,
            max_affected_rows=10,
            max_event_scan_rows=0,
            max_trend_scan_rows=10,
        )


def test_quality_projection_backfill_bounds_projection_event_scan(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_quality_trend_row(
        run_id="quality-event-bound",
        task_id="task",
        task_class="source_change",
        gate="outcome_review",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=1.0,
    )

    with pytest.raises(
        QualityProjectionBackfillRequired,
        match="event_scan_limit_exceeded",
    ):
        schema_migrations._backfill_canonical_quality_projection_evidence(
            state._conn,
            max_affected_rows=10,
            max_event_scan_rows=0,
            max_trend_scan_rows=10,
        )


def test_quality_projection_backfill_is_one_pass_and_counts_only_affected_rows(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        state._conn.execute(
            """INSERT INTO supervisor_quality_trends(
                 run_id, task_id, task_class, gate, accepted,
                 first_pass_accepted, revision_rounds,
                 time_to_accepted_outcome_s, p11_audit_sample_size,
                 false_accept_count, false_accept_denominator,
                 false_accept_rate, policy_overlay_hash,
                 policy_proposal_id, details_json, computed_at)
               VALUES(?, ?, 'source_change', 'outcome_review', 1, 1, 0,
                      1.0, 0, 0, 0, 0.0, '', '', '{}', ?)""",
            (f"quality-run-{index}", f"task-{index}", 100 + index),
        )
    state._conn.commit()
    statements: list[str] = []
    state._conn.set_trace_callback(statements.append)
    try:
        schema_migrations._backfill_canonical_quality_projection_evidence(
            state._conn,
            max_affected_rows=3,
            max_event_scan_rows=10,
            max_trend_scan_rows=3,
        )
        first_count = state._conn.execute(
            """SELECT COUNT(*)
                 FROM events
                WHERE kind=?""",
            (QUALITY_TREND_PROJECTION_EVENT,),
        ).fetchone()[0]
        schema_migrations._backfill_canonical_quality_projection_evidence(
            state._conn,
            max_affected_rows=0,
            max_event_scan_rows=10,
            max_trend_scan_rows=3,
        )
    finally:
        state._conn.set_trace_callback(None)

    assert first_count == 3
    assert state._conn.execute(
        """SELECT COUNT(*)
             FROM events
            WHERE kind=?""",
        (QUALITY_TREND_PROJECTION_EVENT,),
    ).fetchone()[0] == 3
    normalized = [" ".join(statement.split()) for statement in statements]
    assert not any(
        "WHERE run_id=" in statement
        and "AND kind=" in statement
        and "SELECT payload_json" in statement
        for statement in normalized
    )
    assert any(
        "SELECT event_id FROM events ORDER BY event_id ASC LIMIT" in statement
        for statement in normalized
    )


def test_sqlite_quality_projection_migration_writer_redacts_before_hashing(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    payload = quality_trend_projection_event_payload(
        {
            "run_id": "migration-redaction-run",
            "task_id": "task",
            "task_class": "source_change",
            "gate": "outcome_review",
            "accepted": True,
            "first_pass_accepted": False,
            "revision_rounds": 1,
            "time_to_accepted_outcome_s": 1.5,
            "p11_audit_sample_size": 0,
            "false_accept_count": 0,
            "false_accept_denominator": 0,
            "false_accept_rate": 0.0,
            "policy_overlay_hash": "",
            "policy_proposal_id": "",
            "details": {
                "credential": (
                    "OPENAI_API_KEY=sk-proj-super-secret-value"
                )
            },
            "computed_at": 123,
        }
    )

    schema_migrations._append_legacy_quality_projection_event(
        state._conn,
        run_id="migration-redaction-run",
        payload=payload,
    )
    state._conn.commit()

    [event] = state._conn.execute(
        """SELECT event_id, run_id, event_sequence, ts, source, kind,
                  payload_json, previous_event_hash, event_hash,
                  canonical_payload_hash, artifact_manifest_hash,
                  ledger_genesis_kind
             FROM events
            WHERE run_id='migration-redaction-run'"""
    ).fetchall()
    persisted_payload = json.loads(event["payload_json"])

    assert "sk-proj-super-secret-value" not in event["payload_json"]
    assert (
        persisted_payload["projection_row"]["details"]["credential"]
        == "OPENAI_API_KEY=[REDACTED]"
    )
    assert verify_event_chain_structure(
        [event],
        expected_run_id="migration-redaction-run",
    ).valid is True


def test_postgres_quality_projection_migration_writer_redacts_before_hashing():
    class _Result:
        def __init__(self, *, scalar=None, rows=(), first=None):
            self._scalar = scalar
            self._rows = list(rows)
            self._first = first

        def scalar_one(self):
            return self._scalar

        def mappings(self):
            return self

        def __iter__(self):
            return iter(self._rows)

        def first(self):
            return self._first

    class _Bind:
        def __init__(self):
            self.inserted_event = None

        def execute(self, statement, params=None):
            sql = " ".join(str(statement).split())
            if "EXTRACT(EPOCH FROM clock_timestamp())" in sql:
                return _Result(scalar=200)
            if "FROM supervisor_quality_trends" in sql:
                return _Result(
                    rows=[
                        {
                            "run_id": "postgres-migration-redaction",
                            "task_id": "task",
                            "task_class": "source_change",
                            "gate": "outcome_review",
                            "accepted": True,
                            "first_pass_accepted": False,
                            "revision_rounds": 1,
                            "time_to_accepted_outcome_s": 1.5,
                            "p11_audit_sample_size": 0,
                            "false_accept_count": 0,
                            "false_accept_denominator": 0,
                            "false_accept_rate": 0.0,
                            "policy_overlay_hash": "",
                            "policy_proposal_id": "",
                            "details_json": {
                                "credential": (
                                    "OPENAI_API_KEY="
                                    "sk-proj-super-secret-value"
                                )
                            },
                            "computed_at": 123,
                        }
                    ]
                )
            if "SELECT payload_json FROM events" in sql:
                return _Result(first=None)
            if "SELECT event_id, event_sequence, event_hash" in sql:
                return _Result(first=None)
            if "INSERT INTO events" in sql:
                self.inserted_event = dict(params or {})
                return _Result()
            if "INSERT INTO event_stream_sequences" in sql:
                return _Result()
            raise AssertionError(f"unexpected migration SQL: {sql}")

    migration = runpy.run_path(
        str(Path("migrations/versions/20260712_0001_evidence_ledger.py"))
    )
    bind = _Bind()

    migration["_backfill_quality_projection_evidence"](bind)

    assert bind.inserted_event is not None
    payload_json = bind.inserted_event["payload_json"]
    payload = json.loads(payload_json)
    event = {
        "event_id": bind.inserted_event["event_id"],
        "event_sequence": bind.inserted_event["event_sequence"],
        "run_id": bind.inserted_event["run_id"],
        "ts": bind.inserted_event["ts"],
        "source": bind.inserted_event["source"],
        "kind": bind.inserted_event["kind"],
        "payload_json": payload_json,
        "previous_event_hash": bind.inserted_event["previous_event_hash"],
        "event_hash": bind.inserted_event["event_hash"],
        "canonical_payload_hash": bind.inserted_event[
            "canonical_payload_hash"
        ],
        "artifact_manifest_hash": bind.inserted_event[
            "artifact_manifest_hash"
        ],
        "ledger_genesis_kind": bind.inserted_event[
            "ledger_genesis_kind"
        ],
    }

    assert "sk-proj-super-secret-value" not in payload_json
    assert (
        payload["projection_row"]["details"]["credential"]
        == "OPENAI_API_KEY=[REDACTED]"
    )
    assert verify_event_chain_structure(
        [event],
        expected_run_id="postgres-migration-redaction",
    ).valid is True


def test_postgres_ledger_migration_recognizes_prepopulated_v2_hashes():
    migration = runpy.run_path(
        str(Path("migrations/versions/20260712_0001_evidence_ledger.py"))
    )
    payload = {"value": 1}
    expected = build_ledger_fields(
        run_id="postgres-v2-run",
        event_sequence=1,
        ts=101,
        source="test",
        kind="event_msg",
        payload=payload,
        previous_event_hash=None,
        ledger_genesis_kind=NATIVE_GENESIS,
        event_hash_schema_version=LEGACY_EVENT_HASH_SCHEMA_VERSION,
    )

    schema_version, observed = migration[
        "_ledger_fields_for_existing_event"
    ](
        run_id="postgres-v2-run",
        event_sequence=1,
        ts=101,
        source="test",
        kind="event_msg",
        payload=payload,
        previous_event_hash=None,
        genesis_kind=NATIVE_GENESIS,
        observed_event_hash=expected.event_hash,
    )

    assert schema_version == LEGACY_EVENT_HASH_SCHEMA_VERSION
    assert observed == expected


@pytest.mark.parametrize(
    ("false_accept_count", "denominator", "rate"),
    (
        (1, 3, 0.5),
        (0, 0, 0.1),
    ),
)
def test_quality_audit_rate_must_equal_derived_value(
    tmp_path,
    false_accept_count,
    denominator,
    rate,
):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    run_forward_migrations(conn)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO quality_trend_audits(
                 run_id, gate, sample_size, false_accept_count,
                 false_accept_denominator, false_accept_rate,
                 audit_details_json, computed_at)
               VALUES('run', 'gate', 3, ?, ?, ?, '{}', 1)""",
            (false_accept_count, denominator, rate),
        )


def test_insert_or_replace_cannot_replace_an_immutable_quality_audit(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state._conn.execute(
        """INSERT INTO quality_trend_audits(
             run_id, gate, sample_size, false_accept_count,
             false_accept_denominator, false_accept_rate,
             audit_details_json, computed_at)
           VALUES('run', 'gate', 2, 1, 2, 0.5, '{"version":1}', 100)"""
    )
    state._conn.commit()
    state._conn.execute("PRAGMA recursive_triggers=OFF")

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        state._conn.execute(
            """INSERT OR REPLACE INTO quality_trend_audits(
                 run_id, gate, sample_size, false_accept_count,
                 false_accept_denominator, false_accept_rate,
                 audit_details_json, computed_at)
               VALUES(
                 'run', 'gate', 4, 0, 4, 0.0, '{"version":2}', 100
               )"""
        )

    row = state._conn.execute(
        """SELECT sample_size, false_accept_count, audit_details_json
             FROM quality_trend_audits
            WHERE run_id='run' AND gate='gate' AND computed_at=100"""
    ).fetchone()
    assert dict(row) == {
        "sample_size": 2,
        "false_accept_count": 1,
        "audit_details_json": '{"version":1}',
    }


def test_recorded_partial_audit_schema_fails_on_inexact_legacy_rate(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE quality_trend_audits (
             run_id TEXT NOT NULL,
             gate TEXT NOT NULL,
             sample_size INTEGER NOT NULL,
             false_accept_count INTEGER NOT NULL,
             false_accept_denominator INTEGER NOT NULL,
             false_accept_rate REAL NOT NULL,
             audit_details_json TEXT NOT NULL DEFAULT '{}',
             computed_at INTEGER NOT NULL,
             PRIMARY KEY(run_id, gate, computed_at)
           )"""
    )
    conn.execute(
        """INSERT INTO quality_trend_audits(
             run_id, gate, sample_size, false_accept_count,
             false_accept_denominator, false_accept_rate,
             audit_details_json, computed_at)
           VALUES('run', 'gate', 3, 1, 3, 0.5, '{}', 1)"""
    )
    conn.execute(
        """CREATE TABLE schema_migrations (
             version INTEGER PRIMARY KEY,
             name TEXT NOT NULL,
             applied_at INTEGER NOT NULL
           )"""
    )
    conn.executemany(
        """INSERT INTO schema_migrations(version, name, applied_at)
           VALUES(?, ?, 1)""",
        (
            (migration["version"], migration["name"])
            for migration in EXPECTED_MIGRATIONS
        ),
    )
    conn.commit()

    with pytest.raises(
        RuntimeError,
        match="invalid legacy quality audit counts or derived rate",
    ):
        run_forward_migrations(conn)


def test_recorded_migrations_self_heal_event_and_audit_triggers(tmp_path):
    state = State(str(tmp_path / "state.db"))
    event_id = state.write_event(
        run_id="run",
        source="test",
        kind="event_msg",
        payload={"ok": True},
    )
    state._conn.execute(
        """INSERT INTO quality_trend_audits(
             run_id, gate, sample_size, false_accept_count,
             false_accept_denominator, false_accept_rate,
             audit_details_json, computed_at)
           VALUES('run', 'gate', 2, 1, 2, 0.5, '{}', 1)"""
    )
    for trigger_name in (
        "events_no_replace",
        "events_no_update",
        "events_no_delete",
        "quality_trend_audits_no_replace",
        "quality_trend_audits_validate_insert",
        "quality_trend_audits_no_update",
        "quality_trend_audits_no_delete",
        "dual_agent_workflow_jobs_terminal_no_delete",
    ):
        state._conn.execute(f"DROP TRIGGER {trigger_name}")
    state._conn.commit()

    run_forward_migrations(state._conn)

    trigger_names = {
        row["name"]
        for row in state._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    }
    assert {
        "events_no_replace",
        "events_no_update",
        "events_no_delete",
        "quality_trend_audits_no_replace",
        "quality_trend_audits_validate_insert",
        "quality_trend_audits_no_update",
        "quality_trend_audits_no_delete",
        "dual_agent_workflow_jobs_terminal_no_delete",
    } <= trigger_names
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        state._conn.execute(
            "UPDATE events SET source='tampered' WHERE event_id=?",
            (event_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        state._conn.execute(
            """DELETE FROM quality_trend_audits
                WHERE run_id='run' AND gate='gate'"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        state._conn.execute(
            """INSERT INTO quality_trend_audits(
                 run_id, gate, sample_size, false_accept_count,
                 false_accept_denominator, false_accept_rate,
                 audit_details_json, computed_at)
               VALUES('run-2', 'gate', 2, 1, 2, 0.25, '{}', 2)"""
        )


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
    assert applied_migrations(conn) == EXPECTED_MIGRATIONS


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
             worker_pgid INTEGER,
             worker_started_at REAL,
             worker_containment_id TEXT,
             worker_reaped_at INTEGER,
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


def test_startup_does_not_rerun_event_idempotency_backfill(tmp_path):
    db_path = tmp_path / "state.db"
    state = State(str(db_path))
    state.write_event_once(
        run_id="claimed-trace-run",
        source="dual_agent",
        kind="dual_agent_production_trace_recorded",
        payload={
            "source_event_hash": "a" * 64,
            "receipt": {"status": "recorded"},
        },
        idempotency_key="production-trace:" + ("a" * 64),
    )
    state.write_event(
        run_id="legacy-trace-run",
        source="dual_agent",
        kind="dual_agent_production_trace_recorded",
        payload={"receipt": {"status": "recorded"}},
    )
    state._conn.close()

    reopened = State(str(db_path))

    assert reopened._conn.execute(
        "SELECT COUNT(*) FROM event_idempotency_claims"
    ).fetchone()[0] == 1
    with pytest.raises(
        sqlite3.IntegrityError,
        match="event idempotency claims are immutable",
    ):
        reopened._conn.execute("DELETE FROM event_idempotency_claims")


def test_versioned_idempotency_migration_still_backfills_and_validates(
    tmp_path,
):
    db_path = tmp_path / "state.db"
    state = State(str(db_path))
    state.write_event(
        run_id="backfill-run",
        source="dual_agent",
        kind="dual_agent_production_trace_recorded",
        payload={
            "source_event_hash": "a" * 64,
            "receipt": {"status": "recorded"},
        },
    )
    conn = state._conn
    conn.execute(
        "DELETE FROM schema_migrations WHERE name='event_idempotency_claims'"
    )
    conn.commit()
    state._conn.close()

    reopened = State(str(db_path))

    claim = reopened._conn.execute(
        """SELECT run_id, kind, idempotency_key, event_id
             FROM event_idempotency_claims"""
    ).fetchone()
    assert claim is not None
    assert claim["run_id"] == "backfill-run"
    assert claim["idempotency_key"] == "production-trace:" + ("a" * 64)


def test_postgres_migrations_preserve_forward_only_integrity_contracts():
    evidence_migration = Path(
        "migrations/versions/20260712_0001_evidence_ledger.py"
    ).read_text(encoding="utf-8")
    identity_migration = Path(
        "migrations/versions/20260712_0002_workflow_process_identity.py"
    ).read_text(encoding="utf-8")
    historical_migration = Path(
        "migrations/versions/20260712_0003_historical_operation_claims.py"
    ).read_text(encoding="utf-8")
    idempotency_migration = Path(
        "migrations/versions/20260712_0004_event_idempotency_claims.py"
    ).read_text(encoding="utf-8")
    spawn_ownership_migration = Path(
        "migrations/versions/20260715_0001_workflow_spawn_ownership.py"
    ).read_text(encoding="utf-8")
    historical_ownership_migration = Path(
        "migrations/versions/"
        "20260715_0002_historical_execution_ownership.py"
    ).read_text(encoding="utf-8")
    normalized_spawn_ownership_migration = " ".join(
        spawn_ownership_migration.split()
    )

    assert "LOCK TABLE events IN SHARE ROW EXCLUSIVE MODE" in evidence_migration
    assert "payload_json=CAST(:payload_json AS jsonb)" not in evidence_migration
    assert "refusing to rewrite historical evidence" in evidence_migration
    assert (
        "conflicting pre-populated event ledger metadata"
        in evidence_migration
    )
    assert (
        "conflicting immutable quality audit history"
        in evidence_migration
    )
    assert (
        "ON CONFLICT(run_id, gate, computed_at) DO NOTHING"
        not in evidence_migration
    )
    assert "quality_trend_audits_rate_exact" in evidence_migration
    assert "QUALITY_TREND_PROJECTION_EVENT" in evidence_migration
    assert "forward-only migration" in evidence_migration

    assert "process-identity migration requires quiescence" in identity_migration
    assert "worker_containment_id TEXT" in identity_migration
    assert "quality_trend_audits_rate_exact" in identity_migration
    assert "DROP TRIGGER IF EXISTS events_no_update" in identity_migration
    assert "DROP TRIGGER IF EXISTS events_no_delete" in identity_migration
    assert "quality_trend_audits_no_update" in identity_migration
    assert "quality_trend_audits_no_delete" in identity_migration
    assert "NEW.worker_containment_id" in identity_migration
    assert "NEW.worker_reaped_at" in identity_migration
    assert "reject_worker_reaped_at_rewrite" in identity_migration
    assert "forward-only migration" in identity_migration

    assert "historical_operation_claims" in historical_migration
    assert "operation_id TEXT PRIMARY KEY" in historical_migration
    assert "forward-only migration" in historical_migration
    assert "event_idempotency_claims" in idempotency_migration
    assert "production-trace:" in idempotency_migration
    assert "duplicate production trace events" in idempotency_migration
    assert "forward-only migration" in idempotency_migration
    assert "worker_prepared_at DOUBLE PRECISION" in (
        normalized_spawn_ownership_migration
    )
    assert "cleanup_attempts" in normalized_spawn_ownership_migration
    assert "INTEGER NOT NULL DEFAULT 0" in (
        normalized_spawn_ownership_migration
    )
    assert "cleanup_escalated_at BIGINT" in (
        normalized_spawn_ownership_migration
    )
    assert "(pid IS NULL OR worker_reaped_at IS NOT NULL)" in (
        normalized_spawn_ownership_migration
    )
    assert "NEW.worker_prepared_at" in spawn_ownership_migration
    assert "NEW.cleanup_attempts" in spawn_ownership_migration
    assert "reject_worker_reaped_at_rewrite" in spawn_ownership_migration
    assert "forward-only migration" in spawn_ownership_migration
    assert (
        "LOCK TABLE historical_operation_claims "
        "IN SHARE ROW EXCLUSIVE MODE"
        in historical_ownership_migration
    )
    assert (
        "historical execution-ownership migration requires quiescence"
        in historical_ownership_migration
    )
    assert historical_ownership_migration.index(
        "LOCK TABLE historical_operation_claims"
    ) < historical_ownership_migration.index(
        "SELECT operation_id"
    ) < historical_ownership_migration.index(
        "ADD COLUMN IF NOT EXISTS execution_owner_token"
    )
    assert "execution_owner_token TEXT" in historical_ownership_migration
    assert "execution_generation" in historical_ownership_migration
    assert "INTEGER NOT NULL DEFAULT 0" in historical_ownership_migration
    assert (
        "execution_heartbeat_at DOUBLE PRECISION"
        in historical_ownership_migration
    )
    assert (
        "idx_historical_operation_claims_execution_owner"
        in historical_ownership_migration
    )
    assert (
        "WHERE execution_owner_token IS NOT NULL"
        in historical_ownership_migration
    )
    assert "forward-only migration" in historical_ownership_migration

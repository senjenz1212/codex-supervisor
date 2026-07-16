from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from supervisor.postgres_state import (
    POSTGRES_CLAIM_AVAILABLE_JOBS_SQL,
    POSTGRES_CLAIM_WORKFLOW_JOB_FOR_REAP_SQL,
    POSTGRES_ALEMBIC_HEAD,
    POSTGRES_EVENT_IMMUTABILITY_SQL,
    POSTGRES_LOCK_ORDER,
    POSTGRES_SCHEMA_SQL,
    PostgresState,
)
from supervisor.dual_agent_workflow import workflow_resume_prompt
from supervisor.evidence_committer import (
    EVIDENCE_COMMIT_EVENT_KIND,
    EvidenceCommitter,
    HmacCheckpointAuthority,
)
from supervisor.ledger_checkpoints import (
    CheckpointPersistenceError,
    FilesystemTrustedCheckpointPinStore,
    LedgerCheckpointStore,
    checkpoint_identity,
)
from supervisor.quality_trends import query_quality_trends, record_quality_trends_for_run, record_transport_incident
from supervisor.state import (
    HISTORICAL_OPERATION_EVENT_SOURCE,
    State,
    is_postgres_state_dsn,
)


def test_state_uses_sqlite_for_filesystem_paths(tmp_path):
    state = State(str(tmp_path / "state.db"))

    assert type(state) is State
    assert state.write_event(
        run_id="sqlite-run",
        source="test",
        kind="event_msg",
        payload={"ok": True},
    ) == 1


def test_state_postgres_url_routes_to_postgres_lane(monkeypatch):
    def fake_init(self, dsn, *args, **kwargs):
        self.dsn = dsn

    monkeypatch.setattr(PostgresState, "__init__", fake_init)

    state = State("postgresql://localhost/codex_supervisor")

    assert isinstance(state, PostgresState)
    assert state.dsn == "postgresql://localhost/codex_supervisor"
    assert is_postgres_state_dsn("postgres://localhost/codex_supervisor")
    assert is_postgres_state_dsn("postgresql://localhost/codex_supervisor")


def test_state_forwards_checkpoint_coordinator_to_postgres_lane(monkeypatch):
    captured = {}
    coordinator = object()

    def fake_init(self, dsn, *args, **kwargs):
        self.dsn = dsn
        captured.update(kwargs)

    monkeypatch.setattr(PostgresState, "__init__", fake_init)

    state = State(
        "postgresql://localhost/codex_supervisor",
        ledger_checkpoint_coordinator=coordinator,
    )

    assert isinstance(state, PostgresState)
    assert captured["ledger_checkpoint_coordinator"] is coordinator


def test_postgres_ledger_verification_matches_sqlite_assurance_levels(tmp_path):
    sqlite = State(str(tmp_path / "state.db"))
    for index in range(2):
        sqlite.write_event(
            run_id="parity-run",
            source="test",
            kind="event_msg",
            payload={"index": index},
            ts=100 + index,
        )
    events = sqlite.read_events_since(
        "parity-run",
        after_event_id=0,
        limit=10,
    )

    class _Result:
        def fetchall(self):
            return events

    class _Connection:
        def execute(self, _statement, _params=None):
            return _Result()

    postgres = PostgresState.__new__(PostgresState)
    postgres._conn = _Connection()
    authority = HmacCheckpointAuthority(
        key_id="parity-key",
        key=b"parity-checkpoint-key",
    )
    checkpoints = LedgerCheckpointStore(tmp_path / "checkpoints")
    pins = FilesystemTrustedCheckpointPinStore(tmp_path / "trusted-pins")
    persisted = sqlite.checkpoint_event_ledger(
        "parity-run",
        checkpoint_store=checkpoints,
        signer=authority,
        verifier=authority,
        created_at=1234,
    )
    pins.pin(checkpoint_identity(persisted.checkpoint))
    trusted_head = pins.latest("parity-run")

    sqlite_structural = sqlite.verify_event_ledger_structure("parity-run")
    postgres_structural = postgres.verify_event_ledger_structure("parity-run")
    sqlite_release = sqlite.verify_event_ledger("parity-run")
    postgres_release = postgres.verify_event_ledger("parity-run")
    sqlite_authoritative = sqlite.verify_event_ledger(
        "parity-run",
        checkpoint_store=checkpoints,
        verifier=authority,
        trusted_latest_checkpoint=trusted_head,
    )
    postgres_authoritative = postgres.verify_event_ledger(
        "parity-run",
        checkpoint_store=checkpoints,
        verifier=authority,
        trusted_latest_checkpoint=trusted_head,
    )

    assert postgres_structural.to_dict() == sqlite_structural.to_dict()
    assert postgres_release.to_dict() == sqlite_release.to_dict()
    assert postgres_authoritative.to_dict() == sqlite_authoritative.to_dict()
    assert postgres_structural.valid is True
    assert postgres_release.valid is False
    assert postgres_release.failure_code == "trusted_head_required"
    assert postgres_authoritative.valid is True
    assert postgres_authoritative.authoritative_head_verified is True

    events = events[:-1]
    postgres_truncated = postgres.verify_event_ledger(
        "parity-run",
        checkpoint_store=checkpoints,
        verifier=authority,
        trusted_latest_checkpoint=trusted_head,
    )
    assert postgres_truncated.valid is False
    assert postgres_truncated.failure_code == "checkpoint_event_count_mismatch"
    assert postgres_truncated.authoritative_head_verified is False


def test_postgres_claim_sql_uses_fenced_skip_locked_cte():
    normalized = re.sub(r"\s+", " ", POSTGRES_CLAIM_AVAILABLE_JOBS_SQL.strip())

    assert normalized.startswith("WITH c AS MATERIALIZED ( SELECT id")
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert f"ORDER BY {POSTGRES_LOCK_ORDER}" in normalized
    assert f"ORDER BY {POSTGRES_LOCK_ORDER} LIMIT %(limit)s FOR UPDATE SKIP LOCKED" in normalized
    assert normalized.index("LIMIT %(limit)s") < normalized.index(") UPDATE")
    assert "WHERE j.id = c.id" in normalized
    assert "(pid IS NULL OR worker_reaped_at IS NOT NULL)" in normalized


def test_postgres_reap_claim_sql_is_full_snapshot_cas():
    normalized = re.sub(
        r"\s+",
        " ",
        POSTGRES_CLAIM_WORKFLOW_JOB_FOR_REAP_SQL.strip(),
    )

    assert normalized.startswith(
        "UPDATE dual_agent_workflow_jobs AS j SET leased_by = %(reaper_id)s"
    )
    for comparison in (
        "j.leased_by IS NOT DISTINCT FROM %(expected_leased_by)s",
        "j.lease_expires_at IS NOT DISTINCT FROM %(expected_lease_expires_at)s",
        "j.heartbeat_at IS NOT DISTINCT FROM %(expected_heartbeat_at)s",
        "j.pid IS NOT DISTINCT FROM %(expected_pid)s",
        "j.worker_pgid IS NOT DISTINCT FROM %(expected_worker_pgid)s",
        "j.worker_started_at IS NOT DISTINCT FROM %(expected_worker_started_at)s",
        "j.worker_containment_id IS NOT DISTINCT FROM %(expected_worker_containment_id)s",
    ):
        assert comparison in normalized
    assert "j.recovery_point IN ('spawn_prepared', 'spawned')" in normalized
    assert "j.status = 'running'" in normalized
    assert "j.terminal_outcome_json IS NULL" in normalized
    assert "j.worker_reaped_at IS NULL" in normalized
    assert normalized.endswith("RETURNING j.*")

    class _Result:
        def fetchone(self):
            return {"job_id": "job", "leased_by": "reaper:dispatcher"}

    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Connection:
        def __init__(self):
            self.statement = ""
            self.params = {}

        def transaction(self):
            return _Transaction()

        def execute(self, statement, params=None):
            self.statement = str(statement)
            self.params = dict(params or {})
            return _Result()

    connection = _Connection()
    state = PostgresState.__new__(PostgresState)
    state._conn = connection
    state._write_lock = threading.RLock()

    claimed = state.claim_dual_agent_workflow_job_for_reap(
        job_id="job",
        reaper_id="reaper:dispatcher",
        lease_ttl_s=60,
        now=1000,
        expected_leased_by="worker:41010",
        expected_lease_expires_at=999,
        expected_heartbeat_at=998,
        expected_pid=41010,
        expected_worker_pgid=41011,
        expected_worker_started_at=123.5,
        expected_worker_containment_id="containment-1",
    )

    assert claimed == {"job_id": "job", "leased_by": "reaper:dispatcher"}
    assert connection.statement == POSTGRES_CLAIM_WORKFLOW_JOB_FOR_REAP_SQL
    assert connection.params == {
        "job_id": "job",
        "reaper_id": "reaper:dispatcher",
        "claim_expires_at": 1060,
        "now": 1000,
        "expected_leased_by": "worker:41010",
        "expected_lease_expires_at": 999,
        "expected_heartbeat_at": 998,
        "expected_pid": 41010,
        "expected_worker_pgid": 41011,
        "expected_worker_started_at": 123.5,
        "expected_worker_containment_id": "containment-1",
    }


def test_postgres_reconcile_replays_only_events_beyond_trusted_head():
    events_by_run = {
        "run-a": [
            (1, 1, "event_msg"),
            (2, 2, "event_msg"),
            (3, 3, "run.completed"),
        ],
        "run-b": [(4, 1, "event_msg")],
    }

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            normalized = " ".join(str(statement).split())
            if normalized.startswith("SELECT DISTINCT run_id"):
                return _Result([{"run_id": run} for run in events_by_run])
            assert "WHERE run_id=%s AND event_sequence>%s" in normalized
            run_id, trusted_count = params
            return _Result(
                [
                    {
                        "event_id": event_id,
                        "event_sequence": sequence,
                        "kind": kind,
                    }
                    for event_id, sequence, kind in events_by_run[run_id]
                    if sequence > trusted_count
                ]
            )

    class _Pins:
        def latest(self, run_id):
            return {"event_count": 2} if run_id == "run-a" else None

    class _Coordinator:
        trusted_pin_store = _Pins()

        def __init__(self):
            self.calls = []

        def coordinate_event(
            self,
            *,
            run_id,
            event_id,
            event_count,
            event_kind,
            events_loader,
        ):
            self.calls.append((run_id, event_id, event_count, event_kind))

    state = PostgresState.__new__(PostgresState)
    state._conn = _Connection()
    state._write_lock = threading.RLock()
    coordinator = _Coordinator()
    state._ledger_checkpoint_coordinator = coordinator

    assert state.reconcile_event_checkpoints() == 2
    assert coordinator.calls == [
        ("run-a", 3, 3, "run.completed"),
        ("run-b", 4, 1, "event_msg"),
    ]


def test_postgres_schema_carries_idempotency_and_partitioned_catch_up():
    assert "event_stream_sequences" in POSTGRES_SCHEMA_SQL
    assert "event_sequence BIGINT NOT NULL" in POSTGRES_SCHEMA_SQL
    assert "previous_event_id BIGINT" in POSTGRES_SCHEMA_SQL
    assert "event_hash TEXT NOT NULL" in POSTGRES_SCHEMA_SQL
    assert "event_idempotency_claims" in POSTGRES_SCHEMA_SQL
    assert "PRIMARY KEY(run_id, kind, idempotency_key)" in POSTGRES_SCHEMA_SQL
    assert (
        "enforce_event_idempotency_claim_immutability"
        in POSTGRES_SCHEMA_SQL
    )
    assert (
        "BEFORE UPDATE OR DELETE ON event_idempotency_claims"
        in POSTGRES_SCHEMA_SQL
    )
    assert (
        "OLD.event_id IS NULL AND NEW.event_id IS NOT NULL"
        in POSTGRES_SCHEMA_SQL
    )
    assert "event.run_id = NEW.run_id" in POSTGRES_SCHEMA_SQL
    assert "event.event_id = NEW.event_id" in POSTGRES_SCHEMA_SQL
    assert "event.kind = NEW.kind" in POSTGRES_SCHEMA_SQL
    assert "event.source = NEW.source" in POSTGRES_SCHEMA_SQL
    assert (
        "event.canonical_payload_hash = NEW.payload_sha256"
        in POSTGRES_SCHEMA_SQL
    )
    assert (
        "BEFORE TRUNCATE ON event_idempotency_claims"
        in POSTGRES_SCHEMA_SQL
    )
    assert "CONSTRAINT events_run_event_unique UNIQUE(run_id, event_id)" in POSTGRES_SCHEMA_SQL
    assert "CONSTRAINT events_run_sequence_unique UNIQUE(run_id, event_sequence)" in POSTGRES_SCHEMA_SQL
    assert "quality_trend_audits_counts_valid" in POSTGRES_SCHEMA_SQL
    assert "quality_trend_audits_rate_exact" in POSTGRES_SCHEMA_SQL
    assert "dual_agent_workflows" in POSTGRES_SCHEMA_SQL
    assert "dual_agent_workflow_steps" in POSTGRES_SCHEMA_SQL
    assert "UNIQUE(run_id, task_id, gate)" in POSTGRES_SCHEMA_SQL
    assert "idx_dual_agent_workflow_jobs_active_idempotency_token" in POSTGRES_SCHEMA_SQL
    assert "WHERE idempotency_token IS NOT NULL AND recovery_point != 'terminal'" in POSTGRES_SCHEMA_SQL
    assert "idx_dual_agent_workflow_jobs_dispatchable" in POSTGRES_SCHEMA_SQL
    assert "ON dual_agent_workflow_jobs(priority, created_at, id)" in POSTGRES_SCHEMA_SQL
    assert "(pid IS NULL OR worker_reaped_at IS NOT NULL)" in (
        POSTGRES_SCHEMA_SQL
    )
    assert "execution_owner_token TEXT" in POSTGRES_SCHEMA_SQL
    assert "execution_generation INTEGER NOT NULL DEFAULT 0" in (
        POSTGRES_SCHEMA_SQL
    )
    assert "execution_heartbeat_at DOUBLE PRECISION" in POSTGRES_SCHEMA_SQL
    assert "idx_historical_operation_claims_execution_owner" in (
        POSTGRES_SCHEMA_SQL
    )
    assert "WHERE execution_owner_token IS NOT NULL" in POSTGRES_SCHEMA_SQL


def test_postgres_historical_reservation_does_not_read_terminal_variables():
    row = {
        "operation_id": "historical-reserve",
        "request_hash": "a" * 64,
        "operation": "replay",
        "status": "running",
        "terminal_event_id": None,
        "created_at": 1,
        "updated_at": 1,
    }

    class _Result:
        def __init__(self, value):
            self._value = value

        def fetchone(self):
            return self._value

    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Connection:
        def __init__(self):
            self.statements = []

        def transaction(self):
            return _Transaction()

        def execute(self, statement, _params=None):
            text = str(statement)
            self.statements.append(text)
            if "clock_timestamp()" in text:
                return _Result({"state_now": 1})
            return _Result(row)

    connection = _Connection()
    state = PostgresState.__new__(PostgresState)
    state._conn = connection
    state._write_lock = threading.RLock()

    claimed, reserved = state.reserve_historical_operation(
        operation_id=row["operation_id"],
        request_hash=row["request_hash"],
        operation=row["operation"],
    )

    assert reserved is True
    assert claimed == row
    assert len(connection.statements) == 2
    assert "clock_timestamp()" in connection.statements[0]
    assert "INSERT INTO historical_operation_claims" in connection.statements[1]


def test_postgres_historical_completion_validates_source_and_request_link():
    operation_id = "historical-complete"
    request_hash = "b" * 64
    requested_event_id = 1
    terminal_event_id = 2

    class _Result:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Connection:
        def transaction(self):
            return _Transaction()

        def execute(self, statement, _params=None):
            sql = " ".join(str(statement).split())
            if "FROM historical_operation_claims" in sql:
                return _Result(
                    {
                        "request_hash": request_hash,
                        "operation": "replay",
                    }
                )
            if "FROM events" in sql and "event_id=%s" in sql:
                event_id = int((_params or (None, 0))[1])
                if event_id == terminal_event_id:
                    return _Result(
                        {
                            "source": "untrusted_component",
                            "kind": "historical_operation.completed",
                            "payload_json": {
                                "operation_id": operation_id,
                                "operation": "replay",
                                "request_hash": request_hash,
                                "requested_event_id": requested_event_id,
                            },
                        }
                    )
            raise AssertionError(f"unexpected SQL: {sql}")

    state = PostgresState.__new__(PostgresState)
    state._conn = _Connection()
    state._write_lock = threading.RLock()

    with pytest.raises(
        RuntimeError,
        match="requires its matching ledger event",
    ):
        state.complete_historical_operation(
            operation_id=operation_id,
            request_hash=request_hash,
            status="completed",
            terminal_event_id=terminal_event_id,
        )


def test_alembic_migration_and_make_target_exist():
    base_migration = Path("migrations/versions/20260604_0001_postgres_event_job_lane.py").read_text(
        encoding="utf-8"
    )
    lessons_migration = Path("migrations/versions/20260610_0001_supervisor_lessons.py").read_text(
        encoding="utf-8"
    )
    trends_migration = Path("migrations/versions/20260610_0002_supervisor_quality_trends.py").read_text(
        encoding="utf-8"
    )
    queue_migration = Path("migrations/versions/20260610_0003_autoresearch_experiment_queue.py").read_text(
        encoding="utf-8"
    )
    overlay_migration = Path("migrations/versions/20260610_0004_policy_overlay_trend_columns.py").read_text(
        encoding="utf-8"
    )
    historical_ownership_migration = Path(
        "migrations/versions/"
        "20260715_0002_historical_execution_ownership.py"
    ).read_text(encoding="utf-8")
    makefile = Path("Makefile").read_text(encoding="utf-8")
    config_example = Path("config.example.yaml").read_text(encoding="utf-8")

    assert "event_stream_sequences" in base_migration
    assert "dual_agent_workflows" in base_migration
    assert "dual_agent_workflow_steps" in base_migration
    assert "idx_dual_agent_workflow_jobs_active_idempotency_token" in base_migration
    assert "idx_dual_agent_workflow_jobs_dispatchable" in base_migration
    assert "supervisor_lessons" not in base_migration
    assert 'revision = "20260610_0001"' in lessons_migration
    assert 'down_revision = "20260604_0001"' in lessons_migration
    assert "supervisor_lessons" in lessons_migration
    assert 'revision = "20260610_0002"' in trends_migration
    assert 'down_revision = "20260610_0001"' in trends_migration
    assert "supervisor_quality_trends" in trends_migration
    assert "idx_supervisor_quality_trends_task_gate" in trends_migration
    assert 'revision = "20260610_0003"' in queue_migration
    assert 'down_revision = "20260610_0002"' in queue_migration
    assert "supervisor_autoresearch_experiments" in queue_migration
    assert "idx_supervisor_autoresearch_experiments_status" in queue_migration
    assert 'revision = "20260610_0004"' in overlay_migration
    assert 'down_revision = "20260610_0003"' in overlay_migration
    assert "policy_overlay_hash" in overlay_migration
    assert "retired_at" in overlay_migration
    assert 'revision = "20260715_0002"' in historical_ownership_migration
    assert 'down_revision = "20260715_0001"' in (
        historical_ownership_migration
    )
    assert "execution_owner_token" in historical_ownership_migration
    assert "execution_generation" in historical_ownership_migration
    assert "execution_heartbeat_at" in historical_ownership_migration
    assert (
        "historical execution-ownership migration requires quiescence"
        in historical_ownership_migration
    )
    assert "uv run --extra postgres alembic -c alembic.ini upgrade head" in makefile
    assert "PgBouncer" in config_example
    assert "state_db: ~/.codex-supervisor/state.db" in config_example
    assert "max_runnable_experiments_per_week: 2" in config_example


def test_postgres_conformance_gate_rejects_selection_arguments():
    script = Path("scripts/run_postgres_conformance.sh").read_text(
        encoding="utf-8"
    )
    assert "env -u PYTEST_PLUGINS -u PYTHONPATH" in script
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in script
    assert "PYTHONNOUSERSITE=1" in script
    assert "-p pytest_asyncio.plugin" in script

    completed = subprocess.run(
        [
            "./scripts/run_postgres_conformance.sh",
            "-k",
            "test_state_uses_sqlite_for_filesystem_paths",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "does not accept pytest selection arguments" in completed.stderr


def test_postgres_inline_schema_and_alembic_migration_stay_structurally_equivalent():
    migration = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("migrations/versions").glob("*.py"))
    )
    inline_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", POSTGRES_SCHEMA_SQL))
    migration_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", migration))
    inline_indexes = set(re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS ([a-z_]+)", POSTGRES_SCHEMA_SQL))
    migration_indexes = set(re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS ([a-z_]+)", migration))
    inline = POSTGRES_SCHEMA_SQL + "\n" + POSTGRES_EVENT_IMMUTABILITY_SQL

    assert migration_tables == inline_tables
    assert migration_indexes == inline_indexes
    for required_snippet in (
        "CONSTRAINT events_run_event_unique UNIQUE(run_id, event_id)",
        "events_run_sequence_unique",
        "events_sequence_positive",
        "events_genesis_hash_shape",
        "CONSTRAINT events_previous_id_shape CHECK",
        "event_id > 1 AND previous_event_id = event_id - 1",
        "quality_trend_audits_counts_valid",
        "quality_trend_audits_rate_exact",
        "dual_agent_workflow_jobs_terminal_freeze",
        "dual_agent_workflow_jobs_terminal_no_delete",
        "dual_agent_workflow_jobs_no_truncate",
        "dual_agent_workflow_jobs_worker_reaped_once",
        "quality_trend_audits_no_truncate",
        "UNIQUE(run_id, task_id, gate)",
        "idx_dual_agent_workflow_jobs_active_idempotency_token",
        "WHERE idempotency_token IS NOT NULL AND recovery_point != 'terminal'",
        "idx_dual_agent_workflow_jobs_dispatchable",
        "recovery_point IN ('reserved', 'request_written')",
        "supervisor_lessons",
        "idx_supervisor_lessons_task_gate",
        "supervisor_quality_trends",
        "idx_supervisor_quality_trends_task_gate",
        "policy_overlay_hash TEXT NOT NULL DEFAULT ''",
        "retired_at",
        "supervisor_autoresearch_experiments",
        "idx_supervisor_autoresearch_experiments_status",
        "UNIQUE(run_id, gate)",
        "signal_key TEXT NOT NULL UNIQUE",
        "idx_historical_operation_claims_execution_owner",
        "WHERE execution_owner_token IS NOT NULL",
    ):
        assert required_snippet in inline
        assert required_snippet in migration


def test_postgres_migrations_lock_before_inspection_in_runtime_write_order():
    ledger_migration = Path(
        "migrations/versions/20260712_0001_evidence_ledger.py"
    ).read_text(encoding="utf-8")
    identity_migration = Path(
        "migrations/versions/20260712_0002_workflow_process_identity.py"
    ).read_text(encoding="utf-8")
    historical_ownership_migration = Path(
        "migrations/versions/"
        "20260715_0002_historical_execution_ownership.py"
    ).read_text(encoding="utf-8")

    lock_markers = (
        "LOCK TABLE supervisor_quality_trends IN SHARE MODE",
        "LOCK TABLE quality_trend_audits IN SHARE ROW EXCLUSIVE MODE",
        "LOCK TABLE event_stream_sequences IN SHARE ROW EXCLUSIVE MODE",
        "LOCK TABLE events IN SHARE ROW EXCLUSIVE MODE",
    )
    positions = [ledger_migration.index(marker) for marker in lock_markers]
    assert positions == sorted(positions)
    assert positions[-1] < ledger_migration.index(
        "SELECT global_id, event_id, run_id"
    )

    lock_position = identity_migration.index(
        "LOCK TABLE dual_agent_workflow_jobs IN SHARE ROW EXCLUSIVE MODE"
    )
    quiescence_position = identity_migration.index(
        "SELECT job_id, pid"
    )
    assert lock_position < quiescence_position
    assert "CREATE TRIGGER events_no_truncate" in identity_migration
    assert (
        "CREATE TRIGGER quality_trend_audits_no_truncate"
        in identity_migration
    )
    assert (
        "CREATE TRIGGER dual_agent_workflow_jobs_no_truncate"
        in identity_migration
    )
    historical_lock_position = historical_ownership_migration.index(
        "LOCK TABLE historical_operation_claims"
    )
    historical_quiescence_position = historical_ownership_migration.index(
        "SELECT operation_id"
    )
    historical_alter_position = historical_ownership_migration.index(
        "ADD COLUMN IF NOT EXISTS execution_owner_token"
    )
    assert (
        historical_lock_position
        < historical_quiescence_position
        < historical_alter_position
    )


def test_postgres_startup_refuses_to_rewrite_existing_unmigrated_schema():
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Connection:
        def __init__(self):
            self.statements: list[str] = []

        def transaction(self):
            return _Transaction()

        def execute(self, statement, _params=None):
            text = str(statement)
            self.statements.append(text)
            if "to_regclass('events')" in text:
                return _Result(
                    {
                        "events_table": "events",
                        "alembic_table": None,
                    }
                )
            return _Result()

    connection = _Connection()
    state = PostgresState.__new__(PostgresState)
    state._conn = connection
    state._write_lock = threading.RLock()
    state._Jsonb = lambda value: value

    with pytest.raises(RuntimeError, match="make migrate"):
        state.apply_schema()

    assert POSTGRES_ALEMBIC_HEAD == "20260715_0002"
    assert not any("UPDATE events" in sql for sql in connection.statements)
    assert not any("DROP TRIGGER" in sql for sql in connection.statements)


def test_alembic_lessons_revision_upgrades_from_applied_base(monkeypatch):
    dsn = os.environ.get("CODEX_SUPERVISOR_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("CODEX_SUPERVISOR_POSTGRES_TEST_DSN is not set")
    psycopg = pytest.importorskip("psycopg")
    command = pytest.importorskip("alembic.command")
    alembic_config = pytest.importorskip("alembic.config")

    schema = f"cs_migrate_{uuid.uuid4().hex}"
    dsn_with_schema = _dsn_with_search_path(dsn, schema)
    with psycopg.connect(dsn) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.commit()
    try:
        cfg = alembic_config.Config("alembic.ini")
        monkeypatch.setenv("DATABASE_URL", dsn_with_schema)
        monkeypatch.delenv("POSTGRES_DSN", raising=False)

        command.upgrade(cfg, "20260604_0001")
        with psycopg.connect(dsn_with_schema) as conn:
            row = conn.execute("SELECT to_regclass('supervisor_lessons') AS table_name").fetchone()
            assert row[0] is None

        command.upgrade(cfg, "20260610_0004")
        with psycopg.connect(dsn_with_schema) as conn:
            conn.execute(
                """INSERT INTO supervisor_quality_trends(
                     run_id, task_id, task_class, gate, accepted,
                     first_pass_accepted, revision_rounds,
                     time_to_accepted_outcome_s, p11_audit_sample_size,
                     false_accept_count, false_accept_denominator,
                     false_accept_rate, details_json, computed_at)
                   VALUES(
                     'legacy-audit-run', 'legacy-task', 'source_change',
                     'outcome_review', TRUE, TRUE, 0, 1.0,
                     3, 1, 3, 0.3333333333333333,
                     '{"p11_audit":{"source":"legacy"}}'::jsonb, 123
                   )"""
            )
            conn.execute(
                """INSERT INTO events(
                     run_id, event_id, previous_event_id, ts,
                     source, kind, payload_json)
                   VALUES
                     ('legacy-run-a', 1, NULL, 101, 'test', 'event_msg',
                      '{"index":1}'::jsonb),
                     ('legacy-run-b', 1, NULL, 102, 'test', 'event_msg',
                      '{"index":1}'::jsonb),
                     ('legacy-run-a', 2, 1, 103, 'test', 'event_msg',
                      '{"index":2}'::jsonb)"""
            )
            conn.execute(
                """INSERT INTO event_stream_sequences(run_id, last_event_id)
                   VALUES('legacy-run-a', 2), ('legacy-run-b', 1)"""
            )
            conn.commit()

        command.upgrade(cfg, "20260715_0001")
        with psycopg.connect(dsn_with_schema) as conn:
            conn.execute(
                """INSERT INTO historical_operation_claims(
                     operation_id, request_hash, operation, status,
                     terminal_event_id, created_at, updated_at)
                   VALUES(
                     'live-historical-migration-claim', %s, 'replay',
                     'running', NULL, 1, 1
                   )""",
                ("a" * 64,),
            )
            conn.commit()

        with pytest.raises(
            RuntimeError,
            match="execution-ownership migration requires quiescence",
        ):
            command.upgrade(cfg, "head")

        with psycopg.connect(dsn_with_schema) as conn:
            assert conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "20260715_0001"
            assert conn.execute(
                """SELECT column_name
                     FROM information_schema.columns
                    WHERE table_schema=current_schema()
                      AND table_name='historical_operation_claims'
                      AND column_name='execution_owner_token'"""
            ).fetchone() is None
            conn.execute(
                """UPDATE historical_operation_claims
                      SET status='failed'
                    WHERE operation_id='live-historical-migration-claim'"""
            )
            conn.commit()

        command.upgrade(cfg, "head")
        with psycopg.connect(dsn_with_schema) as conn:
            row = conn.execute("SELECT to_regclass('supervisor_lessons') AS table_name").fetchone()
            assert row[0] == "supervisor_lessons"
            row = conn.execute("SELECT to_regclass('supervisor_quality_trends') AS table_name").fetchone()
            assert row[0] == "supervisor_quality_trends"
            audit = conn.execute(
                """SELECT sample_size, false_accept_count,
                          false_accept_denominator, false_accept_rate,
                          audit_details_json, computed_at
                     FROM quality_trend_audits
                    WHERE run_id='legacy-audit-run'
                      AND gate='outcome_review'"""
            ).fetchone()
            assert audit[0:3] == (3, 1, 3)
            assert audit[3] == pytest.approx(1 / 3)
            assert audit[4] == {"source": "legacy"}
            assert audit[5] == 123
        migrated = PostgresState(dsn_with_schema, apply_schema=False)
        try:
            run_a = migrated.read_events_since(
                "legacy-run-a",
                after_event_id=0,
                limit=10,
            )
            run_b = migrated.read_events_since(
                "legacy-run-b",
                after_event_id=0,
                limit=10,
            )
            assert [event["event_sequence"] for event in run_a] == [1, 2]
            assert [event["event_sequence"] for event in run_b] == [1]
            assert run_a[0]["ledger_genesis_kind"] == "legacy-import"
            assert run_b[0]["ledger_genesis_kind"] == "legacy-import"
            assert (
                migrated.verify_event_ledger_structure("legacy-run-a").valid
                is True
            )
            assert (
                migrated.verify_event_ledger_structure("legacy-run-b").valid
                is True
            )
        finally:
            migrated.close()
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()


def test_alembic_ledger_migration_rejects_forged_prepopulated_hashes(
    monkeypatch,
):
    dsn = os.environ.get("CODEX_SUPERVISOR_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("CODEX_SUPERVISOR_POSTGRES_TEST_DSN is not set")
    psycopg = pytest.importorskip("psycopg")
    command = pytest.importorskip("alembic.command")
    alembic_config = pytest.importorskip("alembic.config")

    schema = f"cs_forged_ledger_{uuid.uuid4().hex}"
    dsn_with_schema = _dsn_with_search_path(dsn, schema)
    with psycopg.connect(dsn) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.commit()
    try:
        cfg = alembic_config.Config("alembic.ini")
        monkeypatch.setenv("DATABASE_URL", dsn_with_schema)
        monkeypatch.delenv("POSTGRES_DSN", raising=False)
        command.upgrade(cfg, "20260610_0004")

        forged_hash = "f" * 64
        with psycopg.connect(dsn_with_schema) as conn:
            for statement in (
                "ALTER TABLE events ADD COLUMN event_sequence BIGINT",
                "ALTER TABLE events ADD COLUMN previous_event_hash TEXT",
                "ALTER TABLE events ADD COLUMN event_hash TEXT",
                "ALTER TABLE events ADD COLUMN canonical_payload_hash TEXT",
                "ALTER TABLE events ADD COLUMN artifact_manifest_hash TEXT",
                "ALTER TABLE events ADD COLUMN ledger_genesis_kind TEXT",
            ):
                conn.execute(statement)
            conn.execute(
                """INSERT INTO events(
                     run_id, event_id, previous_event_id, event_sequence,
                     ts, source, kind, payload_json, previous_event_hash,
                     event_hash, canonical_payload_hash,
                     artifact_manifest_hash, ledger_genesis_kind)
                   VALUES(
                     'forged-run', 1, NULL, 1, 101, 'test', 'event_msg',
                     '{"value":1}'::jsonb, NULL, %s, %s, %s,
                     'legacy-import'
                   )""",
                (forged_hash, forged_hash, forged_hash),
            )
            conn.execute(
                """INSERT INTO event_stream_sequences(run_id, last_event_id)
                   VALUES('forged-run', 1)"""
            )
            conn.commit()

        with pytest.raises(
            RuntimeError,
            match="conflicting pre-populated event ledger metadata",
        ):
            command.upgrade(cfg, "20260712_0001")

        with psycopg.connect(dsn_with_schema) as conn:
            assert conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "20260610_0004"
            assert conn.execute(
                "SELECT event_hash FROM events WHERE run_id='forged-run'"
            ).fetchone()[0] == forged_hash
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()


def test_alembic_audit_backfill_rejects_conflicting_immutable_history(
    monkeypatch,
):
    dsn = os.environ.get("CODEX_SUPERVISOR_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("CODEX_SUPERVISOR_POSTGRES_TEST_DSN is not set")
    psycopg = pytest.importorskip("psycopg")
    command = pytest.importorskip("alembic.command")
    alembic_config = pytest.importorskip("alembic.config")

    schema = f"cs_audit_conflict_{uuid.uuid4().hex}"
    dsn_with_schema = _dsn_with_search_path(dsn, schema)
    with psycopg.connect(dsn) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.commit()
    try:
        cfg = alembic_config.Config("alembic.ini")
        monkeypatch.setenv("DATABASE_URL", dsn_with_schema)
        monkeypatch.delenv("POSTGRES_DSN", raising=False)
        command.upgrade(cfg, "20260610_0004")

        with psycopg.connect(dsn_with_schema) as conn:
            conn.execute(
                """INSERT INTO supervisor_quality_trends(
                     run_id, task_id, task_class, gate, accepted,
                     first_pass_accepted, revision_rounds,
                     time_to_accepted_outcome_s, p11_audit_sample_size,
                     false_accept_count, false_accept_denominator,
                     false_accept_rate, details_json, computed_at)
                   VALUES(
                     'audit-run', 'task', 'source_change', 'outcome_review',
                     TRUE, TRUE, 0, 1.0, 3, 1, 3,
                     0.3333333333333333,
                     '{"p11_audit":{"source":"trend"}}'::jsonb, 123
                   )"""
            )
            conn.execute(
                """CREATE TABLE quality_trend_audits (
                     run_id TEXT NOT NULL,
                     gate TEXT NOT NULL,
                     sample_size INTEGER NOT NULL,
                     false_accept_count INTEGER NOT NULL,
                     false_accept_denominator INTEGER NOT NULL,
                     false_accept_rate DOUBLE PRECISION NOT NULL,
                     audit_details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                     computed_at BIGINT NOT NULL,
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
                     '{"source":"existing"}'::jsonb, 123
                   )"""
            )
            conn.commit()

        with pytest.raises(
            RuntimeError,
            match="conflicting immutable quality audit history",
        ):
            command.upgrade(cfg, "20260712_0001")

        with psycopg.connect(dsn_with_schema) as conn:
            assert conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "20260610_0004"
            assert conn.execute(
                """SELECT sample_size, audit_details_json
                     FROM quality_trend_audits
                    WHERE run_id='audit-run'
                      AND gate='outcome_review'
                      AND computed_at=123"""
            ).fetchone() == (4, {"source": "existing"})
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()


def _dsn_with_search_path(dsn: str, schema: str) -> str:
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _postgres_dsn() -> str:
    dsn = os.environ.get("CODEX_SUPERVISOR_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("CODEX_SUPERVISOR_POSTGRES_TEST_DSN is not set")
    pytest.importorskip("psycopg")
    return dsn


@pytest.fixture()
def postgres_state():
    dsn = _postgres_dsn()
    schema = f"cs_lane_{uuid.uuid4().hex}"
    state = PostgresState(dsn, schema=schema)
    try:
        yield state
    finally:
        state.close()
        psycopg = pytest.importorskip("psycopg")
        from psycopg import sql

        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _reserve_job(
    state: PostgresState,
    tmp_path: Path,
    *,
    job_id: str,
    token: str,
) -> tuple[dict, bool]:
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / job_id
    payload = {
        "cwd": str(tmp_path),
        "task_id": "task",
        "run_id": "run",
        "intent": "postgres lane test",
        "tool_receipts": [],
    }
    return state.reserve_dual_agent_workflow_job(
        job_id=job_id,
        run_id="run",
        task_id="task",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        idempotency_token=token,
        request_payload_json=json.dumps(payload, sort_keys=True),
        config_path=str(tmp_path / "config.yaml"),
    )


def _insert_legacy_postgres_historical_event(
    state: PostgresState,
    *,
    run_id: str,
    kind: str,
    payload: dict,
) -> int:
    """Build a controlled legacy fixture without reopening the public bypass."""
    with state._write_lock:
        with state._conn.transaction():
            event_id = state._insert_event_unlocked(
                run_id=run_id,
                source=HISTORICAL_OPERATION_EVENT_SOURCE,
                kind=kind,
                payload=payload,
            )
    state._coordinate_committed_event(
        run_id=run_id,
        event_id=event_id,
        event_kind=kind,
    )
    return event_id


def test_postgres_partitioned_per_run_catch_up(postgres_state):
    a1 = postgres_state.write_event(
        run_id="run-a",
        source="test",
        kind="event_msg",
        payload={"run": "a", "index": 1},
    )
    b1 = postgres_state.write_event(
        run_id="run-b",
        source="test",
        kind="event_msg",
        payload={"run": "b", "index": 1},
    )
    a2 = postgres_state.write_event(
        run_id="run-a",
        source="test",
        kind="event_msg",
        payload={"run": "a", "index": 2},
    )

    assert (a1, b1, a2) == (1, 1, 2)
    assert postgres_state.latest_event_id("run-a") == 2
    assert postgres_state.latest_event_id("run-b") == 1
    assert [
        (
            event["run_id"],
            event["event_id"],
            event["event_sequence"],
            event["previous_event_id"],
            event["payload"]["index"],
        )
        for event in postgres_state.read_events_since("run-a", after_event_id=0, limit=10)
    ] == [
        ("run-a", 1, 1, None, 1),
        ("run-a", 2, 2, 1, 2),
    ]
    assert [
        event["event_id"]
        for event in postgres_state.read_events_since("run-b", after_event_id=0, limit=10)
    ] == [1]


def test_postgres_write_event_once_is_exact(postgres_state):
    kwargs = {
        "run_id": "run-once",
        "source": "dual_agent",
        "kind": "dual_agent_production_trace_recorded",
        "payload": {
            "source_event_hash": "a" * 64,
            "receipt": {"status": "recorded"},
        },
        "idempotency_key": "production-trace:" + ("a" * 64),
    }

    first = postgres_state.write_event_once(**kwargs)
    second = postgres_state.write_event_once(**kwargs)

    assert second == first
    assert len(
        postgres_state.read_events_since(
            "run-once",
            after_event_id=0,
            limit=10,
        )
    ) == 1
    with pytest.raises(
        RuntimeError,
        match="changed source or payload",
    ):
        postgres_state.write_event_once(
            **{
                **kwargs,
                "payload": {
                    "source_event_hash": "a" * 64,
                    "receipt": {"status": "different"},
                },
            }
        )

    with pytest.raises(ValueError, match="reserved evidence-commit event"):
        postgres_state.write_event(
            run_id="postgres-evidence-commit",
            source="evidence_committer",
            kind=EVIDENCE_COMMIT_EVENT_KIND,
            payload={"commit_id": "forged"},
        )
    with pytest.raises(
        PermissionError,
        match="evidence-commit write capability",
    ):
        postgres_state.write_evidence_commit_event(
            run_id="postgres-evidence-commit",
            payload={"commit_id": "forged-dedicated"},
            capability=object(),
        )
    owner = EvidenceCommitter.__new__(EvidenceCommitter)
    capability = postgres_state._bind_evidence_commit_writer(owner)
    event_id = postgres_state.write_evidence_commit_event(
        run_id="postgres-evidence-commit",
        payload={"commit_id": "dedicated"},
        capability=capability,
    )
    assert (
        postgres_state.write_evidence_commit_event(
            run_id="postgres-evidence-commit",
            payload={"commit_id": "dedicated"},
            capability=capability,
        )
        == event_id
    )
    postgres_state.assert_evidence_commit_event_authority(
        run_id="postgres-evidence-commit",
        commit_id="dedicated",
        event_id=event_id,
    )
    with pytest.raises(
        RuntimeError,
        match="changed source or payload",
    ):
        postgres_state.write_evidence_commit_event(
            run_id="postgres-evidence-commit",
            payload={"commit_id": "dedicated", "changed": True},
            capability=capability,
        )
    [event] = postgres_state.read_events_since(
        "postgres-evidence-commit",
        after_event_id=0,
        limit=10,
    )
    assert event["event_id"] == event_id
    assert event["source"] == "evidence_committer"
    assert event["kind"] == EVIDENCE_COMMIT_EVENT_KIND


def test_postgres_event_idempotency_claims_are_immutable(postgres_state):
    postgres_state.write_event_once(
        run_id="postgres-immutable-claim",
        source="dual_agent",
        kind="dual_agent_production_trace_recorded",
        payload={
            "source_event_hash": "d" * 64,
            "receipt": {"status": "recorded"},
        },
        idempotency_key="production-trace:" + ("d" * 64),
    )

    for statement in (
        """UPDATE event_idempotency_claims
              SET source='attacker'
            WHERE run_id='postgres-immutable-claim'""",
        """DELETE FROM event_idempotency_claims
            WHERE run_id='postgres-immutable-claim'""",
        "TRUNCATE event_idempotency_claims",
    ):
        with pytest.raises(
            postgres_state._errors.RaiseException,
            match="event idempotency claims are immutable",
        ):
            with postgres_state._conn.transaction():
                postgres_state._conn.execute(statement)


def test_postgres_write_event_once_retry_coordinates_existing_event_after_failure():
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class _Connection:
        def __init__(self):
            self.claim = None
            self.event = None

        def transaction(self):
            return _Transaction()

        def execute(self, statement, params=None):
            sql = re.sub(r"\s+", " ", str(statement).strip())
            params = tuple(params or ())
            if sql.startswith("INSERT INTO event_idempotency_claims"):
                if self.claim is not None:
                    return _Result()
                self.claim = {
                    "run_id": params[0],
                    "kind": params[1],
                    "idempotency_key": params[2],
                    "event_id": None,
                    "source": params[3],
                    "payload_sha256": params[4],
                }
                return _Result({"idempotency_key": params[2]})
            if sql.startswith("UPDATE event_idempotency_claims"):
                assert self.claim is not None
                self.claim["event_id"] = params[0]
                return _Result({"event_id": params[0]})
            if sql.startswith(
                "SELECT event_id, source, payload_sha256 "
                "FROM event_idempotency_claims"
            ):
                return _Result(dict(self.claim))
            if sql.startswith(
                "SELECT source, canonical_payload_hash FROM events"
            ):
                if (
                    self.event is not None
                    and params
                    == (
                        self.event["run_id"],
                        self.event["event_id"],
                        self.event["kind"],
                    )
                ):
                    return _Result({
                        "source": self.event["source"],
                        "canonical_payload_hash": self.event[
                            "canonical_payload_hash"
                        ],
                    })
                return _Result()
            if sql.startswith("SELECT event_id, event_sequence FROM events"):
                if (
                    self.event is not None
                    and params
                    == (
                        self.event["run_id"],
                        self.event["event_id"],
                    )
                ):
                    return _Result(dict(self.event))
                return _Result()
            raise AssertionError(f"unexpected SQL: {sql}")

    class _FailingCheckpointCoordinator:
        assurance = "authoritative"

        def __init__(self):
            self.attempts = 0
            self.published_event_ids = []

        def coordinate_event(
            self,
            *,
            run_id,
            event_id,
            event_count,
            event_kind,
            events_loader,
        ):
            del run_id, event_kind, events_loader
            self.attempts += 1
            assert event_count == 1
            if self.attempts == 1:
                raise CheckpointPersistenceError(
                    "trusted_pin_persistence"
                )
            self.published_event_ids.append(event_id)

    connection = _Connection()
    coordinator = _FailingCheckpointCoordinator()
    state = PostgresState.__new__(PostgresState)
    state._conn = connection
    state._write_lock = threading.RLock()
    state._ledger_checkpoint_coordinator = coordinator
    insert_calls = 0

    def insert_event_unlocked(
        *, run_id, source, kind, payload, ts, prepared_payload=None
    ):
        nonlocal insert_calls
        assert prepared_payload is not None
        del payload, ts, prepared_payload
        insert_calls += 1
        assert connection.event is None
        assert connection.claim is not None
        connection.event = {
            "run_id": run_id,
            "event_id": 1,
            "event_sequence": 1,
            "kind": kind,
            "source": source,
            "canonical_payload_hash": connection.claim["payload_sha256"],
        }
        return 1

    state._insert_event_unlocked = insert_event_unlocked
    kwargs = {
        "run_id": "postgres-idempotent-checkpoint-retry",
        "source": "dual_agent",
        "kind": "dual_agent_production_trace_recorded",
        "payload": {
            "source_event_hash": "b" * 64,
            "receipt": {"status": "recorded"},
        },
        "idempotency_key": "production-trace:" + ("b" * 64),
    }

    with pytest.raises(
        CheckpointPersistenceError,
        match="trusted_pin_persistence",
    ):
        state.write_event_once(**kwargs)

    assert state.write_event_once(**kwargs) == 1
    assert insert_calls == 1
    assert coordinator.attempts == 2
    assert coordinator.published_event_ids == [1]
    connection.event["source"] = "attacker"
    with pytest.raises(
        RuntimeError,
        match="does not match its immutable event",
    ):
        state.write_event_once(**kwargs)
    connection.event["source"] = "dual_agent"
    with pytest.raises(RuntimeError, match="changed source or payload"):
        state.write_event_once(
            **{
                **kwargs,
                "payload": {
                    "source_event_hash": "b" * 64,
                    "receipt": {"status": "different"},
                },
            }
        )
    assert coordinator.attempts == 2


def test_postgres_event_stream_rejects_truncate(postgres_state):
    postgres_state.write_event(
        run_id="truncate-guard",
        source="test",
        kind="event_msg",
        payload={"value": 1},
    )

    with pytest.raises(
        postgres_state._errors.RaiseException,
        match="events are append-only",
    ):
        with postgres_state._conn.transaction():
            postgres_state._conn.execute("TRUNCATE events")

    assert len(postgres_state.read_events_since("truncate-guard")) == 1


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE events SET source='attacker' WHERE run_id='mutation-guard'",
        "DELETE FROM events WHERE run_id='mutation-guard'",
    ),
)
def test_postgres_event_stream_rejects_row_mutation(postgres_state, statement):
    postgres_state.write_event(
        run_id="mutation-guard",
        source="test",
        kind="event_msg",
        payload={"value": 1},
    )

    with pytest.raises(
        postgres_state._errors.RaiseException,
        match="events are append-only",
    ):
        with postgres_state._conn.transaction():
            postgres_state._conn.execute(statement)

    [event] = postgres_state.read_events_since("mutation-guard")
    assert event["source"] == "test"


def test_postgres_event_stream_rejects_conflicting_sequence(postgres_state):
    postgres_state.write_event(
        run_id="sequence-guard",
        source="test",
        kind="event_msg",
        payload={"value": 1},
    )

    with pytest.raises(
        postgres_state._errors.UniqueViolation,
        match="events_run_sequence_unique|idx_events_run_sequence",
    ):
        with postgres_state._conn.transaction():
            postgres_state._conn.execute(
                """INSERT INTO events(
                     run_id, event_id, event_sequence, previous_event_id, ts,
                     source, kind, payload_json, previous_event_hash,
                     event_hash, canonical_payload_hash,
                     artifact_manifest_hash, ledger_genesis_kind)
                   SELECT run_id, event_id + 1, event_sequence, event_id, ts + 1,
                          source, kind, payload_json, event_hash,
                          %s, %s, artifact_manifest_hash, NULL
                     FROM events
                    WHERE run_id=%s""",
                ("f" * 64, "e" * 64, "sequence-guard"),
            )

    assert len(postgres_state.read_events_since("sequence-guard")) == 1


def test_postgres_immutable_audits_and_workflow_jobs_reject_truncate(
    postgres_state,
    tmp_path,
):
    postgres_state.upsert_quality_trend_row(
        run_id="truncate-audit",
        task_id="task",
        task_class="generic",
        gate="outcome_review",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=1.0,
    )
    postgres_state.update_quality_trend_audit(
        run_id="truncate-audit",
        gate="outcome_review",
        sample_size=1,
        false_accept_count=0,
        false_accept_denominator=1,
    )
    _reserve_job(
        postgres_state,
        tmp_path,
        job_id="truncate-job",
        token="truncate-job-token",
    )

    with pytest.raises(
        postgres_state._errors.RaiseException,
        match="quality trend audits are immutable",
    ):
        with postgres_state._conn.transaction():
            postgres_state._conn.execute("TRUNCATE quality_trend_audits")
    with pytest.raises(
        postgres_state._errors.RaiseException,
        match="workflow jobs are immutable evidence",
    ):
        with postgres_state._conn.transaction():
            postgres_state._conn.execute("TRUNCATE dual_agent_workflow_jobs")

    assert len(
        postgres_state.list_quality_trend_audits(
            run_id="truncate-audit",
            gate="outcome_review",
        )
    ) == 1
    assert postgres_state.get_dual_agent_workflow_job(
        job_id="truncate-job"
    ) is not None


def test_postgres_generic_job_update_cannot_forge_worker_reap_proof(
    postgres_state,
    tmp_path,
):
    row, created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="postgres-no-forged-reap",
        token="postgres-no-forged-reap-token",
    )
    assert created is True

    with pytest.raises(
        RuntimeError,
        match="containment-verified reap API",
    ):
        postgres_state.update_dual_agent_workflow_job(
            job_id=row["job_id"],
            worker_reaped_at=200,
        )

    stored = postgres_state.get_dual_agent_workflow_job(
        job_id=row["job_id"],
    )
    assert stored["worker_reaped_at"] is None


def test_postgres_historical_claim_requires_authorized_linked_terminal(
    postgres_state,
    monkeypatch,
):
    operation_id = "historical-postgres-linked"
    request_hash = "c" * 64
    row, reserved = postgres_state.reserve_historical_operation(
        operation_id=operation_id,
        request_hash=request_hash,
        operation="replay",
    )
    assert reserved is True
    assert row["status"] == "running"

    with pytest.raises(
        ValueError,
        match="historical operation events require the dedicated writer",
    ):
        postgres_state.write_event(
            run_id=operation_id,
            source="historical_evaluation",
            kind="historical_operation.completed",
            payload={"request_hash": request_hash},
        )

    for kind in (
        "historical_operation.requested",
        "historical_operation.completed",
        "historical_operation.failed",
    ):
        with pytest.raises(ValueError, match="owner-fenced state methods"):
            postgres_state.write_historical_operation_event(
                run_id=operation_id,
                kind=kind,
                payload={"request_hash": request_hash},
            )

    requested_event_id = _insert_legacy_postgres_historical_event(
        postgres_state,
        run_id=operation_id,
        kind="historical_operation.requested",
        payload={
            "operation_id": operation_id,
            "request_hash": request_hash,
            "request": {"operation": "replay"},
        },
    )
    terminal_event_id = _insert_legacy_postgres_historical_event(
        postgres_state,
        run_id=operation_id,
        kind="historical_operation.completed",
        payload={
            "operation_id": operation_id,
            "operation": "replay",
            "request_hash": request_hash,
            "requested_event_id": requested_event_id,
        },
    )

    assert postgres_state.complete_historical_operation(
        operation_id=operation_id,
        request_hash=request_hash,
        status="completed",
        terminal_event_id=terminal_event_id,
    ) == 1
    assert postgres_state.complete_historical_operation(
        operation_id=operation_id,
        request_hash=request_hash,
        status="completed",
        terminal_event_id=terminal_event_id,
    ) == 0

    owned_bypass_id = "historical-postgres-owned-legacy-bypass"
    owned_bypass_hash = "1" * 64
    bypass_reserved, reserved = postgres_state.reserve_historical_operation(
        operation_id=owned_bypass_id,
        request_hash=owned_bypass_hash,
        operation="replay",
    )
    assert reserved is True
    bypass_claim, bypass_requested_event_id, acquired = (
        postgres_state.claim_historical_operation_execution(
            operation_id=owned_bypass_id,
            request_hash=owned_bypass_hash,
            operation="replay",
            request={"operation": "replay"},
            owner_token="postgres-owned-legacy-bypass",
            expected_claim_updated_at=bypass_reserved["updated_at"],
            expected_execution_owner_token=bypass_reserved[
                "execution_owner_token"
            ],
            expected_execution_generation=bypass_reserved[
                "execution_generation"
            ],
            expected_execution_heartbeat_at=bypass_reserved[
                "execution_heartbeat_at"
            ],
        )
    )
    assert acquired is True
    assert bypass_claim["execution_generation"] == 1
    assert bypass_requested_event_id is not None
    bypass_terminal_event_id = _insert_legacy_postgres_historical_event(
        postgres_state,
        run_id=owned_bypass_id,
        kind="historical_operation.completed",
        payload={
            "operation_id": owned_bypass_id,
            "operation": "replay",
            "request_hash": owned_bypass_hash,
            "requested_event_id": bypass_requested_event_id,
        },
    )
    with pytest.raises(RuntimeError, match="compare-and-set failed"):
        postgres_state.complete_historical_operation(
            operation_id=owned_bypass_id,
            request_hash=owned_bypass_hash,
            status="completed",
            terminal_event_id=bypass_terminal_event_id,
        )
    assert postgres_state._conn.execute(
        """SELECT status
             FROM historical_operation_claims
            WHERE operation_id=%s""",
        (owned_bypass_id,),
    ).fetchone()["status"] == "running"

    late_release_id = "historical-postgres-late-preflight-release"
    late_release_hash = "2" * 64
    preflight_claim, reserved = (
        postgres_state.reserve_historical_operation(
            operation_id=late_release_id,
            request_hash=late_release_hash,
            operation="replay",
        )
    )
    assert reserved is True
    execution_claim, late_requested_event_id, acquired = (
        postgres_state.claim_historical_operation_execution(
            operation_id=late_release_id,
            request_hash=late_release_hash,
            operation="replay",
            request={"operation": "replay"},
            owner_token="postgres-late-release-owner",
            expected_claim_updated_at=preflight_claim["updated_at"],
            expected_execution_owner_token=preflight_claim[
                "execution_owner_token"
            ],
            expected_execution_generation=preflight_claim[
                "execution_generation"
            ],
            expected_execution_heartbeat_at=preflight_claim[
                "execution_heartbeat_at"
            ],
        )
    )
    assert acquired is True
    assert late_requested_event_id is not None
    late_terminal_event_id, created = (
        postgres_state.terminalize_historical_operation_execution(
            operation_id=late_release_id,
            request_hash=late_release_hash,
            operation="replay",
            owner_token="postgres-late-release-owner",
            execution_generation=execution_claim[
                "execution_generation"
            ],
            status="completed",
            payload={
                "operation_id": late_release_id,
                "operation": "replay",
                "request_hash": late_release_hash,
                "requested_event_id": late_requested_event_id,
                "execution_owner_token": "postgres-late-release-owner",
                "execution_generation": execution_claim[
                    "execution_generation"
                ],
            },
        )
    )
    assert late_terminal_event_id is not None
    assert created is True
    assert postgres_state.release_historical_operation_preflight(
        operation_id=late_release_id,
        request_hash=late_release_hash,
        operation="replay",
        expected_claim_updated_at=preflight_claim["updated_at"],
        expected_execution_owner_token=preflight_claim[
            "execution_owner_token"
        ],
        expected_execution_generation=preflight_claim[
            "execution_generation"
        ],
        expected_execution_heartbeat_at=preflight_claim[
            "execution_heartbeat_at"
        ],
        payload={
            "operation_id": late_release_id,
            "request_hash": late_release_hash,
            "operation": "replay",
            "error_type": "HistoricalEvidenceError",
            "error": "stale verifier",
        },
    ) is None
    assert [
        event["kind"]
        for event in postgres_state.read_events_since(
            late_release_id,
            after_event_id=0,
            limit=10,
        )
    ] == [
        "historical_operation.requested",
        "historical_operation.completed",
    ]

    for retry_state in ("stale", "preflight_released"):
        retry_operation_id = (
            f"historical-postgres-retry-{retry_state}"
        )
        retry_request_hash = "d" * 64
        claim, reserved = postgres_state.reserve_historical_operation(
            operation_id=retry_operation_id,
            request_hash=retry_request_hash,
            operation="replay",
        )
        assert reserved is True
        if retry_state == "stale":
            claim = dict(
                postgres_state._conn.execute(
                    """UPDATE historical_operation_claims
                          SET updated_at=0
                        WHERE operation_id=%s
                    RETURNING *""",
                    (retry_operation_id,),
                ).fetchone()
            )
        else:
            released_event_id = (
                postgres_state.release_historical_operation_preflight(
                    operation_id=retry_operation_id,
                    request_hash=retry_request_hash,
                    operation="replay",
                    expected_claim_updated_at=claim["updated_at"],
                    expected_execution_owner_token=claim[
                        "execution_owner_token"
                    ],
                    expected_execution_generation=claim[
                        "execution_generation"
                    ],
                    expected_execution_heartbeat_at=claim[
                        "execution_heartbeat_at"
                    ],
                    payload={
                        "operation_id": retry_operation_id,
                        "request_hash": retry_request_hash,
                        "operation": "replay",
                        "error_type": "HistoricalEvidenceError",
                        "error": "retryable preflight failure",
                    },
                )
            )
            assert released_event_id is not None
            claim = dict(
                postgres_state._conn.execute(
                    """SELECT * FROM historical_operation_claims
                        WHERE operation_id=%s""",
                    (retry_operation_id,),
                ).fetchone()
            )

        second_state = PostgresState(
            postgres_state.dsn,
            schema=postgres_state.schema,
            apply_schema=False,
        )
        barrier = Barrier(2)

        def claim_execution(state):
            barrier.wait(timeout=5)
            return state.claim_historical_operation_execution(
                operation_id=retry_operation_id,
                request_hash=retry_request_hash,
                operation="replay",
                request={"operation": "replay"},
                owner_token=(
                    f"postgres-owner-{retry_state}-{id(state)}"
                ),
                expected_claim_updated_at=claim["updated_at"],
                expected_execution_owner_token=claim[
                    "execution_owner_token"
                ],
                expected_execution_generation=claim[
                    "execution_generation"
                ],
                expected_execution_heartbeat_at=claim[
                    "execution_heartbeat_at"
                ],
                lease_duration_s=(
                    1.0 if retry_state == "stale" else None
                ),
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        claim_execution,
                        (postgres_state, second_state),
                    )
                )
        finally:
            second_state.close()

        assert sum(
            acquired
            for _claim, _event_id, acquired in results
        ) == 1
        requested_event_ids = {
            event_id
            for _claim, event_id, _acquired in results
            if event_id is not None
        }
        assert len(requested_event_ids) == 1
        expected_kinds = (
            [
                "historical_operation.preflight_released",
                "historical_operation.requested",
            ]
            if retry_state == "preflight_released"
            else ["historical_operation.requested"]
        )
        assert [
            event["kind"]
            for event in postgres_state.read_events_since(
                retry_operation_id,
                after_event_id=0,
                limit=10,
            )
        ] == expected_kinds

    owned_operation_id = "historical-postgres-owned-stale"
    owned_request_hash = "e" * 64
    reserved_claim, reserved = postgres_state.reserve_historical_operation(
        operation_id=owned_operation_id,
        request_hash=owned_request_hash,
        operation="replay",
    )
    assert reserved is True
    original_owner = "postgres-historical-owner-a"
    owned_claim, requested_event_id, acquired = (
        postgres_state.claim_historical_operation_execution(
            operation_id=owned_operation_id,
            request_hash=owned_request_hash,
            operation="replay",
            request={"operation": "replay"},
            owner_token=original_owner,
            expected_claim_updated_at=reserved_claim["updated_at"],
            expected_execution_owner_token=reserved_claim[
                "execution_owner_token"
            ],
            expected_execution_generation=reserved_claim[
                "execution_generation"
            ],
            expected_execution_heartbeat_at=reserved_claim[
                "execution_heartbeat_at"
            ],
        )
    )
    assert acquired is True
    assert requested_event_id is not None
    original_generation = owned_claim["execution_generation"]
    assert postgres_state.heartbeat_historical_operation_execution(
        operation_id=owned_operation_id,
        request_hash=owned_request_hash,
        owner_token=original_owner,
        execution_generation=original_generation,
    ) is True
    assert postgres_state.heartbeat_historical_operation_execution(
        operation_id=owned_operation_id,
        request_hash=owned_request_hash,
        owner_token="wrong-owner",
        execution_generation=original_generation,
    ) is False
    observed_stale = dict(
        postgres_state._conn.execute(
            """UPDATE historical_operation_claims
                  SET execution_heartbeat_at=0, updated_at=0
                WHERE operation_id=%s
            RETURNING *""",
            (owned_operation_id,),
        ).fetchone()
    )
    assert postgres_state.heartbeat_historical_operation_execution(
        operation_id=owned_operation_id,
        request_hash=owned_request_hash,
        owner_token=original_owner,
        execution_generation=original_generation,
    ) is True
    heartbeat_won_claim, heartbeat_won = (
        postgres_state.take_over_stale_historical_operation_execution(
            operation_id=owned_operation_id,
            request_hash=owned_request_hash,
            operation="replay",
            new_owner_token="postgres-stale-observation-must-lose",
            expected_requested_event_id=requested_event_id,
            expected_claim_updated_at=observed_stale["updated_at"],
            expected_execution_owner_token=observed_stale[
                "execution_owner_token"
            ],
            expected_execution_generation=observed_stale[
                "execution_generation"
            ],
            expected_execution_heartbeat_at=observed_stale[
                "execution_heartbeat_at"
            ],
            lease_duration_s=1.0,
        )
    )
    assert heartbeat_won is False
    assert heartbeat_won_claim["execution_owner_token"] == original_owner
    assert heartbeat_won_claim["execution_generation"] == original_generation
    observed_stale = dict(
        postgres_state._conn.execute(
            """UPDATE historical_operation_claims
                  SET execution_heartbeat_at=0, updated_at=0
                WHERE operation_id=%s
            RETURNING *""",
            (owned_operation_id,),
        ).fetchone()
    )
    takeover_owner = "postgres-historical-owner-b"
    takeover_claim, took_over = (
        postgres_state.take_over_stale_historical_operation_execution(
            operation_id=owned_operation_id,
            request_hash=owned_request_hash,
            operation="replay",
            new_owner_token=takeover_owner,
            expected_requested_event_id=requested_event_id,
            expected_claim_updated_at=observed_stale["updated_at"],
            expected_execution_owner_token=observed_stale[
                "execution_owner_token"
            ],
            expected_execution_generation=observed_stale[
                "execution_generation"
            ],
            expected_execution_heartbeat_at=observed_stale[
                "execution_heartbeat_at"
            ],
            lease_duration_s=1.0,
        )
    )
    assert took_over is True
    assert takeover_claim["execution_owner_token"] == takeover_owner
    assert (
        takeover_claim["execution_generation"]
        == original_generation + 1
    )
    terminal_payload = {
        "operation_id": owned_operation_id,
        "operation": "replay",
        "request_hash": owned_request_hash,
        "requested_event_id": requested_event_id,
        "execution_owner_token": takeover_owner,
        "execution_generation": takeover_claim["execution_generation"],
        "error_type": "HistoricalOperationIndeterminate",
        "error": "stale owner recovered",
    }
    assert postgres_state.terminalize_historical_operation_execution(
        operation_id=owned_operation_id,
        request_hash=owned_request_hash,
        operation="replay",
        owner_token=original_owner,
        execution_generation=original_generation,
        status="completed",
        payload={
            "operation_id": owned_operation_id,
            "operation": "replay",
            "request_hash": owned_request_hash,
            "requested_event_id": requested_event_id,
        },
    ) == (None, False)
    terminal_event_id, terminal_created = (
        postgres_state.terminalize_historical_operation_execution(
            operation_id=owned_operation_id,
            request_hash=owned_request_hash,
            operation="replay",
            owner_token=takeover_owner,
            execution_generation=takeover_claim[
                "execution_generation"
            ],
            status="failed",
            payload=terminal_payload,
        )
    )
    assert terminal_event_id is not None
    assert terminal_created is True
    assert postgres_state.terminalize_historical_operation_execution(
        operation_id=owned_operation_id,
        request_hash=owned_request_hash,
        operation="replay",
        owner_token=takeover_owner,
        execution_generation=takeover_claim["execution_generation"],
        status="failed",
        payload=terminal_payload,
    ) == (terminal_event_id, False)
    assert [
        event["kind"]
        for event in postgres_state.read_events_since(
            owned_operation_id,
            after_event_id=0,
            limit=10,
        )
    ] == [
        "historical_operation.requested",
        "historical_operation.failed",
    ]

    fresh_operation_id = "historical-postgres-fresh-state-clock"
    fresh_request_hash = "3" * 64
    fresh_claim, reserved = postgres_state.reserve_historical_operation(
        operation_id=fresh_operation_id,
        request_hash=fresh_request_hash,
        operation="replay",
    )
    assert reserved is True
    with monkeypatch.context() as clock_skew:
        clock_skew.setattr(
            "supervisor.postgres_state.time.time",
            lambda: 10**15,
        )
        assert postgres_state.historical_operation_preflight_claim_is_stale(
            operation_id=fresh_operation_id,
            request_hash=fresh_request_hash,
            operation="replay",
            expected_claim_updated_at=fresh_claim["updated_at"],
            expected_execution_owner_token=fresh_claim[
                "execution_owner_token"
            ],
            expected_execution_generation=fresh_claim[
                "execution_generation"
            ],
            expected_execution_heartbeat_at=fresh_claim[
                "execution_heartbeat_at"
            ],
            lease_duration_s=3600,
        ) is False
        current, fresh_requested_event_id, acquired = (
            postgres_state.claim_historical_operation_execution(
                operation_id=fresh_operation_id,
                request_hash=fresh_request_hash,
                operation="replay",
                request={"operation": "replay"},
                owner_token="skewed-caller-must-not-steal",
                expected_claim_updated_at=fresh_claim["updated_at"],
                expected_execution_owner_token=fresh_claim[
                    "execution_owner_token"
                ],
                expected_execution_generation=fresh_claim[
                    "execution_generation"
                ],
                expected_execution_heartbeat_at=fresh_claim[
                    "execution_heartbeat_at"
                ],
                lease_duration_s=3600,
            )
        )
    assert acquired is False
    assert fresh_requested_event_id is None
    assert current["execution_owner_token"] is None


def test_postgres_reapplying_schema_preserves_native_event_hashes(postgres_state):
    postgres_state.write_event(
        run_id="stable-native-run",
        source="test",
        kind="event_msg",
        payload={"index": 1},
        ts=101,
    )
    postgres_state.write_event(
        run_id="stable-native-run",
        source="test",
        kind="event_msg",
        payload={"index": 2},
        ts=102,
    )
    before = postgres_state.read_events_since(
        "stable-native-run",
        after_event_id=0,
        limit=10,
    )

    reopened = PostgresState(
        postgres_state.dsn,
        schema=postgres_state.schema,
    )
    try:
        after = reopened.read_events_since(
            "stable-native-run",
            after_event_id=0,
            limit=10,
        )
    finally:
        reopened.close()

    assert [event["ledger_genesis_kind"] for event in before] == [
        "native",
        None,
    ]
    assert after == before


def test_postgres_gate_event_rows_keep_sqlite_payload_shape(postgres_state):
    postgres_state.write_event(
        run_id="run-shape",
        source="test",
        kind="dual_agent_gate_result",
        payload={"task_id": "task", "gate": "outcome_review", "status": "accepted"},
    )

    [row] = postgres_state.read_dual_agent_gate_events("run-shape")

    assert isinstance(row["payload_json"], str)
    assert json.loads(row["payload_json"])["status"] == "accepted"
    assert isinstance(
        postgres_state.get_event(run_id="run-shape", event_id=1)["payload_json"],
        str,
    )


def test_postgres_workflow_resume_prompt_uses_workflow_metadata(postgres_state, tmp_path):
    postgres_state.upsert_dual_agent_workflow(
        run_id="resume-run",
        task_id="resume-task",
        cwd=str(tmp_path),
        intent="resume prompt smoke",
        current_gate="execution",
        status="blocked",
        max_rounds_per_gate=2,
        user_facing=False,
    )
    postgres_state.record_dual_agent_workflow_step(
        run_id="resume-run",
        task_id="resume-task",
        gate="execution",
        status="blocked",
        attempt_count=1,
        latest_event_id=7,
    )
    postgres_state.update_dual_agent_workflow(
        run_id="resume-run",
        task_id="resume-task",
        status="running",
        current_gate="outcome_review",
    )

    prompt = workflow_resume_prompt(
        postgres_state,
        run_id="resume-run",
        task_id="resume-task",
    )

    assert prompt["status"] == "ok"
    assert prompt["current_gate"] == "outcome_review"
    assert "outcome_review" in prompt["prompt"]
    assert prompt["steps"] == [{
        "gate": "execution",
        "status": "blocked",
        "attempt_count": 1,
        "latest_event_id": 7,
    }]


def test_postgres_supervisor_lesson_record_query_and_list(postgres_state):
    lesson, created = postgres_state.record_supervisor_lesson(
        task_class="large",
        gate="execution",
        taxonomy_code="FM-3.2",
        root_cause="No or incomplete verification",
        remediation="Verify supervisor-generated receipts before accepting.",
        source_run_id="source-run",
        created_at=10,
    )
    duplicate, duplicate_created = postgres_state.record_supervisor_lesson(
        task_class="large",
        gate="execution",
        taxonomy_code="FM-3.2",
        root_cause="No or incomplete verification",
        remediation="Verify supervisor-generated receipts before accepting.",
        source_run_id="source-run",
        created_at=20,
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["lesson_id"] == lesson["lesson_id"]
    assert duplicate["observed_count"] == 2
    assert postgres_state.query_supervisor_lessons(
        task_class="large",
        gate="execution",
    ) == [duplicate]
    assert postgres_state.query_supervisor_lessons(task_class="small", gate="execution") == []
    assert postgres_state.list_supervisor_lessons() == [duplicate]


def test_postgres_trends_details_and_incident_aggregation_match_sqlite(postgres_state):
    row = postgres_state.upsert_quality_trend_row(
        run_id="trend-row-run",
        task_id="trend-task",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=3.0,
        details={
            "transport_incidents": {
                "total_count": 2,
                "by_era": {"mcp": 1, "axi": 1},
            },
            "format_ab": {
                "toon": {"turns": 1, "bytes": 80},
                "json": {"turns": 2, "bytes": 200},
            },
        },
    )
    [stored] = postgres_state.list_quality_trend_rows(
        task_class="source_change",
        gate="execution",
    )
    [summary] = query_quality_trends(
        postgres_state,
        task_class="source_change",
        gate="execution",
    )

    assert row["details"]["transport_incidents"]["by_era"] == {"mcp": 1, "axi": 1}
    assert stored["details"]["format_ab"]["json"]["bytes"] == 200
    assert summary["transport_incident_by_era"] == {"axi": 1, "mcp": 1}
    assert summary["transport_incident_axi_rate"] == 1.0
    assert summary["transport_incident_axi_share"] == 0.5
    assert summary["format_toon_turns"] == 1

    postgres_state.write_event(
        run_id="incident-run",
        source="test",
        kind="dual_agent_workflow_route",
        payload={"task_id": "trend-task", "lesson_task_class": "source_change"},
    )
    record_transport_incident(
        postgres_state,
        run_id="incident-run",
        task_id="trend-task",
        incident_type="poll_failure",
        interface="axi",
    )
    postgres_state.write_event(
        run_id="incident-run",
        source="test",
        kind="dual_agent_gate_result",
        payload={
            "task_id": "trend-task",
            "gate": "outcome_review",
            "status": "accepted",
            "supervisor_final_status": "accepted",
            "claude_gate_status": "accepted",
            "attempts": 1,
            "outcome": {"decision": "accept", "changed_files": [], "tests": []},
        },
    )

    events = postgres_state.read_events_since("incident-run", after_event_id=0, limit=10)
    assert any(
        event["kind"] == "transport_incident_observed"
        and event["payload"]["incident_type"] == "poll_failure"
        for event in events
    )
    [aggregated] = record_quality_trends_for_run(postgres_state, run_id="incident-run")
    assert aggregated["details"]["transport_incidents"]["total_count"] == 1
    assert aggregated["details"]["transport_incidents"]["by_type"]["poll_failure"] == 1
    assert aggregated["details"]["transport_incidents"]["by_era"]["axi"] == 1


def test_postgres_quality_projection_rebuild_matches_ledger(
    postgres_state,
    tmp_path,
):
    run_id = "postgres-quality-projection-rebuild"
    postgres_state.upsert_quality_trend_row(
        run_id=run_id,
        task_id="projection-task",
        task_class="generic",
        gate="outcome_review",
        accepted=True,
        first_pass_accepted=False,
        revision_rounds=2,
        time_to_accepted_outcome_s=12.5,
        policy_overlay_hash="a" * 64,
        policy_proposal_id="proposal-1",
        details={"source": "runtime"},
        computed_at=100,
    )
    postgres_state.update_quality_trend_audit(
        run_id=run_id,
        gate="outcome_review",
        sample_size=3,
        false_accept_count=1,
        false_accept_denominator=3,
        audit_details={"auditor": "hidden"},
    )
    expected = postgres_state.quality_trend_projection_snapshot()
    authority = HmacCheckpointAuthority(
        key_id="projection-key",
        key=b"postgres-projection-checkpoint-key",
    )
    checkpoints = LedgerCheckpointStore(tmp_path / "projection-checkpoints")
    pins = FilesystemTrustedCheckpointPinStore(
        tmp_path / "projection-trusted-pins"
    )
    persisted = postgres_state.checkpoint_event_ledger(
        run_id,
        checkpoint_store=checkpoints,
        signer=authority,
        verifier=authority,
        created_at=1234,
    )
    pins.pin(checkpoint_identity(persisted.checkpoint))
    trusted = {run_id: pins.latest(run_id)}

    rebuilt = postgres_state.rebuild_quality_trend_projection_from_ledger(
        checkpoint_store=checkpoints,
        verifier=authority,
        expected_stream_checkpoint_pins=trusted,
    )
    assert rebuilt == expected

    with postgres_state._conn.transaction():
        postgres_state._conn.execute(
            """UPDATE supervisor_quality_trends
                  SET accepted=FALSE,
                      false_accept_count=0,
                      details_json='{}'::jsonb
                WHERE run_id=%s""",
            (run_id,),
        )
    assert postgres_state.quality_trend_projection_snapshot() != expected

    restored = postgres_state.rebuild_quality_trend_projection_from_ledger(
        replace=True,
        checkpoint_store=checkpoints,
        verifier=authority,
        expected_stream_checkpoint_pins=trusted,
    )
    assert restored == expected


def _postgres_projection_authority(
    postgres_state,
    tmp_path,
    *run_ids: str,
):
    authority = HmacCheckpointAuthority(
        key_id="projection-inventory-key",
        key=b"postgres-projection-inventory-key",
    )
    checkpoints = LedgerCheckpointStore(
        tmp_path / "projection-inventory-checkpoints"
    )
    pins = FilesystemTrustedCheckpointPinStore(
        tmp_path / "projection-inventory-pins"
    )
    expected: dict[str, dict[str, object]] = {}
    for run_id in run_ids:
        persisted = postgres_state.checkpoint_event_ledger(
            run_id,
            checkpoint_store=checkpoints,
            signer=authority,
            verifier=authority,
            created_at=1234,
        )
        identity = checkpoint_identity(persisted.checkpoint)
        pins.pin(identity)
        expected[run_id] = pins.latest(run_id)
    return {
        "checkpoint_store": checkpoints,
        "verifier": authority,
        "expected_stream_checkpoint_pins": expected,
    }


def test_postgres_quality_projection_rebuild_rejects_empty_expected_inventory(
    postgres_state,
    tmp_path,
):
    with pytest.raises(
        RuntimeError,
        match="expected stream inventory must be non-empty",
    ):
        postgres_state.rebuild_quality_trend_projection_from_ledger(
            checkpoint_store=LedgerCheckpointStore(
                tmp_path / "projection-inventory-checkpoints"
            ),
            verifier=HmacCheckpointAuthority(
                key_id="projection-inventory-key",
                key=b"postgres-projection-inventory-key",
            ),
            expected_stream_checkpoint_pins={},
        )


def test_postgres_quality_projection_rebuild_repairs_deleted_row(
    postgres_state,
    tmp_path,
):
    run_id = "postgres-quality-projection-row-repair"
    for gate in ("execution", "outcome_review"):
        postgres_state.upsert_quality_trend_row(
            run_id=run_id,
            task_id="projection-task",
            task_class="generic",
            gate=gate,
            accepted=True,
            first_pass_accepted=gate == "execution",
            revision_rounds=0 if gate == "execution" else 1,
            time_to_accepted_outcome_s=1.0,
        )
    expected = postgres_state.quality_trend_projection_snapshot()
    authoritative = _postgres_projection_authority(
        postgres_state,
        tmp_path,
        run_id,
    )
    with postgres_state._conn.transaction():
        postgres_state._conn.execute(
            """
            DELETE FROM supervisor_quality_trends
             WHERE run_id=%s AND gate=%s
            """,
            (run_id, "outcome_review"),
        )

    restored = postgres_state.rebuild_quality_trend_projection_from_ledger(
        replace=True,
        **authoritative,
    )

    assert restored == expected


def test_postgres_quality_projection_rebuild_rejects_deleted_expected_stream(
    postgres_state,
    tmp_path,
):
    run_ids = (
        "postgres-quality-projection-surviving",
        "postgres-quality-projection-deleted",
    )
    for run_id in run_ids:
        postgres_state.upsert_quality_trend_row(
            run_id=run_id,
            task_id=f"{run_id}-task",
            task_class="generic",
            gate="execution",
            accepted=True,
            first_pass_accepted=True,
            revision_rounds=0,
            time_to_accepted_outcome_s=1.0,
        )
    authoritative = _postgres_projection_authority(
        postgres_state,
        tmp_path,
        *run_ids,
    )
    deleted_run = run_ids[1]
    with postgres_state._conn.transaction():
        postgres_state._conn.execute(
            "ALTER TABLE events DISABLE TRIGGER events_no_delete"
        )
        postgres_state._conn.execute(
            "DELETE FROM events WHERE run_id=%s",
            (deleted_run,),
        )
        postgres_state._conn.execute(
            "ALTER TABLE events ENABLE TRIGGER events_no_delete"
        )
        postgres_state._conn.execute(
            "DELETE FROM supervisor_quality_trends WHERE run_id=%s",
            (deleted_run,),
        )

    with pytest.raises(
        RuntimeError,
        match=(
            "postgres-quality-projection-deleted: "
            "checkpoint_event_count_mismatch"
        ),
    ):
        postgres_state.rebuild_quality_trend_projection_from_ledger(
            replace=True,
            **authoritative,
        )


def test_postgres_quality_projection_rebuild_serializes_concurrent_new_stream(
    postgres_state,
    tmp_path,
    monkeypatch,
):
    initial_run_id = "postgres-quality-projection-before-rebuild"
    concurrent_run_id = "postgres-quality-projection-during-rebuild"
    postgres_state.upsert_quality_trend_row(
        run_id=initial_run_id,
        task_id="initial-task",
        task_class="generic",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=1.0,
    )
    authoritative = _postgres_projection_authority(
        postgres_state,
        tmp_path,
        initial_run_id,
    )

    import supervisor.ledger_checkpoints as ledger_checkpoints

    original_verify = (
        ledger_checkpoints.verify_authoritative_event_chain
    )
    verification_started = threading.Event()
    allow_verification = threading.Event()

    def paused_verify(*args, **kwargs):
        verification_started.set()
        if not allow_verification.wait(timeout=10):
            raise TimeoutError("test did not release projection verification")
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(
        ledger_checkpoints,
        "verify_authoritative_event_chain",
        paused_verify,
    )

    concurrent_state = PostgresState(
        postgres_state.dsn,
        schema=postgres_state.schema,
        apply_schema=False,
    )
    writer_started = threading.Event()
    writer_finished = threading.Event()

    def write_concurrent_projection() -> None:
        writer_started.set()
        try:
            concurrent_state.upsert_quality_trend_row(
                run_id=concurrent_run_id,
                task_id="concurrent-task",
                task_class="generic",
                gate="execution",
                accepted=True,
                first_pass_accepted=True,
                revision_rounds=0,
                time_to_accepted_outcome_s=1.0,
            )
        finally:
            writer_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rebuild_future = pool.submit(
                postgres_state.rebuild_quality_trend_projection_from_ledger,
                replace=True,
                **authoritative,
            )
            assert verification_started.wait(timeout=10)
            writer_future = pool.submit(write_concurrent_projection)
            assert writer_started.wait(timeout=10)
            assert not writer_finished.wait(timeout=0.25)

            allow_verification.set()
            rebuilt = rebuild_future.result(timeout=10)
            writer_future.result(timeout=10)
    finally:
        allow_verification.set()
        concurrent_state.close()

    assert {
        row["run_id"] for row in rebuilt
    } == {initial_run_id}
    assert {
        row["run_id"]
        for row in postgres_state.quality_trend_projection_snapshot()
    } == {initial_run_id, concurrent_run_id}


def test_postgres_quality_projection_rebuild_avoids_existing_stream_deadlock(
    postgres_state,
    tmp_path,
    monkeypatch,
):
    run_id = "postgres-quality-projection-existing-stream-race"
    postgres_state.upsert_quality_trend_row(
        run_id=run_id,
        task_id="initial-task",
        task_class="generic",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=1.0,
        computed_at=100,
    )
    authoritative = _postgres_projection_authority(
        postgres_state,
        tmp_path,
        run_id,
    )

    import supervisor.ledger_checkpoints as ledger_checkpoints

    original_verify = (
        ledger_checkpoints.verify_authoritative_event_chain
    )
    verification_started = threading.Event()
    allow_verification = threading.Event()

    def paused_verify(*args, **kwargs):
        verification_started.set()
        if not allow_verification.wait(timeout=10):
            raise TimeoutError("test did not release projection verification")
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(
        ledger_checkpoints,
        "verify_authoritative_event_chain",
        paused_verify,
    )

    concurrent_state = PostgresState(
        postgres_state.dsn,
        schema=postgres_state.schema,
        apply_schema=False,
    )
    writer_started = threading.Event()
    writer_finished = threading.Event()

    def update_existing_projection() -> None:
        writer_started.set()
        try:
            concurrent_state.upsert_quality_trend_row(
                run_id=run_id,
                task_id="updated-task",
                task_class="generic",
                gate="execution",
                accepted=False,
                first_pass_accepted=False,
                revision_rounds=1,
                time_to_accepted_outcome_s=2.0,
                computed_at=200,
            )
        finally:
            writer_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rebuild_future = pool.submit(
                postgres_state.rebuild_quality_trend_projection_from_ledger,
                replace=True,
                **authoritative,
            )
            assert verification_started.wait(timeout=10)
            writer_future = pool.submit(update_existing_projection)
            assert writer_started.wait(timeout=10)
            assert not writer_finished.wait(timeout=0.25)

            allow_verification.set()
            rebuilt = rebuild_future.result(timeout=10)
            writer_future.result(timeout=10)
    finally:
        allow_verification.set()
        concurrent_state.close()

    [rebuilt_row] = rebuilt
    assert rebuilt_row["accepted"] is True
    assert rebuilt_row["computed_at"] == 100

    [row] = postgres_state.quality_trend_projection_snapshot()
    assert row["run_id"] == run_id
    assert row["task_id"] == "updated-task"
    assert row["accepted"] is False
    assert row["computed_at"] == 200


def test_postgres_quality_audit_revisions_serialize_and_reject_impossible_counts(
    postgres_state,
):
    postgres_state.upsert_quality_trend_row(
        run_id="audit-run",
        task_id="audit-task",
        task_class="source_change",
        gate="outcome_review",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=1.0,
        details={},
    )
    barrier = Barrier(2)
    revisions = (
        {
            "sample_size": 2,
            "false_accept_count": 1,
            "false_accept_denominator": 2,
            "audit_details": {"revision": "first"},
        },
        {
            "sample_size": 4,
            "false_accept_count": 0,
            "false_accept_denominator": 4,
            "audit_details": {"revision": "second"},
        },
    )

    def revise(index: int) -> dict:
        state = PostgresState(
            postgres_state.dsn,
            schema=postgres_state.schema,
            apply_schema=False,
        )
        try:
            barrier.wait()
            result = state.update_quality_trend_audit(
                run_id="audit-run",
                gate="outcome_review",
                **revisions[index],
            )
            assert result is not None
            return result
        finally:
            state.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(revise, range(2)))

    audits = postgres_state.list_quality_trend_audits(
        run_id="audit-run",
        gate="outcome_review",
    )
    assert len(results) == 2
    assert len(audits) == 2
    assert audits[0]["computed_at"] < audits[1]["computed_at"]
    assert {
        audit["audit_details"]["revision"] for audit in audits
    } == {"first", "second"}
    [projection] = postgres_state.list_quality_trend_rows(
        task_class="source_change",
        gate="outcome_review",
    )
    latest = audits[-1]
    assert projection["computed_at"] == latest["computed_at"]
    assert projection["p11_audit_sample_size"] == latest["sample_size"]
    assert projection["false_accept_count"] == latest["false_accept_count"]
    assert (
        projection["false_accept_denominator"]
        == latest["false_accept_denominator"]
    )
    assert projection["details"]["p11_audit"] == latest["audit_details"]

    with pytest.raises(ValueError, match="invalid quality audit counts"):
        postgres_state.update_quality_trend_audit(
            run_id="audit-run",
            gate="outcome_review",
            sample_size=2,
            false_accept_count=3,
            false_accept_denominator=2,
        )
    with pytest.raises(postgres_state._errors.CheckViolation):
        postgres_state._conn.execute(
            """INSERT INTO quality_trend_audits(
                 run_id, gate, sample_size, false_accept_count,
                 false_accept_denominator, false_accept_rate,
                 audit_details_json, computed_at)
               VALUES(
                 'audit-run', 'outcome_review', 2, 3, 2, 1.5, '{}'::jsonb, 1
               )"""
        )


def test_postgres_multi_writer_double_submit_creates_one_job(postgres_state, tmp_path):
    dsn = postgres_state.dsn
    schema = postgres_state.schema
    token = "same-token"

    def reserve(index: int) -> tuple[str, bool]:
        state = PostgresState(dsn, schema=schema, apply_schema=False)
        try:
            row, created = _reserve_job(
                state,
                tmp_path,
                job_id=f"job-{index}",
                token=token,
            )
            return row["job_id"], created
        finally:
            state.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))

    job_ids = [job_id for job_id, _created in results]
    created_count = sum(1 for _job_id, created in results if created)
    count_row = postgres_state._conn.execute(
        """SELECT COUNT(*) AS count
           FROM dual_agent_workflow_jobs
           WHERE idempotency_token=%s AND recovery_point != 'terminal'""",
        (token,),
    ).fetchone()

    assert created_count == 1
    assert len(set(job_ids)) == 1
    assert count_row["count"] == 1


def test_postgres_reserve_replays_terminal_token(postgres_state, tmp_path):
    row, created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="job-terminal-original",
        token="terminal-token",
    )
    assert created is True
    postgres_state.complete_dual_agent_workflow_job(
        job_id=row["job_id"],
        status="accepted",
        terminal_outcome={"status": "accepted", "task_id": "task", "run_id": "run"},
    )

    replayed, replay_created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="job-terminal-duplicate",
        token="terminal-token",
    )

    assert replay_created is False
    assert replayed["job_id"] == "job-terminal-original"
    assert replayed["recovery_point"] == "terminal"


def test_postgres_terminal_completion_is_cas_bound_and_frozen(
    postgres_state,
    tmp_path,
):
    row, created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="job-terminal-cas",
        token="terminal-cas-token",
    )
    assert created is True
    outcome = {
        "status": "accepted",
        "run_id": "run",
        "task_id": "task",
        "evidence": ["original"],
    }

    event_id = postgres_state.complete_dual_agent_workflow_job(
        job_id=row["job_id"],
        status="accepted",
        terminal_status="accepted",
        terminal_outcome=outcome,
        returncode=0,
    )
    assert postgres_state.complete_dual_agent_workflow_job(
        job_id=row["job_id"],
        status="accepted",
        terminal_status="accepted",
        terminal_outcome=outcome,
        returncode=0,
    ) == 0

    [event] = postgres_state.read_events_since(
        "run",
        after_event_id=0,
        limit=10,
    )
    assert event["event_id"] == event_id
    assert event["run_id"] == "run"
    assert event["event_sequence"] == 1
    assert event["kind"] == "dual_agent_workflow_terminal_outcome"
    assert event["payload"]["terminal_record"]["terminal_outcome"] == outcome
    assert len(event["payload"]["terminal_record_sha256"]) == 64

    with pytest.raises(RuntimeError, match="terminal outcome discrepancy"):
        postgres_state.complete_dual_agent_workflow_job(
            job_id=row["job_id"],
            status="blocked",
            terminal_status="blocked",
            terminal_outcome={
                "status": "blocked",
                "run_id": "run",
                "task_id": "task",
                "evidence": ["conflicting"],
            },
            error="conflicting completion",
        )
    with pytest.raises(RuntimeError, match="terminal workflow job is immutable"):
        postgres_state.clear_dual_agent_workflow_job_lease(
            job_id=row["job_id"],
            error="late dispatcher error",
        )
    with pytest.raises(RuntimeError, match="terminal workflow job is immutable"):
        postgres_state.park_dual_agent_workflow_job(
            job_id=row["job_id"],
            reason="late park",
        )
    with pytest.raises(
        postgres_state._errors.RaiseException,
        match="terminal workflow job fields are immutable",
    ):
        postgres_state._conn.execute(
            """UPDATE dual_agent_workflow_jobs
                  SET status='blocked'
                WHERE job_id=%s""",
            (row["job_id"],),
        )
    with pytest.raises(
        postgres_state._errors.RaiseException,
        match="terminal workflow job is immutable",
    ):
        postgres_state._conn.execute(
            """DELETE FROM dual_agent_workflow_jobs
                WHERE job_id=%s""",
            (row["job_id"],),
        )

    stored = postgres_state.get_dual_agent_workflow_job(job_id=row["job_id"])
    assert stored["status"] == "accepted"
    assert stored["terminal_status"] == "accepted"
    assert stored["terminal_outcome_json"] == json.dumps(
        outcome,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    assert stored["returncode"] == 0
    assert stored["error"] is None
    events = postgres_state.read_events_since(
        "run",
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == [
        "dual_agent_workflow_terminal_outcome",
        "dual_agent_workflow_terminal_discrepancy",
    ]


def test_postgres_recovery_point_claim_is_compare_and_set(postgres_state, tmp_path):
    row, created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="claim-phase-job",
        token="claim-phase-token",
    )
    assert created is True

    claimed = postgres_state.claim_dual_agent_workflow_job_recovery_point(
        job_id=row["job_id"],
        expected_recovery_point="reserved",
        claim_token="claim-1",
        claim_ttl_s=60,
    )
    denied = postgres_state.claim_dual_agent_workflow_job_recovery_point(
        job_id=row["job_id"],
        expected_recovery_point="reserved",
        claim_token="claim-2",
        claim_ttl_s=60,
    )

    assert claimed is not None
    assert claimed["recovery_claim_token"] == "claim-1"
    assert denied is None

    postgres_state._conn.execute(
        """UPDATE dual_agent_workflow_jobs
              SET recovery_claimed_at=0
            WHERE job_id=%s""",
        (row["job_id"],),
    )
    reclaimed = postgres_state.claim_dual_agent_workflow_job_recovery_point(
        job_id=row["job_id"],
        expected_recovery_point="reserved",
        claim_token="claim-3",
        claim_ttl_s=1,
    )

    assert reclaimed is not None
    assert reclaimed["recovery_claim_token"] == "claim-3"


def test_postgres_reap_claim_loses_to_heartbeat_and_other_reaper(
    postgres_state,
    tmp_path,
):
    def seed_spawned(job_id: str, pid: int) -> dict:
        job_dir = tmp_path / ".handoff" / "workflow-jobs" / job_id
        postgres_state.upsert_dual_agent_workflow_job(
            job_id=job_id,
            run_id=f"run-{job_id}",
            task_id=f"task-{job_id}",
            cwd=str(tmp_path),
            status="running",
            pid=pid,
            worker_pgid=pid,
            worker_started_at=float(pid),
            worker_containment_id=f"containment-{job_id}",
            request_path=str(job_dir / "request.json"),
            result_path=str(job_dir / "result.json"),
            log_path=str(job_dir / "worker.log"),
            recovery_point="spawned",
        )
        postgres_state.update_dual_agent_workflow_job(
            job_id=job_id,
            leased_by=f"worker:{pid}",
            lease_expires_at=999,
            heartbeat_at=999,
        )
        row = postgres_state.get_dual_agent_workflow_job(job_id=job_id)
        assert row is not None
        return row

    heartbeat_snapshot = seed_spawned("heartbeat-reap-race", 41020)
    assert postgres_state.heartbeat_dual_agent_workflow_job(
        job_id="heartbeat-reap-race",
        leased_by="worker:41020",
        lease_ttl_s=60,
        now=1000,
    )
    denied = postgres_state.claim_dual_agent_workflow_job_for_reap(
        job_id="heartbeat-reap-race",
        reaper_id="reaper:heartbeat-race",
        lease_ttl_s=60,
        now=1000,
        expected_leased_by=heartbeat_snapshot["leased_by"],
        expected_lease_expires_at=heartbeat_snapshot["lease_expires_at"],
        expected_heartbeat_at=heartbeat_snapshot["heartbeat_at"],
        expected_pid=heartbeat_snapshot["pid"],
        expected_worker_pgid=heartbeat_snapshot["worker_pgid"],
        expected_worker_started_at=heartbeat_snapshot["worker_started_at"],
        expected_worker_containment_id=heartbeat_snapshot[
            "worker_containment_id"
        ],
    )
    assert denied is None

    stale_snapshot = seed_spawned("two-reaper-race", 41021)
    barrier = Barrier(2)

    def claim(index: int) -> dict | None:
        state = PostgresState(
            postgres_state.dsn,
            schema=postgres_state.schema,
            apply_schema=False,
        )
        try:
            barrier.wait(timeout=5)
            return state.claim_dual_agent_workflow_job_for_reap(
                job_id="two-reaper-race",
                reaper_id=f"reaper:postgres-{index}",
                lease_ttl_s=60,
                now=1000,
                expected_leased_by=stale_snapshot["leased_by"],
                expected_lease_expires_at=stale_snapshot[
                    "lease_expires_at"
                ],
                expected_heartbeat_at=stale_snapshot["heartbeat_at"],
                expected_pid=stale_snapshot["pid"],
                expected_worker_pgid=stale_snapshot["worker_pgid"],
                expected_worker_started_at=stale_snapshot[
                    "worker_started_at"
                ],
                expected_worker_containment_id=stale_snapshot[
                    "worker_containment_id"
                ],
            )
        finally:
            state.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0]["leased_by"] in {
        "reaper:postgres-0",
        "reaper:postgres-1",
    }
    stored = postgres_state.get_dual_agent_workflow_job(
        job_id="two-reaper-race"
    )
    assert stored is not None
    assert stored["leased_by"] == winners[0]["leased_by"]
    assert stored["lease_expires_at"] == 1060
    assert stored["heartbeat_at"] == 1000
    _assert_postgres_spawn_ownership_survives_reap_retry_and_escalation(
        postgres_state,
        tmp_path,
    )


def _assert_postgres_spawn_ownership_survives_reap_retry_and_escalation(
    postgres_state,
    tmp_path,
) -> None:
    row, created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="postgres-spawn-ownership",
        token="postgres-spawn-ownership-token",
    )
    assert created is True
    postgres_state.update_dual_agent_workflow_job(
        job_id=row["job_id"],
        status="submitted",
        recovery_point="request_written",
    )
    claimed = postgres_state.claim_next_dual_agent_workflow_job_for_dispatch(
        dispatcher_id="dispatcher-before-reap",
        lease_ttl_s=60,
        now=1000,
        job_id=row["job_id"],
    )
    assert claimed is not None
    active_before_prepare = (
        postgres_state.count_active_dual_agent_workflow_job_leases(now=1001)
    )
    prepared = postgres_state.prepare_dual_agent_workflow_job_spawn(
        job_id=row["job_id"],
        dispatcher_id="dispatcher-before-reap",
        containment_id="containment-before-reap",
        lease_ttl_s=60,
        now=1000,
    )
    assert prepared is not None
    assert prepared["recovery_point"] == "spawn_prepared"
    assert (
        postgres_state.count_active_dual_agent_workflow_job_leases(now=1001)
        == active_before_prepare + 1
    )
    spawned = postgres_state.record_dual_agent_workflow_job_spawned(
        job_id=row["job_id"],
        dispatcher_id="dispatcher-before-reap",
        containment_id="containment-before-reap",
        pid=41030,
        worker_pgid=41030,
        worker_started_at=123.5,
        lease_ttl_s=60,
        now=1000,
    )
    assert spawned is not None
    postgres_state.record_dual_agent_workflow_worker_reaped(
        job_id=row["job_id"],
        worker_reaped_at=1001,
        termination={
            "status": "worker_tree_terminated",
            "safe_to_finalize": True,
            "pid": 41030,
            "pgid": 41030,
            "containment_id": "containment-before-reap",
        },
    )
    retried = postgres_state.reschedule_dual_agent_workflow_job_after_reap(
        job_id=row["job_id"],
        containment_id="containment-before-reap",
        dispatch_attempts=1,
        error="fast_exit_without_result",
    )
    assert retried is not None
    assert retried["recovery_point"] == "request_written"
    assert retried["pid"] == 41030
    assert retried["worker_reaped_at"] == 1001

    claimed_retry = (
        postgres_state.claim_next_dual_agent_workflow_job_for_dispatch(
            dispatcher_id="dispatcher-after-reap",
            lease_ttl_s=60,
            now=1002,
            job_id=row["job_id"],
        )
    )
    assert claimed_retry is not None
    prepared_retry = postgres_state.prepare_dual_agent_workflow_job_spawn(
        job_id=row["job_id"],
        dispatcher_id="dispatcher-after-reap",
        containment_id="containment-after-reap",
        lease_ttl_s=60,
        now=1002,
    )
    assert prepared_retry is not None
    assert prepared_retry["pid"] is None
    assert prepared_retry["worker_reaped_at"] is None
    assert (
        prepared_retry["worker_containment_id"]
        == "containment-after-reap"
    )

    deferred = prepared_retry
    for cleanup_attempt, now in enumerate((2000, 2001, 2002), start=1):
        deferred = postgres_state.defer_dual_agent_workflow_job_cleanup(
            job_id=row["job_id"],
            dispatcher_id="dispatcher-after-reap",
            containment_id="containment-after-reap",
            reason="containment_scan_incomplete",
            retry_delay_s=1,
            max_cleanup_retry_attempts=2,
            now=now,
        )
        assert deferred is not None
        assert deferred["cleanup_attempts"] == cleanup_attempt
        assert deferred["status"] == "running"
        assert deferred["parked_reason"] is None
    assert deferred["cleanup_escalated_at"] == 2002
    assert str(deferred["error"]).startswith(
        "cleanup_retry_attempts_exhausted"
    )


def test_postgres_concurrent_skip_locked_claimers_get_disjoint_jobs(postgres_state, tmp_path):
    dsn = postgres_state.dsn
    schema = postgres_state.schema
    for index in range(9):
        _reserve_job(
            postgres_state,
            tmp_path,
            job_id=f"claim-job-{index}",
            token=f"claim-token-{index}",
        )

    def claim(index: int) -> list[str]:
        state = PostgresState(dsn, schema=schema, apply_schema=False)
        try:
            rows = state.claim_dual_agent_workflow_jobs_for_dispatch(
                dispatcher_id=f"dispatcher-{index}",
                lease_ttl_s=60,
                now=1000,
                limit=3,
            )
            return [row["job_id"] for row in rows]
        finally:
            state.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        batches = list(pool.map(claim, range(3)))

    claimed = [job_id for batch in batches for job_id in batch]

    assert len(claimed) == 9
    assert len(set(claimed)) == 9
    assert all(len(batch) == 3 for batch in batches)


def test_postgres_claim_limit_is_bounded_by_cte(postgres_state, tmp_path):
    for index in range(5):
        _reserve_job(
            postgres_state,
            tmp_path,
            job_id=f"limit-job-{index}",
            token=f"limit-token-{index}",
        )

    claimed = postgres_state.claim_dual_agent_workflow_jobs_for_dispatch(
        dispatcher_id="dispatcher-limit",
        lease_ttl_s=60,
        now=1000,
        limit=2,
    )

    assert len(claimed) == 2

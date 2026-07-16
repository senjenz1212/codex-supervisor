from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/20260712_0004_event_idempotency_claims.py"
)


class _RecordingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(" ".join(statement.split()))


def _record_upgrade_sql() -> str:
    op = _RecordingOp()
    alembic = ModuleType("alembic")
    alembic.op = op
    with patch.dict(sys.modules, {"alembic": alembic}):
        migration = runpy.run_path(str(MIGRATION_PATH))
        migration["upgrade"]()
    return " ".join(op.statements)


def test_upgrade_locks_claims_then_events_before_backfill() -> None:
    sql = _record_upgrade_sql()

    claim_lock = sql.index(
        "LOCK TABLE event_idempotency_claims "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    event_lock = sql.index(
        "LOCK TABLE events IN SHARE ROW EXCLUSIVE MODE"
    )
    backfill = sql.index("INSERT INTO event_idempotency_claims")

    assert claim_lock < event_lock < backfill


def test_upgrade_prechecks_missing_source_event_hash_before_backfill() -> None:
    sql = _record_upgrade_sql()

    event_lock = sql.index(
        "LOCK TABLE events IN SHARE ROW EXCLUSIVE MODE"
    )
    precheck = sql.index(
        "production trace event lacks source_event_hash for "
        "idempotency backfill"
    )
    backfill = sql.index("INSERT INTO event_idempotency_claims")

    assert event_lock < precheck < backfill
    assert (
        "NULLIF( TRIM(payload_json->>'source_event_hash'), '' ) IS NULL"
        in sql
    )


def test_upgrade_validates_exact_claim_coverage_after_backfill() -> None:
    sql = _record_upgrade_sql()

    backfill = sql.index("INSERT INTO event_idempotency_claims")
    validation = sql.index(
        "production trace event lacks exactly one matching "
        "idempotency claim"
    )

    assert backfill < validation
    assert "SELECT COUNT(*) FROM event_idempotency_claims AS claim" in sql
    assert "claim.run_id = event.run_id" in sql
    assert "claim.kind = event.kind" in sql
    assert (
        "claim.idempotency_key = 'production-trace:' || "
        "(event.payload_json->>'source_event_hash')"
    ) in sql
    assert "claim.event_id = event.event_id" in sql
    assert "claim.source = event.source" in sql
    assert (
        "claim.payload_sha256 = event.canonical_payload_hash"
    ) in sql
    assert ") <> 1" in sql


def test_upgrade_enforces_claim_immutability_after_backfill() -> None:
    sql = _record_upgrade_sql()

    validation = sql.index(
        "production trace event lacks exactly one matching "
        "idempotency claim"
    )
    guard = sql.index(
        "CREATE OR REPLACE FUNCTION "
        "enforce_event_idempotency_claim_immutability"
    )

    assert validation < guard
    assert "OLD.event_id IS NULL AND NEW.event_id IS NOT NULL" in sql
    assert "event.run_id = NEW.run_id" in sql
    assert "event.event_id = NEW.event_id" in sql
    assert "event.kind = NEW.kind" in sql
    assert "event.source = NEW.source" in sql
    assert (
        "event.canonical_payload_hash = NEW.payload_sha256"
        in sql
    )
    assert "BEFORE UPDATE OR DELETE ON event_idempotency_claims" in sql
    assert "BEFORE TRUNCATE ON event_idempotency_claims" in sql
    assert "event idempotency claims are immutable" in sql

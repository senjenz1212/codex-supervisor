"""deduplicate authoritative logical events across writers

Revision ID: 20260712_0004
Revises: 20260712_0003
Create Date: 2026-07-13
"""
from __future__ import annotations

from alembic import op


revision = "20260712_0004"
down_revision = "20260712_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS event_idempotency_claims (
          run_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          event_id BIGINT,
          source TEXT NOT NULL,
          payload_sha256 TEXT NOT NULL,
          created_at BIGINT NOT NULL,
          PRIMARY KEY(run_id, kind, idempotency_key),
          UNIQUE(run_id, event_id)
        )
        """
    )
    # write_event_once claims the idempotency key before appending its event.
    # Lock in the same order so active claim-first writers can drain without
    # deadlocking, then fence legacy event-only writers through validation.
    op.execute(
        "LOCK TABLE event_idempotency_claims "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute("LOCK TABLE events IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        INSERT INTO event_idempotency_claims(
          run_id, kind, idempotency_key, event_id, source,
          payload_sha256, created_at
        )
        SELECT
          run_id,
          kind,
          'production-trace:' || (payload_json->>'source_event_hash'),
          event_id,
          source,
          canonical_payload_hash,
          ts
        FROM events
        WHERE kind = 'dual_agent_production_trace_recorded'
        ON CONFLICT(run_id, kind, idempotency_key) DO NOTHING
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM events
            WHERE kind = 'dual_agent_production_trace_recorded'
            GROUP BY run_id, kind, payload_json->>'source_event_hash'
            HAVING COUNT(*) > 1
          ) THEN
            RAISE EXCEPTION
              'duplicate production trace events require manual repair';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM events AS event
            WHERE event.kind = 'dual_agent_production_trace_recorded'
              AND (
                SELECT COUNT(*)
                FROM event_idempotency_claims AS claim
                WHERE claim.run_id = event.run_id
                  AND claim.kind = event.kind
                  AND claim.idempotency_key =
                    'production-trace:' ||
                    (event.payload_json->>'source_event_hash')
                  AND claim.event_id = event.event_id
                  AND claim.source = event.source
                  AND claim.payload_sha256 =
                    event.canonical_payload_hash
              ) <> 1
          ) THEN
            RAISE EXCEPTION
              'production trace event lacks exactly one matching idempotency claim';
          END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION
          enforce_event_idempotency_claim_immutability()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF OLD.event_id IS NULL
               AND NEW.event_id IS NOT NULL
               AND NEW.run_id IS NOT DISTINCT FROM OLD.run_id
               AND NEW.kind IS NOT DISTINCT FROM OLD.kind
               AND NEW.idempotency_key
                   IS NOT DISTINCT FROM OLD.idempotency_key
               AND NEW.source IS NOT DISTINCT FROM OLD.source
               AND NEW.payload_sha256
                   IS NOT DISTINCT FROM OLD.payload_sha256
               AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
               AND EXISTS (
                 SELECT 1
                 FROM events AS event
                 WHERE event.run_id = NEW.run_id
                   AND event.event_id = NEW.event_id
                   AND event.kind = NEW.kind
                   AND event.source = NEW.source
                   AND event.canonical_payload_hash =
                     NEW.payload_sha256
               ) THEN
              RETURN NEW;
            END IF;
          END IF;
          RAISE EXCEPTION 'event idempotency claims are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS event_idempotency_claims_no_truncate
          ON event_idempotency_claims
        """
    )
    op.execute(
        """
        CREATE TRIGGER event_idempotency_claims_no_truncate
        BEFORE TRUNCATE ON event_idempotency_claims
        FOR EACH STATEMENT EXECUTE FUNCTION
          enforce_event_idempotency_claim_immutability()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS event_idempotency_claims_immutable
          ON event_idempotency_claims
        """
    )
    op.execute(
        """
        CREATE TRIGGER event_idempotency_claims_immutable
        BEFORE UPDATE OR DELETE ON event_idempotency_claims
        FOR EACH ROW EXECUTE FUNCTION
          enforce_event_idempotency_claim_immutability()
        """
    )


def downgrade() -> None:
    raise RuntimeError("20260712_0004 is a forward-only migration")

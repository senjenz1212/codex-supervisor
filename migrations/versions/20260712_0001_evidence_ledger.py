"""tamper-evident event ledger and immutable quality audits

Revision ID: 20260712_0001
Revises: 20260610_0004
Create Date: 2026-07-12
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from supervisor.evidence_ledger import (
    GENESIS_KINDS,
    LEGACY_IMPORT_GENESIS,
    build_ledger_fields,
    prepare_event_payload,
)
from supervisor.quality_projection import (
    QUALITY_TREND_PROJECTION_EVENT,
    canonical_quality_trend_projection_row,
    quality_trend_projection_event_payload,
)


revision = "20260712_0001"
down_revision = "20260610_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS quality_trend_audits (
      run_id TEXT NOT NULL,
      gate TEXT NOT NULL,
      sample_size INTEGER NOT NULL,
      false_accept_count INTEGER NOT NULL,
      false_accept_denominator INTEGER NOT NULL,
      false_accept_rate DOUBLE PRECISION NOT NULL,
      audit_details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
      computed_at BIGINT NOT NULL,
      PRIMARY KEY(run_id, gate, computed_at),
      CONSTRAINT quality_trend_audits_counts_valid CHECK (
           sample_size >= 0
       AND false_accept_count >= 0
       AND false_accept_denominator >= 0
       AND false_accept_count <= false_accept_denominator
       AND false_accept_denominator <= sample_size
       AND false_accept_rate >= 0.0
       AND false_accept_rate <= 1.0
      ),
      CONSTRAINT quality_trend_audits_rate_exact CHECK (
        false_accept_rate = CASE
          WHEN false_accept_denominator = 0 THEN 0.0
          ELSE false_accept_count::double precision
               / false_accept_denominator::double precision
        END
      )
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_quality_trend_audits_run_gate
      ON quality_trend_audits(run_id, gate, computed_at)
    """)
    bind = op.get_bind()
    # Match the runtime writer order:
    # quality projection -> audit revision -> stream sequence -> event row.
    # Taking all four locks up front prevents a writer from holding a stream
    # sequence row while waiting on events as this migration waits in reverse.
    op.execute(
        "LOCK TABLE supervisor_quality_trends IN SHARE MODE"
    )
    op.execute(
        "LOCK TABLE quality_trend_audits IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(
        "LOCK TABLE event_stream_sequences IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute("LOCK TABLE events IN SHARE ROW EXCLUSIVE MODE")
    invalid_audit = bind.execute(sa.text(
        """SELECT run_id, gate
             FROM supervisor_quality_trends
            WHERE p11_audit_sample_size < 0
               OR false_accept_count < 0
               OR false_accept_denominator < 0
               OR false_accept_count > false_accept_denominator
               OR false_accept_denominator > p11_audit_sample_size
            LIMIT 1"""
    )).mappings().first()
    if invalid_audit is not None:
        raise RuntimeError(
            "invalid legacy quality audit counts: "
            f"run_id={invalid_audit['run_id']} gate={invalid_audit['gate']}"
        )
    conflicting_audit = bind.execute(sa.text(
        """SELECT trends.run_id, trends.gate, trends.computed_at
             FROM supervisor_quality_trends AS trends
             JOIN quality_trend_audits AS audits
               ON audits.run_id = trends.run_id
              AND audits.gate = trends.gate
              AND audits.computed_at = trends.computed_at
            WHERE (
                     trends.p11_audit_sample_size != 0
                  OR trends.false_accept_count != 0
                  OR trends.false_accept_denominator != 0
                  OR jsonb_typeof(
                       trends.details_json->'p11_audit'
                     ) = 'object'
                  )
              AND (
                     audits.sample_size
                       IS DISTINCT FROM trends.p11_audit_sample_size
                  OR audits.false_accept_count
                       IS DISTINCT FROM trends.false_accept_count
                  OR audits.false_accept_denominator
                       IS DISTINCT FROM trends.false_accept_denominator
                  OR audits.false_accept_rate IS DISTINCT FROM CASE
                       WHEN trends.false_accept_denominator = 0 THEN 0.0
                       ELSE trends.false_accept_count::double precision
                            / trends.false_accept_denominator::double precision
                     END
                  OR audits.audit_details_json IS DISTINCT FROM CASE
                       WHEN jsonb_typeof(
                              trends.details_json->'p11_audit'
                            ) = 'object'
                         THEN trends.details_json->'p11_audit'
                       ELSE '{}'::jsonb
                     END
                  )
            LIMIT 1"""
    )).mappings().first()
    if conflicting_audit is not None:
        raise RuntimeError(
            "conflicting immutable quality audit history: "
            f"run_id={conflicting_audit['run_id']} "
            f"gate={conflicting_audit['gate']} "
            f"computed_at={conflicting_audit['computed_at']}"
        )
    op.execute("""
    INSERT INTO quality_trend_audits(
      run_id, gate, sample_size, false_accept_count,
      false_accept_denominator, false_accept_rate,
      audit_details_json, computed_at
    )
    SELECT trends.run_id,
           trends.gate,
           trends.p11_audit_sample_size,
           trends.false_accept_count,
           trends.false_accept_denominator,
           CASE
             WHEN trends.false_accept_denominator = 0 THEN 0.0
             ELSE trends.false_accept_count::double precision
                  / trends.false_accept_denominator::double precision
           END,
           CASE
             WHEN jsonb_typeof(
                    trends.details_json->'p11_audit'
                  ) = 'object'
               THEN trends.details_json->'p11_audit'
             ELSE '{}'::jsonb
           END,
           trends.computed_at
      FROM supervisor_quality_trends AS trends
     WHERE (
              trends.p11_audit_sample_size != 0
           OR trends.false_accept_count != 0
           OR trends.false_accept_denominator != 0
           OR jsonb_typeof(
                trends.details_json->'p11_audit'
              ) = 'object'
           )
       AND NOT EXISTS (
             SELECT 1
               FROM quality_trend_audits AS audits
              WHERE audits.run_id = trends.run_id
                AND audits.gate = trends.gate
                AND audits.computed_at = trends.computed_at
           )
    """)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'quality_trend_audits_counts_valid'
           AND conrelid = 'quality_trend_audits'::regclass
      ) THEN
        ALTER TABLE quality_trend_audits
          ADD CONSTRAINT quality_trend_audits_counts_valid CHECK (
               sample_size >= 0
           AND false_accept_count >= 0
           AND false_accept_denominator >= 0
           AND false_accept_count <= false_accept_denominator
           AND false_accept_denominator <= sample_size
           AND false_accept_rate >= 0.0
           AND false_accept_rate <= 1.0
          );
      END IF;
      IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'quality_trend_audits_rate_exact'
           AND conrelid = 'quality_trend_audits'::regclass
      ) THEN
        ALTER TABLE quality_trend_audits
          ADD CONSTRAINT quality_trend_audits_rate_exact CHECK (
            false_accept_rate = CASE
              WHEN false_accept_denominator = 0 THEN 0.0
              ELSE false_accept_count::double precision
                   / false_accept_denominator::double precision
            END
          );
      END IF;
    END;
    $$;
    """)
    for statement in (
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS previous_event_hash TEXT",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS event_hash TEXT",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS canonical_payload_hash TEXT",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS artifact_manifest_hash TEXT",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS ledger_genesis_kind TEXT",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS event_sequence BIGINT",
    ):
        op.execute(statement)

    # This is an explicit operator-run Alembic maintenance step. Lock out
    # concurrent appenders, stream the historical rows once, preserve JSONB
    # payloads exactly, and add only ledger metadata.
    rows = bind.execute(
        sa.text(
            """SELECT global_id, event_id, run_id, ts, source, kind,
                      payload_json, event_sequence, previous_event_hash,
                      event_hash, canonical_payload_hash,
                      artifact_manifest_hash, ledger_genesis_kind
                 FROM events
                ORDER BY run_id ASC, event_id ASC"""
        )
    ).mappings()
    current_run_id: str | None = None
    event_sequence = 0
    previous_event_hash: str | None = None
    first_genesis = LEGACY_IMPORT_GENESIS
    for row in rows:
        run_id = str(row["run_id"])
        if run_id != current_run_id:
            current_run_id = run_id
            event_sequence = 0
            previous_event_hash = None
            observed_genesis = row["ledger_genesis_kind"]
            first_genesis = (
                str(observed_genesis)
                if observed_genesis in GENESIS_KINDS
                else LEGACY_IMPORT_GENESIS
            )
        event_sequence += 1
        payload = row["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise RuntimeError(
                "event payload must be an object during ledger migration: "
                f"run_id={run_id} event_id={row['event_id']}"
            )
        normalized_payload = prepare_event_payload(
            run_id=run_id,
            source=str(row["source"]),
            kind=str(row["kind"]),
            payload=payload,
        )
        if normalized_payload != payload:
            raise RuntimeError(
                "legacy event payload requires redaction or trace "
                "normalization; refusing to rewrite historical evidence: "
                f"run_id={run_id} event_id={row['event_id']}"
            )
        fields = build_ledger_fields(
            run_id=run_id,
            event_sequence=event_sequence,
            ts=int(row["ts"]),
            source=str(row["source"]),
            kind=str(row["kind"]),
            payload=payload,
            previous_event_hash=previous_event_hash,
            ledger_genesis_kind=(
                first_genesis if event_sequence == 1 else None
            ),
        )
        expected_metadata = {
            "event_sequence": fields.event_sequence,
            "previous_event_hash": fields.previous_event_hash,
            "event_hash": fields.event_hash,
            "canonical_payload_hash": fields.canonical_payload_hash,
            "artifact_manifest_hash": fields.artifact_manifest_hash,
            "ledger_genesis_kind": fields.ledger_genesis_kind,
        }
        for field, expected_value in expected_metadata.items():
            observed_value = row[field]
            if observed_value is None:
                continue
            if field == "event_sequence":
                observed_value = int(observed_value)
            else:
                observed_value = str(observed_value)
            if observed_value != expected_value:
                raise RuntimeError(
                    "conflicting pre-populated event ledger metadata: "
                    f"run_id={run_id} event_id={row['event_id']} "
                    f"field={field}"
                )
        bind.execute(
            sa.text(
                """UPDATE events
                      SET event_sequence=:event_sequence,
                          previous_event_hash=:previous_event_hash,
                          event_hash=:event_hash,
                          canonical_payload_hash=:canonical_payload_hash,
                          artifact_manifest_hash=:artifact_manifest_hash,
                          ledger_genesis_kind=:ledger_genesis_kind
                    WHERE global_id=:global_id"""
            ),
            {
                "event_sequence": fields.event_sequence,
                "previous_event_hash": fields.previous_event_hash,
                "event_hash": fields.event_hash,
                "canonical_payload_hash": fields.canonical_payload_hash,
                "artifact_manifest_hash": fields.artifact_manifest_hash,
                "ledger_genesis_kind": fields.ledger_genesis_kind,
                "global_id": int(row["global_id"]),
            },
        )
        previous_event_hash = fields.event_hash

    _backfill_quality_projection_evidence(bind)

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_hash ON events(event_hash)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_run_sequence "
        "ON events(run_id, event_sequence)"
    )
    op.execute("ALTER TABLE events ALTER COLUMN event_sequence SET NOT NULL")
    op.execute("ALTER TABLE events ALTER COLUMN event_hash SET NOT NULL")
    op.execute(
        "ALTER TABLE events ALTER COLUMN canonical_payload_hash SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE events ALTER COLUMN artifact_manifest_hash SET NOT NULL"
    )
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'events_run_sequence_unique'
           AND conrelid = 'events'::regclass
      ) THEN
        ALTER TABLE events
          ADD CONSTRAINT events_run_sequence_unique
          UNIQUE(run_id, event_sequence);
      END IF;
      IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'events_sequence_positive'
           AND conrelid = 'events'::regclass
      ) THEN
        ALTER TABLE events
          ADD CONSTRAINT events_sequence_positive
          CHECK(event_sequence > 0);
      END IF;
      IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'events_genesis_hash_shape'
           AND conrelid = 'events'::regclass
      ) THEN
        ALTER TABLE events
          ADD CONSTRAINT events_genesis_hash_shape CHECK (
               (previous_event_hash IS NULL AND ledger_genesis_kind IN ('native', 'legacy-import'))
            OR (previous_event_hash IS NOT NULL AND ledger_genesis_kind IS NULL)
          );
      END IF;
    END;
    $$;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION reject_event_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      RAISE EXCEPTION 'events are append-only';
    END;
    $$;
    """)
    op.execute("DROP TRIGGER IF EXISTS events_no_update ON events")
    op.execute("""
    CREATE TRIGGER events_no_update
    BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION reject_event_mutation()
    """)
    op.execute("DROP TRIGGER IF EXISTS events_no_delete ON events")
    op.execute("""
    CREATE TRIGGER events_no_delete
    BEFORE DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION reject_event_mutation()
    """)
    op.execute("DROP TRIGGER IF EXISTS events_no_truncate ON events")
    op.execute("""
    CREATE TRIGGER events_no_truncate
    BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_event_mutation()
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION reject_quality_trend_audit_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      RAISE EXCEPTION 'quality trend audits are immutable';
    END;
    $$;
    """)
    op.execute(
        "DROP TRIGGER IF EXISTS quality_trend_audits_no_update "
        "ON quality_trend_audits"
    )
    op.execute("""
    CREATE TRIGGER quality_trend_audits_no_update
    BEFORE UPDATE ON quality_trend_audits
    FOR EACH ROW EXECUTE FUNCTION reject_quality_trend_audit_mutation()
    """)
    op.execute(
        "DROP TRIGGER IF EXISTS quality_trend_audits_no_delete "
        "ON quality_trend_audits"
    )
    op.execute("""
    CREATE TRIGGER quality_trend_audits_no_delete
    BEFORE DELETE ON quality_trend_audits
    FOR EACH ROW EXECUTE FUNCTION reject_quality_trend_audit_mutation()
    """)
    op.execute(
        "DROP TRIGGER IF EXISTS quality_trend_audits_no_truncate "
        "ON quality_trend_audits"
    )
    op.execute("""
    CREATE TRIGGER quality_trend_audits_no_truncate
    BEFORE TRUNCATE ON quality_trend_audits
    FOR EACH STATEMENT EXECUTE FUNCTION reject_quality_trend_audit_mutation()
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION reject_terminal_workflow_job_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'workflow jobs are immutable evidence';
      END IF;
      IF TG_OP = 'DELETE' THEN
        IF OLD.terminal_outcome_json IS NOT NULL
           OR OLD.recovery_point = 'terminal'
           OR OLD.status IN (
                'accepted', 'blocked', 'cancelled', 'completed',
                'denied', 'failed'
              )
        THEN
          RAISE EXCEPTION 'terminal workflow job is immutable';
        END IF;
        RETURN OLD;
      END IF;
      IF OLD.terminal_outcome_json IS NOT NULL
         AND (
              NEW.job_id IS DISTINCT FROM OLD.job_id
           OR NEW.run_id IS DISTINCT FROM OLD.run_id
           OR NEW.task_id IS DISTINCT FROM OLD.task_id
           OR NEW.result_path IS DISTINCT FROM OLD.result_path
           OR NEW.status IS DISTINCT FROM OLD.status
           OR NEW.recovery_point IS DISTINCT FROM OLD.recovery_point
           OR NEW.terminal_status IS DISTINCT FROM OLD.terminal_status
           OR NEW.terminal_outcome_json
                IS DISTINCT FROM OLD.terminal_outcome_json
           OR NEW.terminal_outcome_recorded_at
                IS DISTINCT FROM OLD.terminal_outcome_recorded_at
           OR NEW.returncode IS DISTINCT FROM OLD.returncode
           OR NEW.error IS DISTINCT FROM OLD.error
         )
      THEN
        RAISE EXCEPTION 'terminal workflow job fields are immutable';
      END IF;
      RETURN NEW;
    END;
    $$;
    """)
    op.execute(
        "DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_terminal_freeze "
        "ON dual_agent_workflow_jobs"
    )
    op.execute("""
    CREATE TRIGGER dual_agent_workflow_jobs_terminal_freeze
    BEFORE UPDATE ON dual_agent_workflow_jobs
    FOR EACH ROW EXECUTE FUNCTION reject_terminal_workflow_job_mutation()
    """)
    op.execute(
        "DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_terminal_no_delete "
        "ON dual_agent_workflow_jobs"
    )
    op.execute("""
    CREATE TRIGGER dual_agent_workflow_jobs_terminal_no_delete
    BEFORE DELETE ON dual_agent_workflow_jobs
    FOR EACH ROW EXECUTE FUNCTION reject_terminal_workflow_job_mutation()
    """)
    op.execute(
        "DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_no_truncate "
        "ON dual_agent_workflow_jobs"
    )
    op.execute("""
    CREATE TRIGGER dual_agent_workflow_jobs_no_truncate
    BEFORE TRUNCATE ON dual_agent_workflow_jobs
    FOR EACH STATEMENT EXECUTE FUNCTION reject_terminal_workflow_job_mutation()
    """)


def _backfill_quality_projection_evidence(bind: sa.engine.Connection) -> None:
    imported_at = int(
        bind.execute(
            sa.text(
                "SELECT EXTRACT(EPOCH FROM clock_timestamp())::bigint"
            )
        ).scalar_one()
    )
    rows = bind.execute(
        sa.text(
            """SELECT run_id, task_id, task_class, gate, accepted,
                      first_pass_accepted, revision_rounds,
                      time_to_accepted_outcome_s, p11_audit_sample_size,
                      false_accept_count, false_accept_denominator,
                      false_accept_rate, policy_overlay_hash,
                      policy_proposal_id, details_json, computed_at
                 FROM supervisor_quality_trends
                ORDER BY run_id ASC, gate ASC"""
        )
    ).mappings()
    for row in rows:
        details = row["details_json"]
        if isinstance(details, str):
            details = json.loads(details)
        projection_row = canonical_quality_trend_projection_row(
            {
                "run_id": str(row["run_id"]),
                "task_id": str(row["task_id"]),
                "task_class": str(row["task_class"]),
                "gate": str(row["gate"]),
                "accepted": bool(row["accepted"]),
                "first_pass_accepted": bool(
                    row["first_pass_accepted"]
                ),
                "revision_rounds": int(row["revision_rounds"] or 0),
                "time_to_accepted_outcome_s": (
                    float(row["time_to_accepted_outcome_s"])
                    if row["time_to_accepted_outcome_s"] is not None
                    else None
                ),
                "p11_audit_sample_size": int(
                    row["p11_audit_sample_size"] or 0
                ),
                "false_accept_count": int(
                    row["false_accept_count"] or 0
                ),
                "false_accept_denominator": int(
                    row["false_accept_denominator"] or 0
                ),
                "false_accept_rate": float(
                    row["false_accept_rate"] or 0.0
                ),
                "policy_overlay_hash": str(
                    row["policy_overlay_hash"] or ""
                ),
                "policy_proposal_id": str(
                    row["policy_proposal_id"] or ""
                ),
                "details": details if isinstance(details, dict) else {},
                "computed_at": int(row["computed_at"] or 0),
            }
        )
        payload = quality_trend_projection_event_payload(projection_row)
        payload_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        latest = bind.execute(
            sa.text(
                """SELECT payload_json
                     FROM events
                    WHERE run_id=:run_id
                      AND kind=:kind
                      AND payload_json->'projection_row'->>'gate'=:gate
                    ORDER BY event_sequence DESC
                    LIMIT 1"""
            ),
            {
                "run_id": projection_row["run_id"],
                "kind": QUALITY_TREND_PROJECTION_EVENT,
                "gate": projection_row["gate"],
            },
        ).mappings().first()
        if latest is not None:
            observed = latest["payload_json"]
            if isinstance(observed, str):
                observed = json.loads(observed)
            if observed == payload:
                continue

        head = bind.execute(
            sa.text(
                """SELECT event_id, event_sequence, event_hash
                     FROM events
                    WHERE run_id=:run_id
                    ORDER BY event_sequence DESC
                    LIMIT 1"""
            ),
            {"run_id": projection_row["run_id"]},
        ).mappings().first()
        event_id = int(head["event_id"]) + 1 if head is not None else 1
        event_sequence = (
            int(head["event_sequence"]) + 1 if head is not None else 1
        )
        previous_event_id = (
            int(head["event_id"]) if head is not None else None
        )
        previous_event_hash = (
            str(head["event_hash"]) if head is not None else None
        )
        fields = build_ledger_fields(
            run_id=projection_row["run_id"],
            event_sequence=event_sequence,
            ts=imported_at,
            source="schema_migration",
            kind=QUALITY_TREND_PROJECTION_EVENT,
            payload=payload,
            previous_event_hash=previous_event_hash,
            ledger_genesis_kind=(
                LEGACY_IMPORT_GENESIS
                if previous_event_hash is None
                else None
            ),
        )
        bind.execute(
            sa.text(
                """INSERT INTO events(
                     run_id, event_id, event_sequence, previous_event_id, ts,
                     source, kind, payload_json, previous_event_hash,
                     event_hash, canonical_payload_hash,
                     artifact_manifest_hash, ledger_genesis_kind)
                   VALUES(
                     :run_id, :event_id, :event_sequence,
                     :previous_event_id, :ts, :source, :kind,
                     CAST(:payload_json AS jsonb), :previous_event_hash,
                     :event_hash, :canonical_payload_hash,
                     :artifact_manifest_hash, :ledger_genesis_kind
                   )"""
            ),
            {
                "run_id": projection_row["run_id"],
                "event_id": event_id,
                "event_sequence": fields.event_sequence,
                "previous_event_id": previous_event_id,
                "ts": imported_at,
                "source": "schema_migration",
                "kind": QUALITY_TREND_PROJECTION_EVENT,
                "payload_json": payload_json,
                "previous_event_hash": fields.previous_event_hash,
                "event_hash": fields.event_hash,
                "canonical_payload_hash": fields.canonical_payload_hash,
                "artifact_manifest_hash": fields.artifact_manifest_hash,
                "ledger_genesis_kind": fields.ledger_genesis_kind,
            },
        )
        bind.execute(
            sa.text(
                """INSERT INTO event_stream_sequences(run_id, last_event_id)
                   VALUES(:run_id, :event_id)
                   ON CONFLICT(run_id) DO UPDATE
                     SET last_event_id=GREATEST(
                       event_stream_sequences.last_event_id,
                       EXCLUDED.last_event_id
                     )"""
            ),
            {
                "run_id": projection_row["run_id"],
                "event_id": event_id,
            },
        )


def downgrade() -> None:
    raise RuntimeError("20260712_0001 is a forward-only migration")

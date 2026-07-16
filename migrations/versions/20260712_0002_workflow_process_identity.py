"""persist detached workflow worker process identity

Revision ID: 20260712_0002
Revises: 20260712_0001
Create Date: 2026-07-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260712_0002"
down_revision = "20260712_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Fence dispatchers before checking quiescence. Without this lock a worker
    # can become live after the SELECT and before the identity columns exist.
    op.execute(
        "LOCK TABLE dual_agent_workflow_jobs IN SHARE ROW EXCLUSIVE MODE"
    )
    active_job = bind.execute(
        sa.text(
            """SELECT job_id, pid
                 FROM dual_agent_workflow_jobs
                WHERE pid IS NOT NULL
                  AND terminal_outcome_json IS NULL
                  AND recovery_point != 'terminal'
                  AND status NOT IN (
                        'accepted', 'blocked', 'cancelled', 'completed',
                        'denied', 'failed'
                      )
                ORDER BY job_id ASC
                LIMIT 1"""
        )
    ).mappings().first()
    if active_job is not None:
        raise RuntimeError(
            "workflow process-identity migration requires quiescence: "
            f"job_id={active_job['job_id']} pid={active_job['pid']} "
            "is potentially live; stop/reap pre-identity workers and clear "
            "pid or record a terminal outcome before retrying"
        )

    op.execute(
        "ALTER TABLE dual_agent_workflow_jobs "
        "ADD COLUMN IF NOT EXISTS worker_pgid INTEGER"
    )
    op.execute(
        "ALTER TABLE dual_agent_workflow_jobs "
        "ADD COLUMN IF NOT EXISTS worker_started_at DOUBLE PRECISION"
    )
    op.execute(
        "ALTER TABLE dual_agent_workflow_jobs "
        "ADD COLUMN IF NOT EXISTS worker_containment_id TEXT"
    )
    op.execute(
        "ALTER TABLE dual_agent_workflow_jobs "
        "ADD COLUMN IF NOT EXISTS worker_reaped_at BIGINT"
    )

    # Revision 0001 may have been stamped after only part of its DDL ran.
    # Reassert the audit table, exact-rate constraint, and mandatory
    # append-only triggers without rewriting any historical event.
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
           OR NEW.pid IS DISTINCT FROM OLD.pid
           OR NEW.worker_pgid IS DISTINCT FROM OLD.worker_pgid
           OR NEW.worker_started_at IS DISTINCT FROM OLD.worker_started_at
           OR NEW.worker_containment_id
                IS DISTINCT FROM OLD.worker_containment_id
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

    op.execute("""
    CREATE OR REPLACE FUNCTION reject_worker_reaped_at_rewrite()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      IF OLD.worker_reaped_at IS NOT NULL
         AND NEW.worker_reaped_at IS DISTINCT FROM OLD.worker_reaped_at
      THEN
        RAISE EXCEPTION 'worker_reaped_at is immutable once recorded';
      END IF;
      RETURN NEW;
    END;
    $$;
    """)
    op.execute(
        "DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_worker_reaped_once "
        "ON dual_agent_workflow_jobs"
    )
    op.execute("""
    CREATE TRIGGER dual_agent_workflow_jobs_worker_reaped_once
    BEFORE UPDATE ON dual_agent_workflow_jobs
    FOR EACH ROW EXECUTE FUNCTION reject_worker_reaped_at_rewrite()
    """)


def downgrade() -> None:
    raise RuntimeError("20260712_0002 is a forward-only migration")

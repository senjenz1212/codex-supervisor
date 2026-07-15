"""persist durable workflow worker spawn ownership

Revision ID: 20260715_0001
Revises: 20260712_0004
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "20260715_0001"
down_revision = "20260712_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "LOCK TABLE dual_agent_workflow_jobs IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(
        "ALTER TABLE dual_agent_workflow_jobs "
        "ADD COLUMN IF NOT EXISTS worker_prepared_at DOUBLE PRECISION"
    )
    op.execute(
        "ALTER TABLE dual_agent_workflow_jobs "
        "ADD COLUMN IF NOT EXISTS cleanup_attempts "
        "INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE dual_agent_workflow_jobs "
        "ADD COLUMN IF NOT EXISTS cleanup_escalated_at BIGINT"
    )
    op.execute(
        "DROP INDEX IF EXISTS idx_dual_agent_workflow_jobs_dispatchable"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS
          idx_dual_agent_workflow_jobs_dispatchable
        ON dual_agent_workflow_jobs(priority, created_at, id)
        WHERE recovery_point IN ('reserved', 'request_written')
          AND terminal_outcome_json IS NULL
          AND (pid IS NULL OR worker_reaped_at IS NOT NULL)
        """
    )
    op.execute(
        """
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
               OR NEW.worker_started_at
                    IS DISTINCT FROM OLD.worker_started_at
               OR NEW.worker_prepared_at
                    IS DISTINCT FROM OLD.worker_prepared_at
               OR NEW.worker_containment_id
                    IS DISTINCT FROM OLD.worker_containment_id
               OR NEW.cleanup_attempts
                    IS DISTINCT FROM OLD.cleanup_attempts
               OR NEW.cleanup_escalated_at
                    IS DISTINCT FROM OLD.cleanup_escalated_at
             )
          THEN
            RAISE EXCEPTION 'terminal workflow job fields are immutable';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_terminal_freeze "
        "ON dual_agent_workflow_jobs"
    )
    op.execute(
        """
        CREATE TRIGGER dual_agent_workflow_jobs_terminal_freeze
        BEFORE UPDATE ON dual_agent_workflow_jobs
        FOR EACH ROW EXECUTE FUNCTION
          reject_terminal_workflow_job_mutation()
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_terminal_no_delete "
        "ON dual_agent_workflow_jobs"
    )
    op.execute(
        """
        CREATE TRIGGER dual_agent_workflow_jobs_terminal_no_delete
        BEFORE DELETE ON dual_agent_workflow_jobs
        FOR EACH ROW EXECUTE FUNCTION
          reject_terminal_workflow_job_mutation()
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_no_truncate "
        "ON dual_agent_workflow_jobs"
    )
    op.execute(
        """
        CREATE TRIGGER dual_agent_workflow_jobs_no_truncate
        BEFORE TRUNCATE ON dual_agent_workflow_jobs
        FOR EACH STATEMENT EXECUTE FUNCTION
          reject_terminal_workflow_job_mutation()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_worker_reaped_at_rewrite()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF OLD.worker_reaped_at IS NOT NULL
             AND NEW.worker_reaped_at IS DISTINCT FROM OLD.worker_reaped_at
             AND NOT (
                  OLD.terminal_outcome_json IS NULL
              AND OLD.recovery_point = 'request_written'
              AND NEW.recovery_point = 'spawn_prepared'
              AND NEW.worker_reaped_at IS NULL
              AND NEW.pid IS NULL
              AND NEW.worker_pgid IS NULL
              AND NEW.worker_started_at IS NULL
              AND NEW.worker_containment_id IS NOT NULL
              AND NEW.worker_containment_id
                   IS DISTINCT FROM OLD.worker_containment_id
             )
          THEN
            RAISE EXCEPTION 'worker_reaped_at is immutable once recorded';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS dual_agent_workflow_jobs_worker_reaped_once "
        "ON dual_agent_workflow_jobs"
    )
    op.execute(
        """
        CREATE TRIGGER dual_agent_workflow_jobs_worker_reaped_once
        BEFORE UPDATE ON dual_agent_workflow_jobs
        FOR EACH ROW EXECUTE FUNCTION reject_worker_reaped_at_rewrite()
        """
    )


def downgrade() -> None:
    raise RuntimeError("20260715_0001 is a forward-only migration")

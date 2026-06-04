"""postgres event/job lane

Revision ID: 20260604_0001
Revises:
Create Date: 2026-06-04
"""
from __future__ import annotations

from alembic import op


revision = "20260604_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      applied_at BIGINT NOT NULL
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS event_stream_sequences (
      run_id TEXT PRIMARY KEY,
      last_event_id BIGINT NOT NULL
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS events (
      global_id BIGSERIAL PRIMARY KEY,
      run_id TEXT NOT NULL,
      event_id BIGINT NOT NULL,
      previous_event_id BIGINT,
      ts BIGINT NOT NULL,
      source TEXT NOT NULL,
      kind TEXT NOT NULL,
      payload_json JSONB NOT NULL,
      CONSTRAINT events_run_event_unique UNIQUE(run_id, event_id),
      CONSTRAINT events_previous_id_shape CHECK (
           (event_id = 1 AND previous_event_id IS NULL)
        OR (event_id > 1 AND previous_event_id = event_id - 1)
      )
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_run_event ON events(run_id, event_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_run_ts ON events(run_id, ts)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_global_id ON events(global_id)")
    op.execute("""
    CREATE TABLE IF NOT EXISTS tail_offsets (
      path TEXT PRIMARY KEY,
      byte_offset BIGINT NOT NULL,
      updated_at BIGINT NOT NULL
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS dual_agent_workflows (
      run_id TEXT NOT NULL,
      task_id TEXT NOT NULL,
      cwd TEXT NOT NULL,
      intent TEXT NOT NULL,
      current_gate TEXT,
      status TEXT NOT NULL,
      max_rounds_per_gate INTEGER NOT NULL,
      user_facing BOOLEAN NOT NULL,
      created_at BIGINT NOT NULL,
      updated_at BIGINT NOT NULL,
      PRIMARY KEY(run_id, task_id)
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_dual_agent_workflows_status
      ON dual_agent_workflows(status, updated_at)
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS dual_agent_workflow_steps (
      id BIGSERIAL PRIMARY KEY,
      run_id TEXT NOT NULL,
      task_id TEXT NOT NULL,
      gate TEXT NOT NULL,
      status TEXT NOT NULL,
      attempt_count INTEGER NOT NULL,
      latest_event_id BIGINT,
      created_at BIGINT NOT NULL,
      updated_at BIGINT NOT NULL,
      UNIQUE(run_id, task_id, gate)
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_steps_task
      ON dual_agent_workflow_steps(run_id, task_id, gate)
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS dual_agent_workflow_jobs (
      id BIGSERIAL UNIQUE,
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
      priority INTEGER NOT NULL DEFAULT 100,
      recovery_point TEXT NOT NULL DEFAULT 'reserved',
      recovery_claim_token TEXT,
      recovery_claimed_at BIGINT,
      leased_by TEXT,
      lease_expires_at BIGINT,
      heartbeat_at BIGINT,
      dispatch_attempts INTEGER NOT NULL DEFAULT 0,
      next_dispatch_at BIGINT,
      parked_reason TEXT,
      request_payload_json TEXT,
      config_path TEXT,
      terminal_status TEXT,
      terminal_outcome_json TEXT,
      terminal_outcome_recorded_at BIGINT,
      returncode INTEGER,
      error TEXT,
      created_at BIGINT NOT NULL,
      updated_at BIGINT NOT NULL
    )
    """)
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_active_idempotency_token
      ON dual_agent_workflow_jobs(idempotency_token)
      WHERE idempotency_token IS NOT NULL AND recovery_point != 'terminal'
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_task
      ON dual_agent_workflow_jobs(run_id, task_id, status)
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_dual_agent_workflow_jobs_dispatchable
      ON dual_agent_workflow_jobs(priority, created_at, id)
      WHERE recovery_point IN ('reserved', 'request_written')
        AND terminal_outcome_json IS NULL
        AND pid IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dual_agent_workflow_jobs_dispatchable")
    op.execute("DROP INDEX IF EXISTS idx_dual_agent_workflow_jobs_task")
    op.execute("DROP INDEX IF EXISTS idx_dual_agent_workflow_jobs_active_idempotency_token")
    op.execute("DROP TABLE IF EXISTS dual_agent_workflow_jobs")
    op.execute("DROP INDEX IF EXISTS idx_dual_agent_workflow_steps_task")
    op.execute("DROP TABLE IF EXISTS dual_agent_workflow_steps")
    op.execute("DROP INDEX IF EXISTS idx_dual_agent_workflows_status")
    op.execute("DROP TABLE IF EXISTS dual_agent_workflows")
    op.execute("DROP TABLE IF EXISTS tail_offsets")
    op.execute("DROP INDEX IF EXISTS idx_events_global_id")
    op.execute("DROP INDEX IF EXISTS idx_events_run_ts")
    op.execute("DROP INDEX IF EXISTS idx_events_run_event")
    op.execute("DROP TABLE IF EXISTS events")
    op.execute("DROP TABLE IF EXISTS event_stream_sequences")
    op.execute("DROP TABLE IF EXISTS schema_migrations")

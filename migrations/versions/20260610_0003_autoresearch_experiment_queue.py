"""supervisor autoresearch experiment queue

Revision ID: 20260610_0003
Revises: 20260610_0002
Create Date: 2026-06-10
"""
from __future__ import annotations

from alembic import op


revision = "20260610_0003"
down_revision = "20260610_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS supervisor_autoresearch_experiments (
      experiment_id TEXT PRIMARY KEY,
      signal_key TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL,
      task_class TEXT NOT NULL,
      gate TEXT NOT NULL,
      taxonomy_code TEXT NOT NULL,
      experiment_json JSONB NOT NULL,
      attempt_json JSONB NOT NULL,
      provenance_json JSONB NOT NULL,
      report_only_reason TEXT NOT NULL DEFAULT '',
      proposal_pointer_json JSONB NOT NULL DEFAULT '{}'::jsonb,
      report_ref TEXT NOT NULL DEFAULT '',
      report_sha256 TEXT NOT NULL DEFAULT '',
      last_run_id TEXT NOT NULL DEFAULT '',
      last_run_started_at BIGINT,
      created_at BIGINT NOT NULL,
      updated_at BIGINT NOT NULL,
      activated_at BIGINT,
      activated_by TEXT,
      activation_channel TEXT
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_supervisor_autoresearch_experiments_status
      ON supervisor_autoresearch_experiments(status, updated_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_supervisor_autoresearch_experiments_status")
    op.execute("DROP TABLE IF EXISTS supervisor_autoresearch_experiments")

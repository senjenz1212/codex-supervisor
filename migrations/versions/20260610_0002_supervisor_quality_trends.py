"""supervisor quality trends

Revision ID: 20260610_0002
Revises: 20260610_0001
Create Date: 2026-06-10
"""
from __future__ import annotations

from alembic import op


revision = "20260610_0002"
down_revision = "20260610_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS supervisor_quality_trends (
      id BIGSERIAL PRIMARY KEY,
      run_id TEXT NOT NULL,
      task_id TEXT NOT NULL,
      task_class TEXT NOT NULL,
      gate TEXT NOT NULL,
      accepted BOOLEAN NOT NULL,
      first_pass_accepted BOOLEAN NOT NULL,
      revision_rounds INTEGER NOT NULL,
      time_to_accepted_outcome_s DOUBLE PRECISION,
      p11_audit_sample_size INTEGER NOT NULL DEFAULT 0,
      false_accept_count INTEGER NOT NULL DEFAULT 0,
      false_accept_denominator INTEGER NOT NULL DEFAULT 0,
      false_accept_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
      details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
      computed_at BIGINT NOT NULL,
      UNIQUE(run_id, gate)
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_supervisor_quality_trends_task_gate
      ON supervisor_quality_trends(task_class, gate, computed_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_supervisor_quality_trends_task_gate")
    op.execute("DROP TABLE IF EXISTS supervisor_quality_trends")

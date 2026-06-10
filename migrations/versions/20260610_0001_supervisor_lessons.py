"""supervisor lessons

Revision ID: 20260610_0001
Revises: 20260604_0001
Create Date: 2026-06-10
"""
from __future__ import annotations

from alembic import op


revision = "20260610_0001"
down_revision = "20260604_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS supervisor_lessons (
      lesson_id TEXT PRIMARY KEY,
      task_class TEXT NOT NULL,
      gate TEXT NOT NULL,
      taxonomy_code TEXT NOT NULL,
      root_cause TEXT NOT NULL,
      remediation TEXT NOT NULL,
      source_run_id TEXT NOT NULL,
      created_at BIGINT NOT NULL
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_supervisor_lessons_task_gate
      ON supervisor_lessons(task_class, gate, created_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_supervisor_lessons_task_gate")
    op.execute("DROP TABLE IF EXISTS supervisor_lessons")

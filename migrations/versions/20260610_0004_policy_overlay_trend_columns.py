"""policy overlay trend attribution columns

Revision ID: 20260610_0004
Revises: 20260610_0003
Create Date: 2026-06-10
"""
from __future__ import annotations

from alembic import op


revision = "20260610_0004"
down_revision = "20260610_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE supervisor_quality_trends "
        "ADD COLUMN IF NOT EXISTS policy_overlay_hash TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE supervisor_quality_trends "
        "ADD COLUMN IF NOT EXISTS policy_proposal_id TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE supervisor_lessons "
        "ADD COLUMN IF NOT EXISTS normalized_key TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE supervisor_lessons "
        "ADD COLUMN IF NOT EXISTS observed_count INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE supervisor_lessons "
        "ADD COLUMN IF NOT EXISTS injection_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE supervisor_lessons "
        "ADD COLUMN IF NOT EXISTS recurrence_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE supervisor_lessons "
        "ADD COLUMN IF NOT EXISTS retired_at BIGINT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE supervisor_lessons DROP COLUMN IF EXISTS retired_at")
    op.execute("ALTER TABLE supervisor_lessons DROP COLUMN IF EXISTS recurrence_count")
    op.execute("ALTER TABLE supervisor_lessons DROP COLUMN IF EXISTS injection_count")
    op.execute("ALTER TABLE supervisor_lessons DROP COLUMN IF EXISTS observed_count")
    op.execute("ALTER TABLE supervisor_lessons DROP COLUMN IF EXISTS normalized_key")
    op.execute("ALTER TABLE supervisor_quality_trends DROP COLUMN IF EXISTS policy_proposal_id")
    op.execute("ALTER TABLE supervisor_quality_trends DROP COLUMN IF EXISTS policy_overlay_hash")

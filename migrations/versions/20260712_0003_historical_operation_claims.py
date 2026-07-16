"""coordinate audited historical operations across processes

Revision ID: 20260712_0003
Revises: 20260712_0002
Create Date: 2026-07-12
"""
from __future__ import annotations

from alembic import op


revision = "20260712_0003"
down_revision = "20260712_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_operation_claims (
          operation_id TEXT PRIMARY KEY,
          request_hash TEXT NOT NULL,
          operation TEXT NOT NULL
            CHECK(operation IN ('rerun', 'regrade', 'replay')),
          status TEXT NOT NULL
            CHECK(status IN ('running', 'completed', 'failed')),
          terminal_event_id BIGINT,
          created_at BIGINT NOT NULL,
          updated_at BIGINT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_historical_operation_claims_status
          ON historical_operation_claims(status, updated_at)
        """
    )


def downgrade() -> None:
    raise RuntimeError("20260712_0003 is a forward-only migration")

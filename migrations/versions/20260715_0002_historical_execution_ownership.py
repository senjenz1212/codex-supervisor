"""persist fenced ownership for historical execution claims

Revision ID: 20260715_0002
Revises: 20260715_0001
Create Date: 2026-07-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260715_0002"
down_revision = "20260715_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("LOCK TABLE historical_operation_claims IN SHARE ROW EXCLUSIVE MODE")
    running_claim = bind.execute(
        sa.text(
            """SELECT operation_id
                 FROM historical_operation_claims
                WHERE status='running'
                ORDER BY operation_id ASC
                LIMIT 1"""
        )
    ).mappings().first()
    if running_claim is not None:
        raise RuntimeError(
            "historical execution-ownership migration requires quiescence: "
            f"operation_id={running_claim['operation_id']} is running; "
            "complete or fail every historical operation before retrying"
        )
    op.execute(
        "ALTER TABLE historical_operation_claims "
        "ADD COLUMN IF NOT EXISTS execution_owner_token TEXT"
    )
    op.execute(
        "ALTER TABLE historical_operation_claims "
        "ADD COLUMN IF NOT EXISTS execution_generation "
        "INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE historical_operation_claims "
        "ADD COLUMN IF NOT EXISTS execution_heartbeat_at DOUBLE PRECISION"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
          idx_historical_operation_claims_execution_owner
        ON historical_operation_claims(execution_owner_token)
        WHERE execution_owner_token IS NOT NULL
        """
    )


def downgrade() -> None:
    raise RuntimeError("20260715_0002 is a forward-only migration")

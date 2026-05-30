"""workspace soft delete columns and is_system flag

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "workspaces",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column(
        "workspaces",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("scheduled_purge_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Mark the oldest workspace named 'default' as the system workspace
    op.execute(
        """
        UPDATE workspaces
        SET is_system = TRUE
        WHERE LOWER(name) = 'default'
          AND id = (
            SELECT id FROM workspaces
            WHERE LOWER(name) = 'default'
            ORDER BY created_at ASC
            LIMIT 1
          )
        """
    )

    # Seed the workspace_inactive_grace_period_days system setting if absent
    op.execute(
        """
        INSERT INTO system_settings (key, value, updated_at)
        VALUES ('workspace_inactive_grace_period_days', 30, NOW())
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_column("workspaces", "scheduled_purge_at")
    op.drop_column("workspaces", "deleted_at")
    op.drop_column("workspaces", "status")
    op.drop_column("workspaces", "is_system")

"""Add webhook retry tracking columns.

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-25

Changes
-------
* ``webhooks.failure_count`` (INTEGER NOT NULL DEFAULT 0) — rolling count of
  consecutive delivery failures.  Reset to 0 on any successful delivery.
* ``webhooks.disabled_at`` (TIMESTAMPTZ) — set when BookScout auto-disables a
  dead endpoint (failure_count reaches DEAD_THRESHOLD).  NULL means the webhook
  has never been auto-disabled.

These two columns support the v0.47.0 exponential-backoff retry logic and dead
endpoint detection in ``api/v1/webhooks.deliver_event()``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "webhooks",
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "webhooks",
        sa.Column("disabled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhooks", "disabled_at")
    op.drop_column("webhooks", "failure_count")

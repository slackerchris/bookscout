"""Add watchlist.auto_download.

Per-author opt-in for automatic downloading: when True, each scan checks the
author's HIGH-confidence, released, missing books against the indexers and
either sends the best match to the download client or queues it for approval
(global behavior in the download_preferences setting: auto_download_mode).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "watchlist",
        sa.Column("auto_download", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("watchlist", "auto_download")

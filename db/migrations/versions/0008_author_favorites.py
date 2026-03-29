"""Add favorite column to watchlist table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-28

Changes
-------
* ``watchlist.favorite`` (BOOLEAN NOT NULL DEFAULT false) — lets the UI mark
  authors as favourites without relying on browser localStorage.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "watchlist",
        sa.Column("favorite", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("watchlist", "favorite")

"""Add books.primary_author_manual.

When True, the user has explicitly chosen the primary author (via
PATCH /books/{id}) and scans must never reassign it.  When False, scans
apply "billing order wins": the linked author with the lowest author_order
(top billing in the source metadata) becomes primary.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("primary_author_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("books", "primary_author_manual")

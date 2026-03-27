"""Add narrator column to books table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-26

Changes
-------
* ``books.narrator`` (TEXT, nullable) — plain-text narrator credit string,
  e.g. ``"Ray Porter"`` or ``"Ray Porter, Julia Whelan"``.

  Populated by the scan pipeline from the Audible/Audnexus API ``narrators``
  field.  Narrators are never written to the ``authors`` table.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("books", sa.Column("narrator", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("books", "narrator")

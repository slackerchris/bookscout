"""Add language column to books table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-25

Changes
-------
* ``books.language`` (TEXT, nullable) — ISO 639-1 language code stored at scan
  time from the metadata source (e.g. ``"en"``, ``"de"``).  Populated by
  ``core/scan.py`` going forward; existing rows are NULL until re-scanned.

Enables the ``GET /api/v1/authors/{id}/languages`` endpoint introduced in
v0.48.0 which returns a per-language count breakdown for a given author's
catalog.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("books", sa.Column("language", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("books", "language")

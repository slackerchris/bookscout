"""Add indexes on books.asin and books.isbn.

These columns are queried per-book by _find_existing_book() during every scan
(Phase-1 identifier lookup) but had no index — asin's only index was dropped
along with the unique constraint in 0003, and isbn was never indexed.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_books_asin", "books", ["asin"])
    op.create_index("ix_books_isbn", "books", ["isbn"])


def downgrade() -> None:
    op.drop_index("ix_books_isbn", table_name="books")
    op.drop_index("ix_books_asin", table_name="books")

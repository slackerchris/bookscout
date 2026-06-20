"""Add primary author and canonical book tracking.

- book_authors.author_order  — position in the source API's authors array (0 = first-billed)
- books.primary_author_id    — canonical primary author FK; auto-derived from author_order,
                               overridable by the user via the management UI
- books.canonical_book_id    — when set, this book is a duplicate of the referenced book and
                               should be excluded from list queries

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # author_order: position in Audible/source authors array; NULL for pre-existing rows
    op.add_column("book_authors", sa.Column("author_order", sa.Integer(), nullable=True))

    # primary_author_id: the one author this book "belongs to" in list views
    op.add_column(
        "books",
        sa.Column(
            "primary_author_id",
            sa.Integer(),
            sa.ForeignKey("authors.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # canonical_book_id: when non-NULL this row is a duplicate; queries exclude it
    op.add_column(
        "books",
        sa.Column(
            "canonical_book_id",
            sa.Integer(),
            sa.ForeignKey("books.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Backfill primary_author_id for all existing non-deleted books.
    # Default: alphabetically first (by name_sort) author with role='author'.
    # Users can correct outliers via the management UI.
    op.execute(
        """
        UPDATE books
        SET primary_author_id = (
            SELECT ba.author_id
            FROM book_authors ba
            JOIN authors a ON a.id = ba.author_id
            WHERE ba.book_id = books.id
              AND ba.role = 'author'
            ORDER BY a.name_sort ASC
            LIMIT 1
        )
        WHERE primary_author_id IS NULL
          AND deleted = false
        """
    )

    op.create_index("ix_books_primary_author_id", "books", ["primary_author_id"])
    op.create_index("ix_books_canonical_book_id", "books", ["canonical_book_id"])


def downgrade() -> None:
    op.drop_index("ix_books_canonical_book_id", table_name="books")
    op.drop_index("ix_books_primary_author_id", table_name="books")
    op.drop_column("books", "canonical_book_id")
    op.drop_column("books", "primary_author_id")
    op.drop_column("book_authors", "author_order")

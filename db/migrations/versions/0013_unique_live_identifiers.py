"""Merge live duplicate books, then enforce identifier uniqueness.

Concurrent scans of two co-authors could both insert the same book (the
check-then-insert in _find_existing_book has no DB backstop since the plain
unique constraint was dropped in 0003).  This migration:

1. Merges existing live duplicates that share an asin or isbn13 — the lowest
   id becomes canonical, the others get canonical_book_id set (the app's own
   soft-merge mechanism), their author links are copied to the canonical row,
   and ownership is propagated.  No rows are deleted.
2. Creates PARTIAL unique indexes that only cover live rows
   (canonical_book_id IS NULL AND deleted IS FALSE), so soft-deleted rows and
   merged duplicates are exempt — addressing the false-violation concern that
   led 0003 to drop the plain constraint.

The insert race is then handled in core/scan.py: the losing scan catches the
IntegrityError, re-finds the winner's row, and links to it instead.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LIVE = "canonical_book_id IS NULL AND deleted IS FALSE"


def upgrade() -> None:
    conn = op.get_bind()

    for col in ("asin", "isbn13"):
        dupes = conn.execute(
            text(
                f"""
                SELECT {col}, array_agg(id ORDER BY id) AS ids
                FROM books
                WHERE {col} IS NOT NULL AND {_LIVE}
                GROUP BY {col}
                HAVING COUNT(*) > 1
                """
            )
        ).fetchall()

        for row in dupes:
            ids: list[int] = list(row[1])
            canonical_id = ids[0]
            for dup_id in ids[1:]:
                # Copy author links so the canonical book still appears under
                # every credited author.
                conn.execute(
                    text(
                        """
                        INSERT INTO book_authors (book_id, author_id, role, author_order)
                        SELECT :canonical, author_id, role, author_order
                        FROM book_authors
                        WHERE book_id = :dup
                        ON CONFLICT (book_id, author_id, role) DO NOTHING
                        """
                    ),
                    {"canonical": canonical_id, "dup": dup_id},
                )
                conn.execute(
                    text(
                        """
                        UPDATE books SET have_it = TRUE
                        WHERE id = :canonical
                          AND EXISTS (SELECT 1 FROM books WHERE id = :dup AND have_it IS TRUE)
                        """
                    ),
                    {"canonical": canonical_id, "dup": dup_id},
                )
                # Re-point any merge chains at the new canonical, then mark
                # the duplicate itself as merged.
                conn.execute(
                    text("UPDATE books SET canonical_book_id = :canonical WHERE canonical_book_id = :dup"),
                    {"canonical": canonical_id, "dup": dup_id},
                )
                conn.execute(
                    text("UPDATE books SET canonical_book_id = :canonical WHERE id = :dup"),
                    {"canonical": canonical_id, "dup": dup_id},
                )

    op.create_index(
        "uq_books_asin_live",
        "books",
        ["asin"],
        unique=True,
        postgresql_where=sa.text(f"asin IS NOT NULL AND {_LIVE}"),
    )
    op.create_index(
        "uq_books_isbn13_live",
        "books",
        ["isbn13"],
        unique=True,
        postgresql_where=sa.text(f"isbn13 IS NOT NULL AND {_LIVE}"),
    )


def downgrade() -> None:
    op.drop_index("uq_books_isbn13_live", table_name="books")
    op.drop_index("uq_books_asin_live", table_name="books")
    # The duplicate merge is not reversed.

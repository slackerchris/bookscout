"""Deduplicate books that share the same ASIN, ISBN-13, or ISBN.

Cross-watchlist scanning previously created a second ``books`` row whenever
the same physical book was rescanned on behalf of a different watchlist author.
This migration repairs the historical data by:

  1. Grouping ``books`` rows by asin / isbn13 / isbn.
  2. Keeping the earliest ``created_at`` row as canonical.
  3. Re-pointing all ``book_authors`` rows that reference a duplicate book
     ID to the canonical ID (de-duping by composite key to avoid conflicts).
  4. Deleting the now-orphaned duplicate ``books`` rows.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # NOTE: Each GROUP BY + HAVING query performs a full table scan per
    # identifier column.  At ~2,000 books this completes in milliseconds.
    # If the books table grows to 100K+ rows, consider adding a partial
    # index on each identifier column WHERE identifier IS NOT NULL to
    # speed up the grouping.
    for identifier_col in ("asin", "isbn13", "isbn"):
        # Find groups of duplicate books sharing this identifier
        dupes = conn.execute(
            text(
                f"""
                SELECT {identifier_col}, array_agg(id ORDER BY created_at ASC) AS ids
                FROM books
                WHERE {identifier_col} IS NOT NULL
                  AND {identifier_col} <> ''
                GROUP BY {identifier_col}
                HAVING count(*) > 1
                """
            )
        ).fetchall()

        for row in dupes:
            ids: list[int] = list(row[1])
            canonical_id = ids[0]
            duplicate_ids = ids[1:]

            # Re-point book_authors rows; delete any that would create a
            # duplicate composite (book_id, author_id, role) after re-pointing.
            for dup_id in duplicate_ids:
                # Identify rows that can be safely moved (no conflict)
                conn.execute(
                    text(
                        """
                        UPDATE book_authors
                        SET book_id = :canonical
                        WHERE book_id = :dup
                          AND (book_id, author_id, role) NOT IN (
                              SELECT book_id, author_id, role
                              FROM book_authors
                              WHERE book_id = :canonical
                          )
                        """
                    ),
                    {"canonical": canonical_id, "dup": dup_id},
                )
                # Delete remaining rows still pointing at the duplicate
                conn.execute(
                    text("DELETE FROM book_authors WHERE book_id = :dup"),
                    {"dup": dup_id},
                )

            # Delete the duplicate book rows
            conn.execute(
                text("DELETE FROM books WHERE id = ANY(:ids)"),
                {"ids": duplicate_ids},
            )


def downgrade() -> None:
    # Data migrations are not reversible — the duplicate rows are gone.
    pass

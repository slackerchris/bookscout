"""Add author_aliases table and drop uq_books_asin unique constraint.

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-25

Changes
-------
* Creates ``author_aliases`` table: stores name variants for a canonical
  ``authors`` row.  The ``(author_id, alias)`` pair is unique; ``source``
  records where the variant was seen (``'scan'``, ``'abs'``, ``'manual'``).
* Adds a GIN-like btree index on ``author_aliases.alias`` for fast fuzzy
  lookups.
* Drops the ``uq_books_asin`` unique constraint on ``books.asin``.
  Amazon ASINs are not globally canonical — they are reused across
  marketplaces — so the constraint risks false constraint violations when
  the catalog expands beyond English-language audiobooks.  Duplicate
  prevention is handled by the ``_find_existing_book`` Phase-1 lookup
  combined with ``INSERT … ON CONFLICT DO NOTHING`` at the application layer.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── author_aliases ───────────────────────────────────────────────────────
    op.create_table(
        "author_aliases",
        sa.Column("id",        sa.Integer(),  nullable=False),
        sa.Column("author_id", sa.Integer(),  nullable=False),
        sa.Column("alias",     sa.Text(),     nullable=False),
        sa.Column("source",    sa.Text(),     server_default="scan", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"], ["authors.id"], ondelete="CASCADE", name="fk_author_aliases_author_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("author_id", "alias", name="uq_author_alias"),
    )
    op.create_index("ix_author_aliases_alias", "author_aliases", ["alias"])
    op.create_index("ix_author_aliases_author_id", "author_aliases", ["author_id"])

    # ── books.asin — drop unique constraint ──────────────────────────────────
    # See module docstring for reasoning (ASIN marketplace reuse / cross-language
    # catalog expansion).  Dedup is enforced by _find_existing_book() Phase-1.
    op.drop_constraint("uq_books_asin", "books", type_="unique")


def downgrade() -> None:
    op.drop_index("ix_author_aliases_author_id", table_name="author_aliases")
    op.drop_index("ix_author_aliases_alias", table_name="author_aliases")
    op.drop_table("author_aliases")
    op.create_unique_constraint("uq_books_asin", "books", ["asin"])

"""Add name_normalized column + index to authors table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-26

Changes
-------
* ``authors.name_normalized`` (TEXT, nullable) — punctuation/case-stripped
  author name used for indexed lookups in ``_get_or_create_author`` step 3.
  Replaces the previous O(n) Python-side full-table scan for punctuation and
  spacing variants (e.g. ``"J.N. Chaney"`` ↔ ``"J. N. Chaney"`` both map to
  ``"jnchaney"``).

  Backfilled on upgrade using PostgreSQL ``regexp_replace`` to strip all
  non-alphanumeric characters and lowercase.  New rows are populated by
  ``_get_or_create_author`` and ``api/v1/authors.py`` at creation time.

* ``ix_authors_name_normalized`` index — makes step 3 a single indexed lookup
  rather than a sequential scan.

Known limitations (see v0.51.0)
---------------------------------
* Normalisation collision: ``"O'Brian"`` and ``"OBrian"`` map to the same key.
  Low risk in practice but not zero.
* Initial expansion (e.g. ``"J.N."`` ↔ ``"John N."``) is not handled by the
  normalised key — the Python fuzzy-match fallback in step 3b still covers
  that case.  A pg_trgm trigram index would eliminate this remaining O(n) path.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("authors", sa.Column("name_normalized", sa.Text(), nullable=True))

    # Backfill: strip all non-alphanumeric chars and lowercase — mirrors
    # normalize_author_key() in core/normalize.py.
    op.execute(
        sa.text(
            "UPDATE authors "
            "SET name_normalized = lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g'))"
        )
    )

    op.create_index("ix_authors_name_normalized", "authors", ["name_normalized"])


def downgrade() -> None:
    op.drop_index("ix_authors_name_normalized", table_name="authors")
    op.drop_column("authors", "name_normalized")

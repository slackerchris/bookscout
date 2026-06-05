"""Add download_attempts table for download history tracking.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "download_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("book_id", sa.Integer(), sa.ForeignKey("books.id", ondelete="SET NULL"), nullable=True),
        sa.Column("book_title", sa.Text()),
        sa.Column("query", sa.Text()),
        sa.Column("release_title", sa.Text(), nullable=False),
        sa.Column("indexer", sa.Text()),
        sa.Column("source", sa.Text()),
        sa.Column("type", sa.Text()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("seeders", sa.Integer()),
        sa.Column("download_url", sa.Text()),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("error_detail", sa.Text()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_download_attempts_book_id", "download_attempts", ["book_id"])
    op.create_index("ix_download_attempts_created_at", "download_attempts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_download_attempts_created_at", table_name="download_attempts")
    op.drop_index("ix_download_attempts_book_id", table_name="download_attempts")
    op.drop_table("download_attempts")

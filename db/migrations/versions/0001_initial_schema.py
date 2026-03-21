"""Initial schema — all core tables

Revision ID: 0001
Revises:
Create Date: 2026-03-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── authors ─────────────────────────────────────────────────────────────
    op.create_table(
        "authors",
        sa.Column("id",              sa.Integer(),  nullable=False),
        sa.Column("name",            sa.Text(),     nullable=False),
        sa.Column("name_sort",       sa.Text(),     nullable=False),
        sa.Column("asin",            sa.Text(),     nullable=True),
        sa.Column("openlibrary_key", sa.Text(),     nullable=True),
        sa.Column("image_url",       sa.Text(),     nullable=True),
        sa.Column("bio",             sa.Text(),     nullable=True),
        sa.Column("active",          sa.Boolean(),  server_default="true",  nullable=False),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asin",            name="uq_authors_asin"),
        sa.UniqueConstraint("openlibrary_key", name="uq_authors_openlibrary_key"),
    )
    op.create_index("ix_authors_name_sort", "authors", ["name_sort"])

    # ── books ────────────────────────────────────────────────────────────────
    op.create_table(
        "books",
        sa.Column("id",               sa.Integer(),    nullable=False),
        sa.Column("title",            sa.Text(),       nullable=False),
        sa.Column("title_sort",       sa.Text(),       nullable=False),
        sa.Column("subtitle",         sa.Text(),       nullable=True),
        sa.Column("asin",             sa.Text(),       nullable=True),
        sa.Column("isbn",             sa.Text(),       nullable=True),
        sa.Column("isbn13",           sa.Text(),       nullable=True),
        sa.Column("published_year",   sa.Integer(),    nullable=True),
        sa.Column("release_date",     sa.Text(),       nullable=True),
        sa.Column("series_name",      sa.Text(),       nullable=True),
        sa.Column("series_position",  sa.Text(),       nullable=True),
        sa.Column("format",           sa.Text(),       nullable=True),
        sa.Column("source",           sa.Text(),       nullable=True),
        sa.Column("cover_url",        sa.Text(),       nullable=True),
        sa.Column("description",      sa.Text(),       nullable=True),
        sa.Column("audio_format",     sa.Text(),       nullable=True),
        sa.Column("duration_seconds", sa.Integer(),    nullable=True),
        sa.Column("file_path",        sa.Text(),       nullable=True),
        sa.Column("file_size",        sa.BigInteger(), nullable=True),
        sa.Column("file_last_modified", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("score",            sa.Integer(),    server_default="0",     nullable=False),
        sa.Column("confidence_band",  sa.Text(),       server_default="low",   nullable=False),
        sa.Column("score_reasons",    sa.Text(),       nullable=True),
        sa.Column("match_method",     sa.Text(),       server_default="api",   nullable=False),
        sa.Column("match_reviewed",   sa.Boolean(),    server_default="false", nullable=False),
        sa.Column("have_it",          sa.Boolean(),    server_default="false", nullable=False),
        sa.Column("deleted",          sa.Boolean(),    server_default="false", nullable=False),
        sa.Column("created_at",       sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at",       sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asin", name="uq_books_asin"),
    )
    op.create_index("ix_books_isbn13",         "books", ["isbn13"])
    op.create_index("ix_books_confidence_band","books", ["confidence_band"])
    op.create_index("ix_books_have_it",        "books", ["have_it"])

    # ── book_authors (many-to-many) ──────────────────────────────────────────
    op.create_table(
        "book_authors",
        sa.Column("book_id",   sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("role",      sa.Text(),    server_default="author", nullable=False),
        sa.ForeignKeyConstraint(["book_id"],   ["books.id"],   ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["authors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("book_id", "author_id", "role"),
        sa.UniqueConstraint("book_id", "author_id", "role", name="uq_book_author_role"),
    )
    op.create_index("ix_book_authors_author_id", "book_authors", ["author_id"])

    # ── watchlist ────────────────────────────────────────────────────────────
    op.create_table(
        "watchlist",
        sa.Column("id",           sa.Integer(),  nullable=False),
        sa.Column("author_id",    sa.Integer(),  nullable=False),
        sa.Column("last_scanned", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_scan",    sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("scan_enabled", sa.Boolean(),  server_default="true", nullable=False),
        sa.Column("created_at",   sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["authors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("author_id", name="uq_watchlist_author"),
    )

    # ── library_paths ────────────────────────────────────────────────────────
    op.create_table(
        "library_paths",
        sa.Column("id",           sa.Integer(), nullable=False),
        sa.Column("path",         sa.Text(),    nullable=False),
        sa.Column("name",         sa.Text(),    nullable=True),
        sa.Column("scan_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_scanned", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at",   sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path", name="uq_library_paths_path"),
    )

    # ── webhooks ─────────────────────────────────────────────────────────────
    op.create_table(
        "webhooks",
        sa.Column("id",          sa.Integer(), nullable=False),
        sa.Column("url",         sa.Text(),    nullable=False),
        sa.Column("description", sa.Text(),    nullable=True),
        sa.Column("events",      postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("active",      sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at",  sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", name="uq_webhooks_url"),
    )

    # ── webhook_deliveries ───────────────────────────────────────────────────
    op.create_table(
        "webhook_deliveries",
        sa.Column("id",           sa.Integer(), nullable=False),
        sa.Column("webhook_id",   sa.Integer(), nullable=False),
        sa.Column("event_type",   sa.Text(),    nullable=False),
        sa.Column("payload",      postgresql.JSONB(), nullable=True),
        sa.Column("status_code",  sa.Integer(), nullable=True),
        sa.Column("success",      sa.Boolean(), nullable=True),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_deliveries_webhook", "webhook_deliveries", ["webhook_id"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_table("library_paths")
    op.drop_table("watchlist")
    op.drop_table("book_authors")
    op.drop_table("books")
    op.drop_table("authors")

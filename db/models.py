"""
SQLAlchemy 2.0 async ORM models for BookScout.

These define the target PostgreSQL schema introduced in v0.31.0.
The Flask/SQLite app continues to run unchanged until the FastAPI
cutover in v0.33.0; these models are used by Alembic migrations and
the SQLite→PostgreSQL migration script in the meantime.
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKey, Index,
    Integer, Text, TIMESTAMP, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "authors"

    __table_args__ = (
        Index("ix_authors_name_normalized", "name_normalized"),
    )

    id               = Column(Integer, primary_key=True)
    name             = Column(Text, nullable=False)
    name_sort        = Column(Text, nullable=False)      # "Sanderson, Brandon"
    name_normalized  = Column(Text)                      # "jnchaney" — punctuation/case stripped (v0.50.0)
    asin             = Column(Text, unique=True)
    openlibrary_key  = Column(Text, unique=True)
    image_url        = Column(Text)
    bio              = Column(Text)
    active           = Column(Boolean, server_default="true", nullable=False)
    created_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    book_authors    = relationship("BookAuthor", back_populates="author", cascade="all, delete-orphan")
    watchlist_entry = relationship("Watchlist",  back_populates="author", uselist=False, cascade="all, delete-orphan")
    aliases         = relationship("AuthorAlias", back_populates="author", cascade="all, delete-orphan")


class AuthorAlias(Base):
    """Name variants for a canonical Author row (v0.44.0+).

    ``source`` records where the variant was seen: ``'scan'``, ``'abs'``,
    ``'manual'``, etc.  The combination of ``(author_id, alias)`` is unique
    so the same variant cannot be stored twice for the same author.
    """
    __tablename__ = "author_aliases"
    __table_args__ = (
        UniqueConstraint("author_id", "alias", name="uq_author_alias"),
    )

    id        = Column(Integer, primary_key=True)
    author_id = Column(Integer, ForeignKey("authors.id", ondelete="CASCADE"), nullable=False)
    alias     = Column(Text, nullable=False)
    source    = Column(Text, nullable=False, server_default="scan")  # 'scan' | 'abs' | 'manual'
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    author = relationship("Author", back_populates="aliases")


class Book(Base):
    __tablename__ = "books"

    id              = Column(Integer, primary_key=True)
    title           = Column(Text, nullable=False)
    title_sort      = Column(Text, nullable=False)
    subtitle        = Column(Text)
    # NOTE: asin is intentionally NOT unique at the DB level (constraint dropped
    # in migration 0003).  Amazon ASINs are reused across marketplaces, so a
    # unique constraint raises false violations when the catalog expands beyond
    # English-language audiobooks.  Duplicate prevention is handled entirely by
    # _find_existing_book() Phase-1 lookup in core/scan.py.
    asin            = Column(Text)
    isbn            = Column(Text)
    isbn13          = Column(Text)
    published_year  = Column(Integer)
    release_date    = Column(Text)
    series_name     = Column(Text)
    series_position = Column(Text)
    format          = Column(Text)
    source          = Column(Text)
    cover_url       = Column(Text)
    description     = Column(Text)
    language        = Column(Text)          # ISO 639-1 code, e.g. "en", "de"
    audio_format    = Column(Text)
    duration_seconds = Column(Integer)
    file_path       = Column(Text)
    file_size       = Column(BigInteger)
    file_last_modified = Column(TIMESTAMP(timezone=True))

    # Confidence scoring (v0.30.0+)
    score           = Column(Integer, server_default="0", nullable=False)
    confidence_band = Column(Text, server_default="low", nullable=False)
    score_reasons   = Column(Text)

    # Match tracking (v0.31.0+)
    match_method    = Column(Text, server_default="api", nullable=False)  # 'api' | 'filesystem' | 'manual' | 'audiobookshelf'
    match_reviewed  = Column(Boolean, server_default="false", nullable=False)
    have_it         = Column(Boolean, server_default="false", nullable=False)
    deleted         = Column(Boolean, server_default="false", nullable=False)

    created_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    book_authors = relationship("BookAuthor", back_populates="book", cascade="all, delete-orphan")


class BookAuthor(Base):
    """Many-to-many join between books and authors, with a role discriminator."""
    __tablename__ = "book_authors"
    __table_args__ = (
        UniqueConstraint("book_id", "author_id", "role", name="uq_book_author_role"),
    )

    book_id   = Column(Integer, ForeignKey("books.id",   ondelete="CASCADE"), primary_key=True)
    author_id = Column(Integer, ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True)
    role      = Column(Text, server_default="author", primary_key=True)  # 'author' | 'co-author' | 'narrator'

    book   = relationship("Book",   back_populates="book_authors")
    author = relationship("Author", back_populates="book_authors")


class Watchlist(Base):
    """Authors the user wants to monitor for new releases."""
    __tablename__ = "watchlist"

    id           = Column(Integer, primary_key=True)
    author_id    = Column(Integer, ForeignKey("authors.id", ondelete="CASCADE"), unique=True, nullable=False)
    last_scanned = Column(TIMESTAMP(timezone=True))
    next_scan    = Column(TIMESTAMP(timezone=True))
    scan_enabled = Column(Boolean, server_default="true", nullable=False)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    author = relationship("Author", back_populates="watchlist_entry")


class LibraryPath(Base):
    """Filesystem paths to scan for audiobook files (v0.37.0+)."""
    __tablename__ = "library_paths"

    id           = Column(Integer, primary_key=True)
    path         = Column(Text, nullable=False, unique=True)
    name         = Column(Text)
    scan_enabled = Column(Boolean, server_default="true", nullable=False)
    last_scanned = Column(TIMESTAMP(timezone=True))
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class Webhook(Base):
    """Registered webhook consumers (v0.35.0+)."""
    __tablename__ = "webhooks"

    id            = Column(Integer, primary_key=True)
    url           = Column(Text, nullable=False, unique=True)
    description   = Column(Text)
    events        = Column(ARRAY(Text))   # e.g. ['book.discovered', 'scan.complete']
    active        = Column(Boolean, server_default="true", nullable=False)
    failure_count = Column(Integer, server_default="0", nullable=False)
    disabled_at   = Column(TIMESTAMP(timezone=True))
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    deliveries = relationship("WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan")


class WebhookDelivery(Base):
    """Log of webhook delivery attempts (v0.35.0+)."""
    __tablename__ = "webhook_deliveries"

    id           = Column(Integer, primary_key=True)
    webhook_id   = Column(Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    event_type   = Column(Text, nullable=False)
    payload      = Column(JSONB)
    status_code  = Column(Integer)
    success      = Column(Boolean)
    delivered_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    webhook = relationship("Webhook", back_populates="deliveries")


# ---------------------------------------------------------------------------
# Indexes (defined outside models for clarity)
# ---------------------------------------------------------------------------
Index("ix_books_isbn13",               Book.isbn13)
Index("ix_books_confidence_band",      Book.confidence_band)
Index("ix_books_have_it",              Book.have_it)
Index("ix_authors_name_sort",          Author.name_sort)
Index("ix_book_authors_author_id",     BookAuthor.author_id)
Index("ix_webhook_deliveries_webhook", WebhookDelivery.webhook_id)

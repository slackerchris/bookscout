"""Shared pytest fixtures for BookScout tests.

In-memory SQLite session
------------------------
All DB tests use an async SQLite session created fresh per test function.
SQLite is chosen over a real Postgres container so the suite runs locally
with no external services.  SQLAlchemy's async SQLite driver (aiosqlite)
supports the same async ORM API as asyncpg.

The session is created with ``expire_on_commit=False`` and FK enforcement
enabled (SQLite requires ``PRAGMA foreign_keys = ON``).
"""
from __future__ import annotations

import asyncio
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import event, text

from db.models import Base


# ---------------------------------------------------------------------------
# Event loop — one loop per test session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Engine — one in-memory SQLite DB per test session; schema created once
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    # Enable FK enforcement for every new SQLite connection
    @event.listens_for(eng.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    # Skip tables that use Postgres-only types (ARRAY, JSONB) unsupported by SQLite.
    # Consequence: the webhooks and webhook_deliveries tables are absent from the
    # in-memory schema, so deliver_event() / _deliver() cannot be tested at the DB
    # layer here.  See tests/test_webhook_delivery.py for mock-based coverage of
    # that logic (session and httpx are patched; no real DB required).
    _SKIP_TABLES = {"webhooks", "webhook_deliveries"}
    _tables = [t for t in Base.metadata.sorted_tables if t.name not in _SKIP_TABLES]
    async with eng.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=_tables))
    yield eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# Session factory — new transaction rolled back after each test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    """Yield a fresh, isolated async session per test.

    Uses a nested transaction (SAVEPOINT) so each test starts clean without
    re-creating the schema.  SQLite doesn't support SAVEPOINTs in the same
    way as Postgres, so we use a plain transaction and roll it back instead.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as sess:
        async with sess.begin():
            yield sess
            await sess.rollback()


# ---------------------------------------------------------------------------
# Minimal book/author factory helpers (plain dicts, no DB)
# ---------------------------------------------------------------------------

def make_book_dict(
    title: str = "Test Book",
    authors: list[str] | None = None,
    isbn: str | None = None,
    isbn13: str | None = None,
    asin: str | None = None,
    release_date: str = "2020",
    source: str = "TestSource",
    format: str = "",
    series: str | None = None,
    series_position: str | None = None,
) -> dict:
    return {
        "title": title,
        "authors": authors or [],
        "isbn": isbn,
        "isbn13": isbn13,
        "asin": asin,
        "release_date": release_date,
        "source": source,
        "format": format,
        "series": series,
        "series_position": series_position,
        "subtitle": None,
        "cover_url": None,
        "description": None,
        "score": 0,
        "confidence_band": "low",
        "score_reasons": "[]",
        "have_it": False,
    }

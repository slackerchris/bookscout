"""Tests for core/scan.py — _find_existing_book() Phase 1 & 2.

Exercises the three critical scenarios called out in the REFACTOR_PLAN:
  - Live row found via identifier  (Phase 1 hit)
  - Soft-deleted row skipped       (Phase 1 deleted guard)
  - Only title fallback works      (Phase 2)
  - Cross-author hit detection     (is_cross_author flag)
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models import Author, Book, BookAuthor
from core.scan import _find_existing_book


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_author(session: AsyncSession, name: str = "Test Author") -> Author:
    author = Author(name=name, name_sort=name)
    session.add(author)
    await session.flush()
    return author


async def _make_book(
    session: AsyncSession,
    author: Author,
    title: str = "Test Book",
    isbn13: str | None = None,
    isbn: str | None = None,
    asin: str | None = None,
    deleted: bool = False,
    role: str = "author",
) -> Book:
    book = Book(
        title=title,
        title_sort=title,
        isbn13=isbn13,
        isbn=isbn,
        asin=asin,
        deleted=deleted,
        score=0,
        confidence_band="low",
        match_method="api",
    )
    session.add(book)
    await session.flush()
    session.add(BookAuthor(book_id=book.id, author_id=author.id, role=role))
    await session.flush()
    return book


# ---------------------------------------------------------------------------
# Phase 1 — global identifier lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_isbn13_hit(session):
    author = await _make_author(session)
    await _make_book(session, author, isbn13="9780765326362")
    book_dict = {"title": "Other Title", "isbn13": "9780765326362", "isbn": None, "asin": None}
    found, is_cross = await _find_existing_book(session, author.id, book_dict)
    assert found is not None
    assert found.isbn13 == "9780765326362"
    assert is_cross is False


@pytest.mark.asyncio
async def test_phase1_isbn_hit(session):
    author = await _make_author(session)
    await _make_book(session, author, isbn="0765326366")
    book_dict = {"title": "Other Title", "isbn13": None, "isbn": "0765326366", "asin": None}
    found, is_cross = await _find_existing_book(session, author.id, book_dict)
    assert found is not None
    assert found.isbn == "0765326366"


@pytest.mark.asyncio
async def test_phase1_asin_hit(session):
    author = await _make_author(session)
    await _make_book(session, author, asin="B001234567")
    book_dict = {"title": "Other Title", "isbn13": None, "isbn": None, "asin": "B001234567"}
    found, is_cross = await _find_existing_book(session, author.id, book_dict)
    assert found is not None


@pytest.mark.asyncio
async def test_phase1_skips_deleted_row(session):
    """A soft-deleted book must not be returned by Phase 1."""
    author = await _make_author(session)
    await _make_book(session, author, isbn13="9780000000001", deleted=True)
    book_dict = {"title": "Test Book", "isbn13": "9780000000001", "isbn": None, "asin": None}
    found, is_cross = await _find_existing_book(session, author.id, book_dict)
    assert found is None


@pytest.mark.asyncio
async def test_phase1_no_identifier_skips_to_phase2(session):
    """When no identifier is present, Phase 1 returns nothing and Phase 2 runs."""
    author = await _make_author(session)
    await _make_book(session, author, title="Unique Title Here")
    book_dict = {"title": "Unique Title Here", "isbn13": None, "isbn": None, "asin": None}
    found, is_cross = await _find_existing_book(session, author.id, book_dict)
    assert found is not None
    assert found.title == "Unique Title Here"


# ---------------------------------------------------------------------------
# Phase 1 — cross-author detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_cross_author_flag(session):
    """ISBN match on a book owned by a different author → is_cross_author=True."""
    author_a = await _make_author(session, "Author A")
    author_b = await _make_author(session, "Author B")
    # Book was created under author_a
    await _make_book(session, author_a, isbn13="9780000000002")
    # Scanning as author_b → should find it but flag as cross-author
    book_dict = {"title": "Shared Book", "isbn13": "9780000000002", "isbn": None, "asin": None}
    found, is_cross = await _find_existing_book(session, author_b.id, book_dict)
    assert found is not None
    assert is_cross is True


@pytest.mark.asyncio
async def test_phase1_no_cross_author_when_already_linked(session):
    """If the scanning author is already the primary author, is_cross = False."""
    author = await _make_author(session)
    await _make_book(session, author, isbn13="9780000000003")
    book_dict = {"title": "My Book", "isbn13": "9780000000003", "isbn": None, "asin": None}
    found, is_cross = await _find_existing_book(session, author.id, book_dict)
    assert is_cross is False


# ---------------------------------------------------------------------------
# Phase 2 — title fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase2_title_fallback(session):
    author = await _make_author(session)
    await _make_book(session, author, title="My Unique Title")
    book_dict = {"title": "My Unique Title", "isbn13": None, "isbn": None, "asin": None}
    found, is_cross = await _find_existing_book(session, author.id, book_dict)
    assert found is not None
    assert found.title == "My Unique Title"
    assert is_cross is False


@pytest.mark.asyncio
async def test_phase2_different_title_returns_none(session):
    author = await _make_author(session)
    await _make_book(session, author, title="Existing Title")
    book_dict = {"title": "Completely Different Title", "isbn13": None, "isbn": None, "asin": None}
    found, is_cross = await _find_existing_book(session, author.id, book_dict)
    assert found is None


@pytest.mark.asyncio
async def test_phase2_scoped_to_author(session):
    """Phase 2 must not match a book belonging to a different author."""
    author_a = await _make_author(session, "Author A")
    author_b = await _make_author(session, "Author B")
    await _make_book(session, author_a, title="Common Title")
    book_dict = {"title": "Common Title", "isbn13": None, "isbn": None, "asin": None}
    found, _ = await _find_existing_book(session, author_b.id, book_dict)
    assert found is None

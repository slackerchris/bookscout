"""Tests for the billing-order-wins primary-author rule and the live
identifier uniqueness backstop.

Scenario driving the rule: J.N. Chaney's collaborations are billed under
him (position 0 in the source authors array) regardless of which co-author's
scan discovers the book first — so primary_author_id must follow billing
order, not discovery order, unless the user pinned it manually.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from core.scan import _apply_billing_order_primary
from db.models import Author, Book, BookAuthor

pytestmark = pytest.mark.asyncio


async def _make_author(session, name: str) -> Author:
    author = Author(name=name, name_sort=name, name_normalized=name.lower())
    session.add(author)
    await session.flush()
    return author


async def _make_book(session, title: str, primary: Author, **kwargs) -> Book:
    book = Book(
        title=title,
        title_sort=title,
        score=0,
        confidence_band="low",
        match_method="api",
        primary_author_id=primary.id,
        **kwargs,
    )
    session.add(book)
    await session.flush()
    return book


async def test_top_billed_author_becomes_primary(session):
    """Discovery order loses to billing order: Chaney (order 0) wins."""
    maggert = await _make_author(session, "Terry Maggert")
    chaney = await _make_author(session, "J.N. Chaney")
    # Maggert's scan discovered the book first, so he starts as primary...
    book = await _make_book(session, "The Last Reaper", maggert)
    session.add(BookAuthor(book_id=book.id, author_id=maggert.id, role="author", author_order=1))
    session.add(BookAuthor(book_id=book.id, author_id=chaney.id, role="co-author", author_order=0))
    await session.flush()

    await _apply_billing_order_primary(session, book)

    assert book.primary_author_id == chaney.id


async def test_manual_pin_is_never_overridden(session):
    maggert = await _make_author(session, "Terry Maggert")
    chaney = await _make_author(session, "J.N. Chaney")
    book = await _make_book(session, "Pinned Book", maggert, primary_author_manual=True)
    session.add(BookAuthor(book_id=book.id, author_id=maggert.id, role="author", author_order=1))
    session.add(BookAuthor(book_id=book.id, author_id=chaney.id, role="co-author", author_order=0))
    await session.flush()

    await _apply_billing_order_primary(session, book)

    assert book.primary_author_id == maggert.id


async def test_no_billing_info_keeps_current_primary(session):
    """Links without author_order (older rows, sources with no authors array)
    must not disturb the first-discoverer default."""
    a = await _make_author(session, "Author A")
    b = await _make_author(session, "Author B")
    book = await _make_book(session, "Orderless Book", a)
    session.add(BookAuthor(book_id=book.id, author_id=a.id, role="author", author_order=None))
    session.add(BookAuthor(book_id=book.id, author_id=b.id, role="co-author", author_order=None))
    await session.flush()

    await _apply_billing_order_primary(session, book)

    assert book.primary_author_id == a.id


async def test_billing_tie_breaks_deterministically(session):
    """Equal billing positions resolve by lowest author id, so the outcome
    doesn't depend on which co-author was scanned first."""
    first = await _make_author(session, "First Created")
    second = await _make_author(session, "Second Created")
    book = await _make_book(session, "Tied Book", second)
    session.add(BookAuthor(book_id=book.id, author_id=second.id, role="author", author_order=0))
    session.add(BookAuthor(book_id=book.id, author_id=first.id, role="co-author", author_order=0))
    await session.flush()

    await _apply_billing_order_primary(session, book)

    assert book.primary_author_id == first.id


async def test_live_asin_uniqueness_enforced(session):
    """Two live books may not share an ASIN (the concurrent-scan backstop)."""
    a = await _make_author(session, "Author A")
    await _make_book(session, "Original", a, asin="B00TEST123")
    with pytest.raises(IntegrityError):
        await _make_book(session, "Racing Duplicate", a, asin="B00TEST123")
    # Roll back to the last good state so the fixture can clean up.
    await session.rollback()


async def test_merged_and_deleted_rows_exempt_from_uniqueness(session):
    """Soft-deleted rows and canonical-merged duplicates keep their ASIN
    without tripping the partial index."""
    a = await _make_author(session, "Author A")
    original = await _make_book(session, "Original", a, asin="B00TEST456")
    await _make_book(
        session, "Merged Duplicate", a, asin="B00TEST456", canonical_book_id=original.id
    )
    await _make_book(session, "Deleted Copy", a, asin="B00TEST456", deleted=True)

"""Async scan orchestrator — the core pipeline of BookScout.

``scan_author_by_id()`` is the high-level entry point.  It:
1. Queries all configured metadata APIs in parallel
2. Merges + deduplicates results
3. Scores results via the confidence engine
4. Checks each book against Audiobookshelf for ownership + series data
5. Falls back to a direct Audible lookup for series info when needed
6. Persists new / updated books to PostgreSQL
7. Optionally publishes a ``scan.complete`` event to Redis pub/sub

The function is intentionally dependency-injected (session, config, redis) so
arq background tasks and the FastAPI lifespan test fixture can drive it the
same way.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from confidence import score_books
from core.audiobookshelf import check_audiobookshelf
from core.merge import merge_books
from core.metadata import (
    query_audnexus,
    query_google_books,
    query_isbndb,
    query_openlibrary,
    search_audible_metadata_direct,
)
from core.normalize import author_names_match
from db.models import Author, Book, BookAuthor, Watchlist


async def scan_author_by_id(
    session: AsyncSession,
    author_id: int,
    config: Any = None,
    redis_client: Any = None,
) -> dict[str, Any]:
    """Run the full scan pipeline for a single author.

    Returns a summary dict:
    ``{"author_id", "author_name", "books_found", "new_books", "updated_books"}``
    """
    # ------------------------------------------------------------------ config
    language_filter = "en"
    google_api_key = ""
    isbndb_api_key = ""
    abs_url = ""
    abs_token = ""
    if config:
        scan_cfg = getattr(config, "scan", None)
        apis_cfg = getattr(config, "apis", None)
        abs_cfg = getattr(config, "audiobookshelf", None)
        language_filter = getattr(scan_cfg, "language_filter", "en") or "en"
        google_api_key = getattr(apis_cfg, "google_books_key", "") or ""
        isbndb_api_key = getattr(apis_cfg, "isbndb_key", "") or ""
        abs_url = getattr(abs_cfg, "url", "") or ""
        abs_token = getattr(abs_cfg, "token", "") or ""

    # ------------------------------------------------------------------ load author
    result = await session.execute(select(Author).where(Author.id == author_id))
    author: Author | None = result.scalar_one_or_none()
    if not author:
        raise ValueError(f"Author {author_id} not found")

    author_name = author.name
    print(f"[scan] '{author_name}' (lang={language_filter})")

    # ------------------------------------------------------------------ API queries
    async with httpx.AsyncClient() as client:
        source_tasks = [
            query_openlibrary(client, author_name, language_filter),
            query_google_books(client, author_name, language_filter, google_api_key or None),
            query_audnexus(client, author_name, language_filter),
        ]
        if isbndb_api_key:
            source_tasks.append(
                query_isbndb(client, author_name, isbndb_api_key, language_filter)
            )

        source_results = await asyncio.gather(*source_tasks)
        all_books = merge_books(list(source_results))
        all_books = score_books(all_books, search_author=author_name)

        # ABS ownership + series (serialised to stay under ABS rate limit)
        for book in all_books:
            has_it, abs_series, abs_pos = await check_audiobookshelf(
                client, book["title"], author_name, abs_url, abs_token
            )
            book["have_it"] = has_it
            if abs_series:
                book["series"] = abs_series
                book["series_position"] = abs_pos
            elif not has_it and not book.get("series"):
                aud_series, aud_pos = await search_audible_metadata_direct(
                    client, book["title"], author_name
                )
                if aud_series:
                    book["series"] = aud_series
                    book["series_position"] = aud_pos

    # ------------------------------------------------------------------ persist
    new_books = 0
    updated_books = 0
    discovered: list[dict[str, Any]] = []

    for book in all_books:
        existing: Book | None = await _find_existing_book(session, author_id, book)

        # Never re-add intentionally deleted books
        if existing and existing.deleted:
            continue

        source_val = book.get("source", [])
        source_str = json.dumps(source_val) if isinstance(source_val, list) else (source_val or "")
        score_reasons_str = json.dumps(book.get("score_reasons") or [])
        have_it_bool = bool(book.get("have_it", False))
        co_authors = [
            a for a in (book.get("authors") or [])
            if not author_names_match(author_name, a)
        ]
        published_year = _parse_year(book.get("release_date"))

        if not existing:
            new_book = Book(
                title=book["title"],
                title_sort=_sort_title(book["title"]),
                subtitle=book.get("subtitle"),
                isbn=book.get("isbn"),
                isbn13=book.get("isbn13"),
                asin=book.get("asin"),
                release_date=str(book.get("release_date") or "") or None,
                published_year=published_year,
                format=book.get("format"),
                source=source_str,
                cover_url=book.get("cover_url"),
                description=book.get("description"),
                series_name=book.get("series"),
                series_position=book.get("series_position"),
                have_it=have_it_bool,
                score=book.get("score", 0),
                confidence_band=book.get("confidence_band", "low"),
                score_reasons=score_reasons_str,
                match_method="audiobookshelf" if have_it_bool else "api",
            )
            session.add(new_book)
            await session.flush()  # populate new_book.id

            # Primary author link
            session.add(
                BookAuthor(book_id=new_book.id, author_id=author_id, role="author")
            )
            # Co-author links
            for co_name in co_authors:
                co_author = await _get_or_create_author(session, co_name)
                session.add(
                    BookAuthor(book_id=new_book.id, author_id=co_author.id, role="co-author")
                )

            new_books += 1
            if book.get("confidence_band") in ("high", "medium") or have_it_bool:
                discovered.append(
                    {
                        "title": book["title"],
                        "author": author_name,
                        "have_it": have_it_bool,
                        "confidence_band": book.get("confidence_band"),
                    }
                )
        else:
            # COALESCE: only fill in fields that are currently empty
            if have_it_bool:
                existing.have_it = True
                existing.match_method = "audiobookshelf"

            for attr, new_val in (
                ("series_name", book.get("series")),
                ("series_position", book.get("series_position")),
                ("cover_url", book.get("cover_url")),
                ("subtitle", book.get("subtitle")),
                ("description", book.get("description")),
                ("asin", book.get("asin")),
                ("isbn", book.get("isbn")),
                ("isbn13", book.get("isbn13")),
            ):
                if not getattr(existing, attr) and new_val:
                    setattr(existing, attr, new_val)

            existing.score = book.get("score", 0)
            existing.confidence_band = book.get("confidence_band", "low")
            existing.score_reasons = score_reasons_str
            existing.updated_at = datetime.now(timezone.utc)
            updated_books += 1

    # Update watchlist last_scanned
    wl_result = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    watchlist = wl_result.scalar_one_or_none()
    if watchlist:
        watchlist.last_scanned = datetime.now(timezone.utc)

    await session.commit()

    # Publish Redis event
    if redis_client and all_books:
        payload = json.dumps(
            {
                "event": "scan.complete",
                "author_id": author_id,
                "author_name": author_name,
                "books_found": len(all_books),
                "new_books": new_books,
                "updated_books": updated_books,
                "discovered": discovered,
            }
        )
        await redis_client.publish("bookscout:events", payload)

    print(
        f"[scan] '{author_name}': {len(all_books)} found, "
        f"{new_books} new, {updated_books} updated"
    )
    return {
        "author_id": author_id,
        "author_name": author_name,
        "books_found": len(all_books),
        "new_books": new_books,
        "updated_books": updated_books,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _find_existing_book(
    session: AsyncSession, author_id: int, book: dict[str, Any]
) -> Book | None:
    """Return the existing DB record matching this book, or None."""
    for field, value in (
        (Book.isbn13, book.get("isbn13")),
        (Book.isbn, book.get("isbn")),
        (Book.asin, book.get("asin")),
    ):
        if value:
            q = await session.execute(
                select(Book)
                .join(BookAuthor, Book.id == BookAuthor.book_id)
                .where(
                    and_(
                        BookAuthor.author_id == author_id,
                        BookAuthor.role == "author",
                        field == value,
                    )
                )
            )
            found = q.scalar_one_or_none()
            if found:
                return found

    # Title fallback
    q = await session.execute(
        select(Book)
        .join(BookAuthor, Book.id == BookAuthor.book_id)
        .where(
            and_(
                BookAuthor.author_id == author_id,
                BookAuthor.role == "author",
                Book.title == book["title"],
            )
        )
    )
    return q.scalar_one_or_none()


async def _get_or_create_author(session: AsyncSession, name: str) -> Author:
    result = await session.execute(select(Author).where(Author.name == name))
    author = result.scalar_one_or_none()
    if not author:
        author = Author(name=name, name_sort=_sort_name(name))
        session.add(author)
        await session.flush()
    return author


def _sort_title(title: str) -> str:
    for article in ("The ", "A ", "An "):
        if title.startswith(article):
            return title[len(article):] + ", " + article.strip()
    return title


def _sort_name(name: str) -> str:
    parts = name.strip().rsplit(" ", 1)
    return f"{parts[1]}, {parts[0]}" if len(parts) == 2 else name


def _parse_year(release_date: Any) -> int | None:
    if not release_date:
        return None
    try:
        return int(str(release_date)[:4])
    except (ValueError, TypeError):
        return None

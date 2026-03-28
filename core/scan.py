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
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import httpx
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from confidence import score_books
from core.audiobookshelf import fetch_abs_books_for_author
from core.merge import merge_books
from core.metadata import (
    query_audnexus,
    query_google_books,
    query_isbndb,
    query_openlibrary,
    search_audible_metadata_direct,
)
from core.normalize import author_names_match, normalize_author_key, normalize_title_key, sort_name, sort_title
from db.models import Author, AuthorAlias, Book, BookAuthor, Watchlist

logger = logging.getLogger(__name__)

# Role-suffix pattern: strips annotations added by APIs like OpenLibrary/Google Books
# that encode contributor roles in the name string, e.g.
#   "Alan Tepper - Übersetzer"  (German: translator)
#   "Frog Jones - editor"
#   "S. T. Joshi - foreword"
#   "Grover Gardner narrator"  (space-separated, no dash/paren)
#   "Alexandre Dayet - traducteur"  (French: translator)
#   "Stefano Andrea Cresti - traduttore"  (Italian: translator)
#   "Isabel Murillo - translator"  (Spanish/generic)
_CONTRIBUTOR_ROLE_RE = re.compile(
    r"(?:\s*[-–(]\s*|\s+)"          # separator: dash/paren OR plain space
    r"(?:"
    r"editor|narrator|narrators|author|translator|illustrator|foreword|afterword"
    r"|introduction|contributor|undifferentiated"
    r"|traducteur|tarducteur"        # French
    r"|traduttore|traduttrice"       # Italian
    r"|traductor|traducción"         # Spanish
    r"|übersetzer"                   # German
    r"|tradutor|tradução"            # Portuguese
    r"|tarductor"
    r")\s*\)?$",                     # must appear at the END of the string
    re.IGNORECASE,
)

# Names starting with these prefixes are narrator credits, not author names.
_NARRATOR_PREFIX_RE = re.compile(
    r"^(?:read\s+by|narrated\s+by|performed\s+by)\b",
    re.IGNORECASE,
)

# A comma inside a name string almost always means it's a narrator credit
# listing multiple people (e.g. "Scott Aiello, Marc Vietor, Tavia Gilbert").
# Real author names never contain commas in this context.
_MULTI_PERSON_RE = re.compile(r",")

# Known noise strings that appear as author names in API data but are not real authors.
_NOISE_AUTHORS: frozenset[str] = frozenset({
    "et al", "et al.",
    "a full cast", "full cast",
    "various", "various authors", "various artists", "varios autores",
    "aa. vv.", "aa vv", "aavv",
    "unknown", "unknown author",
    "multiple authors", "multiple narrators", "narrators", "others",
    "audible sleep",
    "tbd",
})


def _is_contributor_only(name: str) -> bool:
    """Return True if *name* encodes a non-primary-author role.

    Catches:
    - Role-suffixed names: "Frog Jones - editor", "Grover Gardner narrator"
    - "Read by …" / "Narrated by …" prefix credits
    - Comma-separated multi-person strings (narrator group credits)
    - Known noise strings like "et al", "A Full Cast", etc.
    """
    stripped = name.strip()
    if stripped.lower() in _NOISE_AUTHORS:
        return True
    if _NARRATOR_PREFIX_RE.match(stripped):
        return True
    if _MULTI_PERSON_RE.search(stripped):
        return True
    return bool(_CONTRIBUTOR_ROLE_RE.search(stripped))


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
    auto_add_coauthors = False
    cache_ttl_seconds = 86400  # 24 h default
    src_ol = True
    src_gb = True
    src_audible = True
    src_isbndb = True
    if config:
        scan_cfg = getattr(config, "scan", None)
        apis_cfg = getattr(config, "apis", None)
        abs_cfg = getattr(config, "audiobookshelf", None)
        sources_cfg = getattr(scan_cfg, "sources", None)
        language_filter = getattr(scan_cfg, "language_filter", "en") or "en"
        auto_add_coauthors = bool(getattr(scan_cfg, "auto_add_coauthors", False))
        cache_ttl_hours = int(getattr(scan_cfg, "cache_ttl_hours", 24) or 24)
        cache_ttl_seconds = cache_ttl_hours * 3600
        google_api_key = getattr(apis_cfg, "google_books_key", "") or ""
        isbndb_api_key = getattr(apis_cfg, "isbndb_key", "") or ""
        abs_url = getattr(abs_cfg, "url", "") or ""
        abs_token = getattr(abs_cfg, "token", "") or ""
        if sources_cfg is not None:
            src_ol = bool(getattr(sources_cfg, "openlibrary", True))
            src_gb = bool(getattr(sources_cfg, "google_books", True))
            src_audible = bool(getattr(sources_cfg, "audible", True))
            src_isbndb = bool(getattr(sources_cfg, "isbndb", True))

    # ------------------------------------------------------------------ load author
    result = await session.execute(select(Author).where(Author.id == author_id))
    author: Author | None = result.scalar_one_or_none()
    if not author:
        raise ValueError(f"Author {author_id} not found")

    author_name = author.name
    logger.info("Scan started", extra={"author_id": author_id, "author": author_name, "lang": language_filter})

    # ------------------------------------------------------------------ API queries
    async with httpx.AsyncClient() as client:
        source_tasks = []
        if src_ol:
            source_tasks.append(
                _cached_query(
                    redis_client, cache_ttl_seconds,
                    f"bookscout:meta:openlibrary:{_cache_author_key(author_name)}:{language_filter}",
                    query_openlibrary(client, author_name, language_filter),
                )
            )
        if src_gb:
            source_tasks.append(
                _cached_query(
                    redis_client, cache_ttl_seconds,
                    f"bookscout:meta:google:{_cache_author_key(author_name)}:{language_filter}",
                    query_google_books(client, author_name, language_filter, google_api_key or None),
                )
            )
        if src_audible:
            source_tasks.append(
                _cached_query(
                    redis_client, cache_ttl_seconds,
                    f"bookscout:meta:audnexus:{_cache_author_key(author_name)}:{language_filter}",
                    query_audnexus(client, author_name, language_filter),
                )
            )
        if src_isbndb and isbndb_api_key:
            source_tasks.append(
                _cached_query(
                    redis_client, cache_ttl_seconds,
                    f"bookscout:meta:isbndb:{_cache_author_key(author_name)}:{language_filter}",
                    query_isbndb(client, author_name, isbndb_api_key, language_filter),
                )
            )

        source_results = await asyncio.gather(*source_tasks)
        all_books = merge_books(list(source_results))
        all_books = score_books(all_books, search_author=author_name)

        # ── ABS ownership: one bulk fetch for this author, then local match ──
        # This replaces per-title searches (N HTTP calls → 1–2 calls total).
        abs_owned = await fetch_abs_books_for_author(
            client, author_name, abs_url, abs_token
        )
        logger.info(
            "ABS ownership loaded",
            extra={"author": author_name, "abs_owned_count": len(abs_owned)},
        )

        # Audible series fallback — still concurrent for books with no series data
        abs_sem = asyncio.Semaphore(4)

        async def _enrich_book(book: dict[str, Any]) -> None:
            key = normalize_title_key(book["title"])
            abs_match = abs_owned.get(key)
            if abs_match:
                book["have_it"] = True
                if abs_match["series_name"]:
                    book["series"] = abs_match["series_name"]
                    book["series_position"] = abs_match["series_position"]
                # Prefer ABS ASIN over metadata-API ASIN when available
                if abs_match["asin"] and not book.get("asin"):
                    book["asin"] = abs_match["asin"]
            else:
                book["have_it"] = False
                # Audible fallback for series info on unowned books
                if not book.get("series"):
                    async with abs_sem:
                        aud_series, aud_pos = await search_audible_metadata_direct(
                            client, book["title"], author_name
                        )
                    if aud_series:
                        book["series"] = aud_series
                        book["series_position"] = aud_pos

        await asyncio.gather(*(_enrich_book(b) for b in all_books))

    # ------------------------------------------------------------------ persist
    new_books = 0
    updated_books = 0
    discovered: list[dict[str, Any]] = []
    all_scan_co_names: set[str] = set()

    for book in all_books:
        existing, is_cross_author = await _find_existing_book(session, author_id, book)

        # Never re-add intentionally deleted books
        if existing and existing.deleted:
            continue

        source_val = book.get("source", [])
        source_str = json.dumps(source_val) if isinstance(source_val, list) else (source_val or "")
        score_reasons_str = json.dumps(book.get("score_reasons") or [])
        have_it_bool = bool(book.get("have_it", False))
        # Build narrator string from the narrators list (never touches Author table)
        narrator_names: list[str] = [
            n for n in (book.get("narrators") or [])
            if n and not _is_contributor_only(n)
        ]
        narrator_str: str | None = ", ".join(narrator_names) if narrator_names else None
        co_authors = [
            a for a in (book.get("authors") or [])
            if not author_names_match(author_name, a)
            and not _is_contributor_only(a)
        ]
        all_scan_co_names.update(co_authors)
        published_year = _parse_year(book.get("release_date"))

        if not existing:
            new_book = Book(
                title=book["title"],
                title_sort=sort_title(book["title"]),
                subtitle=book.get("subtitle"),
                isbn=book.get("isbn"),
                isbn13=book.get("isbn13"),
                asin=book.get("asin"),
                release_date=str(book.get("release_date") or "") or None,
                published_year=published_year,
                format=book.get("format"),
                language=book.get("language"),
                source=source_str,
                cover_url=book.get("cover_url"),
                description=book.get("description"),
                series_name=book.get("series"),
                series_position=book.get("series_position"),
                narrator=narrator_str,
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
            # Co-author links — only create Author rows when auto_add_coauthors
            # is enabled; otherwise link only already-tracked authors.
            new_book_co_ids: set[int] = set()
            for co_name in co_authors:
                if auto_add_coauthors:
                    co_author = await _get_or_create_author(session, co_name)
                else:
                    co_author = await _find_author(session, co_name)
                if co_author and co_author.id not in new_book_co_ids:
                    new_book_co_ids.add(co_author.id)
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
            # Cross-author hit: promote scanning author to primary author
            if is_cross_author:
                session.add(
                    BookAuthor(book_id=existing.id, author_id=author_id, role="author")
                )
                await session.execute(
                    delete(BookAuthor).where(
                        and_(
                            BookAuthor.book_id == existing.id,
                            BookAuthor.author_id == author_id,
                            BookAuthor.role == "co-author",
                        )
                    )
                )

            # Only upgrade have_it — never downgrade it.  ABS search returns
            # False for "not found" AND for fuzzy-match misses, so a False result
            # is not a reliable confirmation that the book is absent.  The
            # filesystem scanner is the correct tool for clearing ownership.
            if have_it_bool:
                existing.have_it = True
                existing.match_method = "audiobookshelf"

            # ABS series data is authoritative; overwrite when ABS provides it.
            # For all other fields coalesce: only fill in fields that are currently empty.
            abs_series = book.get("series")
            abs_pos = book.get("series_position")
            if abs_series:
                existing.series_name = abs_series
                existing.series_position = abs_pos
            elif not existing.series_name and abs_pos:
                existing.series_position = abs_pos

            for attr, new_val in (
                ("cover_url", book.get("cover_url")),
                ("subtitle", book.get("subtitle")),
                ("description", book.get("description")),
                ("asin", book.get("asin")),
                ("isbn", book.get("isbn")),
                ("isbn13", book.get("isbn13")),
                ("language", book.get("language")),
                ("narrator", narrator_str),
            ):
                if not getattr(existing, attr) and new_val:
                    setattr(existing, attr, new_val)

            existing.score = book.get("score", 0)
            existing.confidence_band = book.get("confidence_band", "low")
            existing.score_reasons = score_reasons_str
            existing.updated_at = datetime.now(timezone.utc)

            # Full set-reconcile for co-author links
            existing_co_result = await session.execute(
                select(BookAuthor.author_id).where(
                    and_(
                        BookAuthor.book_id == existing.id,
                        BookAuthor.role == "co-author",
                    )
                )
            )
            existing_co_ids: set[int] = {row[0] for row in existing_co_result.fetchall()}
            fresh_co_ids: set[int] = set()
            for co_name in co_authors:
                if auto_add_coauthors:
                    co_author = await _get_or_create_author(session, co_name)
                else:
                    co_author = await _find_author(session, co_name)
                if co_author:
                    if co_author.id not in existing_co_ids and co_author.id not in fresh_co_ids:
                        session.add(
                            BookAuthor(book_id=existing.id, author_id=co_author.id, role="co-author")
                        )
                    fresh_co_ids.add(co_author.id)
            for stale_id in existing_co_ids - fresh_co_ids:
                await session.execute(
                    delete(BookAuthor).where(
                        and_(
                            BookAuthor.book_id == existing.id,
                            BookAuthor.author_id == stale_id,
                            BookAuthor.role == "co-author",
                        )
                    )
                )

            updated_books += 1

    # ------------------------------------------------------------------ language cleanup
    # Soft-delete any existing books for this author whose language is KNOWN
    # and doesn't match the language filter (e.g. Polish editions that were
    # persisted before strict language filtering was in place) and are unowned.
    if language_filter and language_filter != "all":
        lang_cleanup_result = await session.execute(
            select(Book)
            .join(BookAuthor, and_(BookAuthor.book_id == Book.id, BookAuthor.author_id == author_id))
            .where(
                Book.deleted.is_(False),
                Book.have_it.is_(False),
                Book.language.isnot(None),
                Book.language != language_filter,
            )
        )
        for stale_book in lang_cleanup_result.scalars().all():
            stale_book.deleted = True
            stale_book.updated_at = datetime.now(timezone.utc)
            logger.info(
                "Soft-deleted non-matching language book",
                extra={"book_id": stale_book.id, "title": stale_book.title, "language": stale_book.language},
            )

    # ------------------------------------------------------------------ co-author discovery
    discovered_co_authors: list[str] = []
    for co_name in sorted(all_scan_co_names):
        co_q = await session.execute(select(Author).where(Author.name == co_name))
        co_obj = co_q.scalar_one_or_none()
        on_watchlist = False
        if co_obj:
            wl_check = await session.execute(
                select(Watchlist).where(Watchlist.author_id == co_obj.id)
            )
            on_watchlist = wl_check.scalar_one_or_none() is not None
        if not on_watchlist:
            if auto_add_coauthors:
                if not co_obj:
                    co_obj = Author(name=co_name, name_sort=sort_name(co_name), name_normalized=normalize_author_key(co_name))
                    session.add(co_obj)
                    await session.flush()
                session.add(Watchlist(author_id=co_obj.id))
            discovered_co_authors.append(co_name)

    # Update watchlist last_scanned
    wl_result = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    watchlist = wl_result.scalar_one_or_none()
    if watchlist:
        watchlist.last_scanned = datetime.now(timezone.utc)

    await session.commit()

    # Publish Redis events
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
    if redis_client and discovered_co_authors:
        co_payload = json.dumps(
            {
                "event": "coauthor.discovered",
                "author_id": author_id,
                "author_name": author_name,
                "coauthors": discovered_co_authors,
                "auto_added": auto_add_coauthors,
            }
        )
        await redis_client.publish("bookscout:events", co_payload)

    logger.info(
        "Scan complete",
        extra={
            "author_id": author_id,
            "author": author_name,
            "books_found": len(all_books),
            "new_books": new_books,
            "updated_books": updated_books,
        },
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
) -> tuple[Book | None, bool]:
    """Return ``(existing_book_or_None, is_cross_author_hit)``.

    Phase 1 — global identity lookup by isbn13/isbn/asin with *no* author
    filter.  Any existing book row for the same identifier is considered the
    same book, regardless of who originally added it.

    Phase 2 — author-scoped title fallback, used only when no identifier is
    available.

    ``is_cross_author_hit`` is True when a book was found via Phase 1 but the
    scanning author is not yet recorded as a primary (``role='author'``)
    contributor.  The calling code is responsible for adding that link and
    removing any stale ``role='co-author'`` row for the same person.
    """
    # Phase 1: global identifier lookup — no author filter; include soft-deleted rows
    # so the caller's guard can prevent re-creation of intentionally deleted books.
    for field, value in (
        (Book.isbn13, book.get("isbn13")),
        (Book.isbn, book.get("isbn")),
        (Book.asin, book.get("asin")),
    ):
        if value:
            q = await session.execute(
                select(Book).where(field == value)
            )
            found = q.scalars().first()
            if found:
                link_q = await session.execute(
                    select(BookAuthor).where(
                        and_(
                            BookAuthor.book_id == found.id,
                            BookAuthor.author_id == author_id,
                            BookAuthor.role == "author",
                        )
                    )
                )
                is_cross = link_q.scalar_one_or_none() is None
                return found, is_cross

    # Phase 2: author-scoped title fallback; include soft-deleted rows so the
    # caller's guard can prevent re-creation of intentionally deleted books.
    tkey = normalize_title_key(book["title"])
    q = await session.execute(
        select(Book)
        .join(BookAuthor, Book.id == BookAuthor.book_id)
        .where(
            and_(
                BookAuthor.author_id == author_id,
                BookAuthor.role == "author",
            )
        )
    )
    for candidate in q.scalars().all():
        if normalize_title_key(candidate.title) == tkey:
            return candidate, False
    return None, False


async def _find_author(session: AsyncSession, name: str) -> Author | None:
    """Look up an existing Author by name (exact → alias → normalised key → fuzzy).

    Unlike ``_get_or_create_author``, this never inserts a new row.  Used when
    ``auto_add_coauthors`` is False so that co-author names from API data do
    not silently populate the authors table.
    """
    # 1. Exact match
    result = await session.execute(select(Author).where(Author.name == name))
    author = result.scalar_one_or_none()
    if author:
        return author

    # 2. Alias table
    alias_q = await session.execute(
        select(AuthorAlias).where(AuthorAlias.alias == name)
    )
    alias_row = alias_q.scalar_one_or_none()
    if alias_row:
        author_q = await session.execute(
            select(Author).where(Author.id == alias_row.author_id)
        )
        author = author_q.scalar_one_or_none()
        if author:
            return author

    # 3. Normalised-key index
    key = normalize_author_key(name)
    norm_result = await session.execute(
        select(Author).where(Author.name_normalized == key)
    )
    author = norm_result.scalar_one_or_none()
    if author:
        return author

    # 3b. Fuzzy fallback
    all_result = await session.execute(select(Author))
    for existing in all_result.scalars():
        if author_names_match(name, existing.name):
            return existing

    return None


async def _get_or_create_author(session: AsyncSession, name: str) -> Author:
    # 1. Exact match (fast, indexed path)
    result = await session.execute(select(Author).where(Author.name == name))
    author = result.scalar_one_or_none()
    if author:
        await _record_alias(session, author, name, "scan")
        return author

    # 2. Check the aliases table for a previously seen variant
    alias_q = await session.execute(
        select(AuthorAlias).where(AuthorAlias.alias == name)
    )
    alias_row = alias_q.scalar_one_or_none()
    if alias_row:
        author_q = await session.execute(
            select(Author).where(Author.id == alias_row.author_id)
        )
        author = author_q.scalar_one_or_none()
        if author:
            return author

    # 3. Normalised-key lookup — single indexed SQL query covering punctuation
    #    and spacing variants (e.g. "J.N. Chaney" ↔ "J. N. Chaney" both map
    #    to "jnchaney").  Replaces the previous O(n) full-table scan for this
    #    class of variants.
    key = normalize_author_key(name)
    norm_result = await session.execute(
        select(Author).where(Author.name_normalized == key)
    )
    author = norm_result.scalar_one_or_none()
    if author:
        await _record_alias(session, author, name, "scan")
        return author

    # 3b. Full fuzzy-match fallback for initial-expansion variants not handled
    #     by the normalised key (e.g. "J.N. Chaney" ↔ "John N. Chaney").
    #     TODO v0.51.0: replace with pg_trgm trigram index to eliminate this
    #     remaining O(n) scan path.
    all_result = await session.execute(select(Author))
    for existing in all_result.scalars():
        if author_names_match(name, existing.name):
            await _record_alias(session, existing, name, "scan")
            return existing

    # 4. No match — create a new author row and seed its canonical name as
    #    the first alias.
    author = Author(name=name, name_sort=sort_name(name), name_normalized=normalize_author_key(name))
    session.add(author)
    await session.flush()  # populate author.id
    await _record_alias(session, author, name, "scan")
    return author


async def _record_alias(
    session: AsyncSession, author: Author, alias: str, source: str
) -> None:
    """Insert an AuthorAlias row if the (author_id, alias) pair is new."""
    existing = await session.execute(
        select(AuthorAlias).where(
            AuthorAlias.author_id == author.id,
            AuthorAlias.alias == alias,
        )
    )
    if existing.scalar_one_or_none() is None:
        session.add(AuthorAlias(author_id=author.id, alias=alias, source=source))


def _parse_year(release_date: Any) -> int | None:
    if not release_date:
        return None
    try:
        return int(str(release_date)[:4])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Metadata cache helpers
# ---------------------------------------------------------------------------

def _cache_author_key(name: str) -> str:
    """Normalise an author name into a compact Redis key segment."""
    return normalize_author_key(name)


async def _cached_query(
    redis_client: Any,
    ttl_seconds: int,
    key: str,
    coro: Coroutine[Any, Any, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Return cached metadata results when available, otherwise execute *coro*.

    Results are stored as a JSON blob in Redis under *key* with a TTL of
    *ttl_seconds*.  When Redis is unavailable or caching is disabled
    (``redis_client`` is None or ``ttl_seconds`` <= 0), *coro* is executed
    directly with no caching overhead.
    """
    if redis_client is None or ttl_seconds <= 0:
        return await coro

    try:
        cached = await redis_client.get(key)
        if cached is not None:
            logger.debug("Cache hit", extra={"key": key})
            coro.close()  # prevent "coroutine was never awaited" warning
            return json.loads(cached)
    except Exception as exc:
        logger.warning("Cache read failed", extra={"key": key, "error": str(exc)})

    result = await coro

    try:
        await redis_client.set(key, json.dumps(result), ex=ttl_seconds)
        logger.debug("Cache stored", extra={"key": key, "ttl": ttl_seconds})
    except Exception as exc:
        logger.warning("Cache write failed", extra={"key": key, "error": str(exc)})

    return result

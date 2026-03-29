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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Coroutine, TypedDict

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

# ---------------------------------------------------------------------------
# Contributor filtering
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class _ScanConfig:
    language_filter: str = "en"
    google_api_key: str = ""
    isbndb_api_key: str = ""
    abs_url: str = ""
    abs_token: str = ""
    auto_add_coauthors: bool = False
    cache_ttl_seconds: int = 86400
    src_ol: bool = True
    src_gb: bool = True
    src_audible: bool = True
    src_isbndb: bool = True


def _extract_config(config: Any) -> _ScanConfig:
    """Extract scan settings from the application config into a typed dataclass."""
    if not config:
        return _ScanConfig()
    scan_cfg = getattr(config, "scan", None)
    apis_cfg = getattr(config, "apis", None)
    abs_cfg = getattr(config, "audiobookshelf", None)
    sources_cfg = getattr(scan_cfg, "sources", None)
    cache_ttl_hours = int(getattr(scan_cfg, "cache_ttl_hours", 24) or 24)
    cfg = _ScanConfig(
        language_filter=getattr(scan_cfg, "language_filter", "en") or "en",
        google_api_key=getattr(apis_cfg, "google_books_key", "") or "",
        isbndb_api_key=getattr(apis_cfg, "isbndb_key", "") or "",
        abs_url=getattr(abs_cfg, "url", "") or "",
        abs_token=getattr(abs_cfg, "token", "") or "",
        auto_add_coauthors=bool(getattr(scan_cfg, "auto_add_coauthors", False)),
        cache_ttl_seconds=cache_ttl_hours * 3600,
    )
    if sources_cfg is not None:
        cfg.src_ol = bool(getattr(sources_cfg, "openlibrary", True))
        cfg.src_gb = bool(getattr(sources_cfg, "google_books", True))
        cfg.src_audible = bool(getattr(sources_cfg, "audible", True))
        cfg.src_isbndb = bool(getattr(sources_cfg, "isbndb", True))
    return cfg


# ---------------------------------------------------------------------------
# Public types + entry point
# ---------------------------------------------------------------------------

class ScanResult(TypedDict):
    author_id: int
    author_name: str
    books_found: int
    new_books: int
    updated_books: int


async def scan_author_by_id(
    session: AsyncSession,
    author_id: int,
    config: Any = None,
    redis_client: Any = None,
) -> ScanResult:
    """Run the full scan pipeline for a single author.

    Returns a ``ScanResult`` dict with author_id, author_name, books_found,
    new_books, and updated_books.
    """
    cfg = _extract_config(config)
    author = await _load_author(session, author_id)
    author_name = author.name

    logger.info("Scan started", extra={"author_id": author_id, "author": author_name, "lang": cfg.language_filter})

    async with httpx.AsyncClient() as client:
        books = await _fetch_metadata(client, author_name, cfg, redis_client)
        books = await _enrich_with_abs(client, books, author_name, cfg)
        # Re-score after ABS enrichment: ABS may have added an ASIN that
        # wasn't present when _fetch_metadata scored the books, so the
        # asin_present (+40) and audiobook_format_match (+20) bonuses
        # would otherwise be missing for books matched via ABS.
        books = score_books(books, search_author=author_name)

    new_books, updated_books, discovered, discovered_coauthors = await _persist_scan_results(
        session, author_id, author_name, books, cfg
    )

    await _publish_events(
        redis_client, author_id, author_name,
        new_books, updated_books, len(books), discovered, discovered_coauthors,
        cfg.auto_add_coauthors,
    )

    logger.info(
        "Scan complete",
        extra={
            "author_id": author_id,
            "author": author_name,
            "books_found": len(books),
            "new_books": new_books,
            "updated_books": updated_books,
        },
    )
    return {
        "author_id": author_id,
        "author_name": author_name,
        "books_found": len(books),
        "new_books": new_books,
        "updated_books": updated_books,
    }


# ---------------------------------------------------------------------------
# Pipeline phases
# ---------------------------------------------------------------------------

async def _load_author(session: AsyncSession, author_id: int) -> Author:
    result = await session.execute(select(Author).where(Author.id == author_id))
    author = result.scalar_one_or_none()
    if not author:
        raise ValueError(f"Author {author_id} not found")
    return author


async def _fetch_metadata(
    client: httpx.AsyncClient,
    author_name: str,
    cfg: _ScanConfig,
    redis_client: Any,
) -> list[dict[str, Any]]:
    """Query all enabled metadata sources in parallel, merge, and score."""
    source_tasks = []
    if cfg.src_ol:
        source_tasks.append(_cached_query(
            redis_client, cfg.cache_ttl_seconds,
            f"bookscout:meta:openlibrary:{_cache_author_key(author_name)}:{cfg.language_filter}",
            query_openlibrary(client, author_name, cfg.language_filter),
        ))
    if cfg.src_gb:
        source_tasks.append(_cached_query(
            redis_client, cfg.cache_ttl_seconds,
            f"bookscout:meta:google:{_cache_author_key(author_name)}:{cfg.language_filter}",
            query_google_books(client, author_name, cfg.language_filter, cfg.google_api_key or None),
        ))
    if cfg.src_audible:
        source_tasks.append(_cached_query(
            redis_client, cfg.cache_ttl_seconds,
            f"bookscout:meta:audnexus:{_cache_author_key(author_name)}:{cfg.language_filter}",
            query_audnexus(client, author_name, cfg.language_filter),
        ))
    if cfg.src_isbndb and cfg.isbndb_api_key:
        source_tasks.append(_cached_query(
            redis_client, cfg.cache_ttl_seconds,
            f"bookscout:meta:isbndb:{_cache_author_key(author_name)}:{cfg.language_filter}",
            query_isbndb(client, author_name, cfg.isbndb_api_key, cfg.language_filter),
        ))

    source_results = await asyncio.gather(*source_tasks)
    books = merge_books(list(source_results))
    return score_books(books, search_author=author_name)


async def _enrich_book(
    book: dict[str, Any],
    abs_owned: dict[str, dict],
    abs_owned_by_asin: dict[str, dict],
    client: httpx.AsyncClient,
    author_name: str,
    sem: asyncio.Semaphore,
) -> None:
    """Set have_it and series data on *book* using ABS ownership indices.

    ASIN is an exact unique identifier — check it first.
    Fall back to normalised title key for books without an ASIN.
    """
    abs_match = (
        abs_owned_by_asin.get(book.get("asin") or "")
        or abs_owned.get(normalize_title_key(book["title"]))
    )
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
            async with sem:
                aud_series, aud_pos = await search_audible_metadata_direct(
                    client, book["title"], author_name
                )
            if aud_series:
                book["series"] = aud_series
                book["series_position"] = aud_pos


async def _enrich_with_abs(
    client: httpx.AsyncClient,
    books: list[dict[str, Any]],
    author_name: str,
    cfg: _ScanConfig,
) -> list[dict[str, Any]]:
    """Bulk-fetch ABS ownership and enrich all books with have_it + series info."""
    abs_owned = await fetch_abs_books_for_author(client, author_name, cfg.abs_url, cfg.abs_token)
    logger.info(
        "ABS ownership loaded",
        extra={"author": author_name, "abs_owned_count": len(abs_owned)},
    )

    # Secondary index by ASIN for books whose ABS title key doesn't align
    # with the metadata-API title (e.g. ABS stores "Series: Book" but the
    # API returns just "Book").
    abs_owned_by_asin: dict[str, dict] = {
        v["asin"]: v for v in abs_owned.values() if v.get("asin")
    }
    sem = asyncio.Semaphore(4)
    await asyncio.gather(*(
        _enrich_book(b, abs_owned, abs_owned_by_asin, client, author_name, sem)
        for b in books
    ))

    # Warn about ABS keys that didn't match any scanned book.
    # Exclude books already matched via ASIN to avoid false positives in the
    # title-key diff (an ASIN-matched book may have a non-matching title key).
    abs_matched = sum(1 for b in books if b.get("have_it"))
    if abs_owned and abs_matched < len(abs_owned):
        asin_matched_title_keys = {
            normalize_title_key(b["title"])
            for b in books
            if b.get("asin") and b["asin"] in abs_owned_by_asin
        }
        unmatched_keys = (
            set(abs_owned)
            - {normalize_title_key(b["title"]) for b in books}
            - asin_matched_title_keys
        )
        if unmatched_keys:
            scanned_keys = {normalize_title_key(b["title"]): b["title"] for b in books}
            logger.warning(
                "ABS unmatched debug: ABS key and any matching scanned book title",
                extra={"unmatched_debug": {k: scanned_keys.get(k) for k in unmatched_keys}},
            )

    return books


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _persist_scan_results(
    session: AsyncSession,
    author_id: int,
    author_name: str,
    books: list[dict[str, Any]],
    cfg: _ScanConfig,
) -> tuple[int, int, list[dict[str, Any]], list[str]]:
    """Upsert all scanned books, run cleanup, discover co-authors, and commit.

    Returns ``(new_books, updated_books, discovered, discovered_coauthors)``.
    """
    # Pre-compute per-book derived fields (narrators, co_authors, serialised strings)
    # so the insert/update paths don't duplicate this work.
    all_fields = [_prepare_book_fields(b, author_name) for b in books]

    # Batch-resolve all co-author names in one pass before the loop to avoid
    # N×M per-book DB lookups (one query per co-author per book).
    all_possible_co_names: set[str] = set()
    for f in all_fields:
        all_possible_co_names.update(f["co_authors"])
    co_author_cache = await _batch_resolve_names(session, all_possible_co_names, cfg.auto_add_coauthors)

    # Pre-load the author's existing books into a dict for O(1) Phase-2
    # title-fallback lookups instead of one DB query per book.
    title_index = await _load_author_book_index(session, author_id)

    new_books = 0
    updated_books = 0
    discovered: list[dict[str, Any]] = []
    all_co_names: set[str] = set()  # accumulated from non-deleted books only
    _BATCH_SIZE = 50

    for idx, (book, fields) in enumerate(zip(books, all_fields), 1):
        try:
            existing, is_cross_author = await _find_existing_book(session, author_id, book, title_index)

            # Never re-add intentionally deleted books
            if existing and existing.deleted:
                continue

            # Accumulate after the deleted-guard so we match original behaviour
            all_co_names.update(fields["co_authors"])

            if not existing:
                new_book, in_discovered = await _insert_new_book(
                    session, book, fields, author_id, co_author_cache
                )
                # Keep the index current so a duplicate title in the same scan
                # batch doesn't get inserted twice.
                title_index[normalize_title_key(book["title"])] = new_book
                new_books += 1
                if in_discovered:
                    discovered.append({
                        "title": book["title"],
                        "author": author_name,
                        "have_it": fields["have_it_bool"],
                        "confidence_band": book.get("confidence_band"),
                    })
            else:
                await _update_existing_book(
                    session, existing, book, fields, author_id, is_cross_author, co_author_cache
                )
                updated_books += 1

        except Exception:
            logger.exception(
                "Failed to persist book",
                extra={"author_id": author_id, "title": book.get("title")},
            )
            await session.rollback()
            continue

        # Batch-flush every N books to bound the session identity map
        if idx % _BATCH_SIZE == 0:
            await session.flush()

    await _cleanup_language(session, author_id, cfg.language_filter)
    discovered_coauthors = await _process_coauthor_discovery(
        session, author_id, author_name, all_co_names, cfg.auto_add_coauthors
    )
    await _update_watchlist(session, author_id)
    await session.commit()

    return new_books, updated_books, discovered, discovered_coauthors


async def _load_author_book_index(
    session: AsyncSession, author_id: int
) -> dict[str, Book]:
    """Pre-load all books for an author, keyed by normalised title.

    Used as the Phase-2 title-fallback lookup inside ``_find_existing_book``
    so the persist loop performs O(1) dict lookups instead of one DB query
    per book.
    """
    q = await session.execute(
        select(Book)
        .join(BookAuthor, Book.id == BookAuthor.book_id)
        .where(and_(BookAuthor.author_id == author_id, BookAuthor.role == "author"))
    )
    return {normalize_title_key(b.title): b for b in q.scalars().all()}


async def _batch_resolve_names(
    session: AsyncSession,
    names: set[str],
    auto_add: bool,
) -> dict[str, Author | None]:
    """Resolve a set of raw author name strings to Author rows in one pass.

    When *auto_add* is True, missing authors are created; otherwise only
    already-tracked authors are returned (missing names map to None).
    """
    cache: dict[str, Author | None] = {}
    for name in sorted(names):
        cache[name] = await _get_or_create_author(session, name) if auto_add else await _find_author(session, name)
    return cache


def _prepare_book_fields(book: dict[str, Any], primary_author_name: str) -> dict[str, Any]:
    """Pre-compute all derived fields from a raw book dict.

    Separates bookkeeping logic (narrator filtering, co-author extraction,
    JSON serialisation) from the insert/update paths so it runs once per book.
    """
    source_val = book.get("source", [])
    source_str = json.dumps(source_val) if isinstance(source_val, list) else (source_val or "")
    score_reasons_str = json.dumps(book.get("score_reasons") or [])
    have_it_bool = bool(book.get("have_it", False))
    narrator_names = [n for n in (book.get("narrators") or []) if n and not _is_contributor_only(n)]
    narrator_str: str | None = ", ".join(narrator_names) if narrator_names else None
    # Build a normalised key set from narrator names to exclude them from the
    # co-author list.  Some APIs (Audnexus) put narrators in the authors array
    # without a role suffix.
    narrator_keys = frozenset(normalize_author_key(n) for n in (book.get("narrators") or []) if n)
    co_authors = [
        a for a in (book.get("authors") or [])
        if not author_names_match(primary_author_name, a)
        and not _is_contributor_only(a)
        and normalize_author_key(a) not in narrator_keys
    ]
    return {
        "source_str": source_str,
        "score_reasons_str": score_reasons_str,
        "have_it_bool": have_it_bool,
        "narrator_str": narrator_str,
        "co_authors": co_authors,
        "published_year": _parse_year(book.get("release_date")),
    }


async def _insert_new_book(
    session: AsyncSession,
    book: dict[str, Any],
    fields: dict[str, Any],
    author_id: int,
    co_author_cache: dict[str, Author | None],
) -> tuple[Book, bool]:
    """Insert a new Book row, its primary-author link, and any co-author links.

    Returns ``(new_book, in_discovered)`` where *in_discovered* is True when
    the book is high/medium confidence or already owned.
    """
    have_it_bool = fields["have_it_bool"]
    new_book = Book(
        title=book["title"],
        title_sort=sort_title(book["title"]),
        subtitle=book.get("subtitle"),
        isbn=book.get("isbn"),
        isbn13=book.get("isbn13"),
        asin=book.get("asin"),
        release_date=str(book.get("release_date") or "") or None,
        published_year=fields["published_year"],
        format=book.get("format"),
        language=book.get("language"),
        source=fields["source_str"],
        cover_url=book.get("cover_url"),
        description=book.get("description"),
        series_name=book.get("series"),
        series_position=book.get("series_position"),
        narrator=fields["narrator_str"],
        have_it=have_it_bool,
        score=book.get("score", 0),
        confidence_band=book.get("confidence_band", "low"),
        score_reasons=fields["score_reasons_str"],
        match_method="audiobookshelf" if have_it_bool else "api",
    )
    session.add(new_book)
    await session.flush()  # populate new_book.id

    # Primary author link
    session.add(BookAuthor(book_id=new_book.id, author_id=author_id, role="author"))

    # Co-author links — cache already contains the right Author objects (or None)
    # based on whether auto_add_coauthors was enabled at resolution time.
    seen_co_ids: set[int] = set()
    for co_name in fields["co_authors"]:
        co_author = co_author_cache.get(co_name)
        if co_author and co_author.id not in seen_co_ids:
            seen_co_ids.add(co_author.id)
            session.add(BookAuthor(book_id=new_book.id, author_id=co_author.id, role="co-author"))

    in_discovered = bool(book.get("confidence_band") in ("high", "medium") or have_it_bool)
    return new_book, in_discovered


async def _update_existing_book(
    session: AsyncSession,
    existing: Book,
    book: dict[str, Any],
    fields: dict[str, Any],
    author_id: int,
    is_cross_author: bool,
    co_author_cache: dict[str, Author | None],
) -> None:
    """Update an existing Book row and reconcile its co-author links."""
    # Cross-author hit: promote scanning author to primary author
    if is_cross_author:
        session.add(BookAuthor(book_id=existing.id, author_id=author_id, role="author"))
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
    if fields["have_it_bool"]:
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
        ("narrator", fields["narrator_str"]),
    ):
        if not getattr(existing, attr) and new_val:
            setattr(existing, attr, new_val)

    existing.score = book.get("score", 0)
    existing.confidence_band = book.get("confidence_band", "low")
    existing.score_reasons = fields["score_reasons_str"]
    existing.updated_at = datetime.now(timezone.utc)

    # Full set-reconcile for co-author links.
    # Lock the book row to prevent concurrent scans (e.g. overlapping
    # author scans that share books) from racing on the co-author set.
    await session.execute(
        select(Book).where(Book.id == existing.id).with_for_update()
    )
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
    for co_name in fields["co_authors"]:
        co_author = co_author_cache.get(co_name)
        if co_author:
            if co_author.id not in existing_co_ids and co_author.id not in fresh_co_ids:
                session.add(BookAuthor(book_id=existing.id, author_id=co_author.id, role="co-author"))
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


async def _cleanup_language(
    session: AsyncSession, author_id: int, language_filter: str
) -> None:
    """Soft-delete unowned books whose language is known but doesn't match the filter.

    Handles books that were persisted before strict language filtering was in
    place (e.g. Polish editions written before the language filter was set).
    """
    if not language_filter or language_filter == "all":
        return
    result = await session.execute(
        select(Book)
        .join(BookAuthor, and_(BookAuthor.book_id == Book.id, BookAuthor.author_id == author_id))
        .where(
            Book.deleted.is_(False),
            Book.have_it.is_(False),
            Book.language.isnot(None),
            Book.language != language_filter,
        )
    )
    for stale_book in result.scalars().all():
        stale_book.deleted = True
        stale_book.updated_at = datetime.now(timezone.utc)
        logger.info(
            "Soft-deleted non-matching language book",
            extra={"book_id": stale_book.id, "title": stale_book.title, "language": stale_book.language},
        )


async def _process_coauthor_discovery(
    session: AsyncSession,
    author_id: int,
    author_name: str,
    co_names: set[str],
    auto_add_coauthors: bool,
) -> list[str]:
    """Return co-author names not yet on the watchlist; optionally auto-add them."""
    discovered: list[str] = []
    for co_name in sorted(co_names):
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
                    co_obj = Author(
                        name=co_name,
                        name_sort=sort_name(co_name),
                        name_normalized=normalize_author_key(co_name),
                    )
                    session.add(co_obj)
                    await session.flush()
                session.add(Watchlist(author_id=co_obj.id))
            discovered.append(co_name)
    return discovered


async def _update_watchlist(session: AsyncSession, author_id: int) -> None:
    """Stamp last_scanned on the watchlist entry for this author."""
    wl_result = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    watchlist = wl_result.scalar_one_or_none()
    if watchlist:
        watchlist.last_scanned = datetime.now(timezone.utc)


async def _publish_events(
    redis_client: Any,
    author_id: int,
    author_name: str,
    new_books: int,
    updated_books: int,
    books_found: int,
    discovered: list[dict[str, Any]],
    discovered_coauthors: list[str],
    auto_add_coauthors: bool,
) -> None:
    """Publish scan.complete and coauthor.discovered events to Redis pub/sub."""
    if not redis_client:
        return
    if books_found:
        await redis_client.publish(
            "bookscout:events",
            json.dumps({
                "event": "scan.complete",
                "author_id": author_id,
                "author_name": author_name,
                "books_found": books_found,
                "new_books": new_books,
                "updated_books": updated_books,
                "discovered": discovered,
            }),
        )
    if discovered_coauthors:
        await redis_client.publish(
            "bookscout:events",
            json.dumps({
                "event": "coauthor.discovered",
                "author_id": author_id,
                "author_name": author_name,
                "coauthors": discovered_coauthors,
                "auto_added": auto_add_coauthors,
            }),
        )


# ---------------------------------------------------------------------------
# Low-level DB helpers
# ---------------------------------------------------------------------------

async def _find_existing_book(
    session: AsyncSession,
    author_id: int,
    book: dict[str, Any],
    title_index: dict[str, Book] | None = None,
) -> tuple[Book | None, bool]:
    """Return ``(existing_book_or_None, is_cross_author_hit)``.

    Phase 1 — global identity lookup by isbn13/isbn/asin with *no* author
    filter.  Any existing book row for the same identifier is considered the
    same book, regardless of who originally added it.

    Phase 2 — author-scoped title fallback.  When *title_index* is provided
    (pre-built by ``_load_author_book_index``), this is an O(1) dict lookup;
    otherwise a DB query is issued as a fallback for callers without a cache.

    ``is_cross_author_hit`` is True when a book was found via Phase 1 but the
    scanning author is not yet recorded as a primary (``role='author'``)
    contributor.  The calling code is responsible for adding that link and
    removing any stale ``role='co-author'`` row for the same person.
    """
    # Phase 1: global identifier lookup — no author filter; include soft-deleted
    # rows so the caller's guard can prevent re-creation of deleted books.
    try:
        for field, value in (
            (Book.isbn13, book.get("isbn13")),
            (Book.isbn, book.get("isbn")),
            (Book.asin, book.get("asin")),
        ):
            if value:
                q = await session.execute(
                    select(Book).where(field == value).order_by(Book.created_at.asc())
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

        if title_index is not None:
            found = title_index.get(tkey)
            return (found, False) if found else (None, False)

        # DB fallback — used when no pre-built index is available (e.g. tests).
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

    except Exception:
        logger.exception(
            "_find_existing_book failed",
            extra={"author_id": author_id, "title": book.get("title")},
        )
        raise
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

    # 3b. Fuzzy fallback — O(n) scan, capped as a safety valve
    logger.warning("_find_author falling back to full-table fuzzy match", extra={"author_name": name})
    all_result = await session.execute(select(Author).limit(1000))
    for existing in all_result.scalars():
        if author_names_match(name, existing.name):
            return existing

    return None


async def _get_or_create_author(session: AsyncSession, name: str) -> Author:
    """Find an existing Author or create a new one.

    Delegates lookup to ``_find_author`` so the resolution sequence
    (exact → alias → normalised key → fuzzy) is defined in one place.
    """
    author = await _find_author(session, name)
    if author:
        await _record_alias(session, author, name, "scan")
        return author

    # No match — create a new author row and seed its canonical name as
    # the first alias.
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

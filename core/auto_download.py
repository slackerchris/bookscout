"""Automatic downloading of newly discovered books.

After each scan of an author whose watchlist entry has ``auto_download``
enabled, this module finds their HIGH-confidence, released, missing books,
searches the configured indexers, and either:

- ``auto_download_mode = "approval"`` (default): records the best match as a
  *pending* download attempt for one-click approval in the UI, or
- ``auto_download_mode = "auto"``: sends the best match straight to the
  download client and records the attempt.

Dedup rules: a book is skipped when it already has a queued/pending attempt,
or any attempt in the last 24 hours (so a failing grab doesn't retry on
every hourly scan).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.search import send_release, unified_search
from db.models import Author, Book, BookAuthor, DownloadAttempt, Watchlist

logger = logging.getLogger(__name__)

_RETRY_COOLDOWN = timedelta(hours=24)
_BLOCKING_STATUSES = ("queued", "pending")


def parse_release_date(raw: Any) -> date | None:
    """Parse the free-text release_date column: ISO date or bare year."""
    if not raw:
        return None
    text = str(raw).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}", text):
        # Year-only dates count as released once the year has started.
        return date(int(text), 1, 1)
    return None


def book_is_eligible(book: Book, today: date) -> bool:
    """HIGH-confidence, missing, live, and actually released."""
    if book.have_it or book.deleted or book.canonical_book_id is not None:
        return False
    if book.confidence_band != "high":
        return False
    released = parse_release_date(book.release_date)
    return released is not None and released <= today


def select_best_result(
    results: list[dict[str, Any]], prefs: dict[str, Any]
) -> dict[str, Any] | None:
    """Pick the best indexer result under the user's download preferences.

    Hard filters: min_seeders (torrents only) and max_size_gb.  The preferred
    format is a soft filter — applied only when at least one result matches,
    so a lone mp3 release still gets picked when you prefer m4b.
    """
    min_seeders = int(prefs.get("min_seeders") or 0)
    max_size_gb = float(prefs.get("max_size_gb") or 0)
    preferred_format = str(prefs.get("preferred_format") or "").lower()

    viable = []
    for r in results:
        rtype = r.get("type", "torrent")
        if rtype == "torrent" and int(r.get("seeders") or 0) < min_seeders:
            continue
        size = int(r.get("size") or 0)
        if max_size_gb and size > max_size_gb * 1024**3:
            continue
        if not r.get("download_url") and not r.get("url"):
            continue
        viable.append(r)

    if not viable:
        return None

    if preferred_format:
        matching = [r for r in viable if preferred_format in str(r.get("title", "")).lower()]
        if matching:
            viable = matching

    # unified_search already sorts by (seeders, size) desc; keep that order.
    return viable[0]


async def _eligible_books(session: AsyncSession, author_id: int) -> list[Book]:
    q = await session.execute(
        select(Book)
        .join(
            BookAuthor,
            and_(
                BookAuthor.book_id == Book.id,
                BookAuthor.author_id == author_id,
                BookAuthor.role == "author",
            ),
        )
        .where(
            Book.deleted.is_(False),
            Book.have_it.is_(False),
            Book.canonical_book_id.is_(None),
            Book.confidence_band == "high",
        )
    )
    today = datetime.now(timezone.utc).date()
    # primary_author_id gate: a co-authored book auto-downloads under its
    # primary author only, so two watched co-authors can't both grab it.
    return [
        b for b in q.scalars().all()
        if book_is_eligible(b, today) and (b.primary_author_id in (None, author_id))
    ]


async def _blocked_book_ids(
    session: AsyncSession, book_ids: list[int], *, respect_cooldown: bool = True
) -> set[int]:
    """Books with a queued/pending attempt — and, for the automatic post-scan
    pass (``respect_cooldown=True``), any attempt in the cooldown window so a
    failing release isn't retried on every hourly scan.  Explicit user
    requests bypass the cooldown: only in-flight attempts block them.
    """
    if not book_ids:
        return set()
    condition = DownloadAttempt.status.in_(_BLOCKING_STATUSES)
    if respect_cooldown:
        cutoff = datetime.now(timezone.utc) - _RETRY_COOLDOWN
        condition = condition | (DownloadAttempt.created_at >= cutoff)
    q = await session.execute(
        select(DownloadAttempt.book_id).where(
            DownloadAttempt.book_id.in_(book_ids),
            condition,
        )
    )
    return {row[0] for row in q.all()}


async def run_auto_download_for_author(
    session: AsyncSession,
    author_id: int,
    config: Any,
    redis_client: Any = None,
) -> dict[str, Any]:
    """Search + grab/queue eligible books for one author.  Returns a summary."""
    wl_q = await session.execute(select(Watchlist).where(Watchlist.author_id == author_id))
    wl = wl_q.scalar_one_or_none()
    if wl is None or not wl.auto_download:
        return {"enabled": False}

    author = await session.get(Author, author_id)
    if author is None:
        return {"enabled": False}

    prefs = await _load_download_prefs(session)
    mode = str(prefs.get("auto_download_mode") or "approval")

    books = await _eligible_books(session, author_id)
    blocked = await _blocked_book_ids(session, [b.id for b in books])
    candidates = [b for b in books if b.id not in blocked]
    if not candidates:
        return {"enabled": True, "candidates": 0, "sent": 0, "pending": 0}

    sent = 0
    pending = 0
    async with httpx.AsyncClient() as client:
        for book in candidates:
            outcome = await _search_and_record(
                session, client, config, prefs, book, author.name, author_id,
                mode=mode, redis_client=redis_client,
            )
            if outcome == "sent":
                sent += 1
            elif outcome == "pending":
                pending += 1

    return {"enabled": True, "candidates": len(candidates), "sent": sent, "pending": pending}


async def _search_and_record(
    session: AsyncSession,
    client: httpx.AsyncClient,
    config: Any,
    prefs: dict[str, Any],
    book: Book,
    author_name: str,
    author_id: int | None,
    *,
    mode: str,
    redis_client: Any = None,
) -> str:
    """Search the indexers for one book, pick the best match, and either send
    it or record it as pending.  Returns "sent" | "pending" | "failed" | "none".
    """
    prowlarr = getattr(config, "prowlarr", None)
    jackett = getattr(config, "jackett", None)
    query = f"{book.title} {author_name}".strip()
    try:
        results = await unified_search(
            client,
            query,
            prowlarr_url=getattr(prowlarr, "url", "") if prowlarr else "",
            prowlarr_key=getattr(prowlarr, "api_key", "") if prowlarr else "",
            jackett_url=getattr(jackett, "url", "") if jackett else "",
            jackett_key=getattr(jackett, "api_key", "") if jackett else "",
        )
    except Exception as exc:
        logger.warning(
            "Auto-download search failed",
            extra={"book_id": book.id, "error": str(exc)},
        )
        return "none"

    best = select_best_result(results, prefs)
    if best is None:
        return "none"

    url = best.get("download_url") or best.get("url") or ""
    release_type = best.get("type", "torrent")
    attempt = DownloadAttempt(
        book_id=book.id,
        book_title=book.title,
        query=query,
        release_title=best.get("title", ""),
        indexer=best.get("indexer"),
        source=best.get("source"),
        type=release_type,
        size_bytes=best.get("size"),
        seeders=best.get("seeders"),
        download_url=url,
    )

    if mode == "auto":
        result = await send_release(
            client, config, url=url, title=best.get("title", ""),
            release_type=release_type, book_id=book.id,
        )
        ok = bool(result.get("success"))
        attempt.status = "queued" if ok else "failed"
        attempt.error_detail = None if ok else result.get("detail")
        event = "autodownload.sent" if ok else "autodownload.failed"
        outcome = "sent" if ok else "failed"
    else:
        attempt.status = "pending"
        event = "autodownload.pending"
        outcome = "pending"

    session.add(attempt)
    await session.commit()
    await _publish(redis_client, {
        "event": event,
        "book_id": book.id,
        "book_title": book.title,
        "author_id": author_id,
        "author_name": author_name,
        "release_title": best.get("title", ""),
    })
    return outcome


async def request_downloads_for_books(
    session: AsyncSession,
    book_ids: list[int],
    config: Any,
    redis_client: Any = None,
) -> dict[str, Any]:
    """User-triggered batch: find the best indexer match for each book and
    queue it as a *pending* download attempt for approval.

    Used by "search all missing" on the Series page.  Unlike the post-scan
    auto-download pass this ignores watchlist.auto_download (the user asked
    explicitly), but it still skips unreleased books and books that already
    have a queued/pending attempt.
    """
    q = await session.execute(select(Book).where(Book.id.in_(book_ids)))
    books = list(q.scalars().all())

    today = datetime.now(timezone.utc).date()
    eligible = [
        b for b in books
        if not b.have_it and not b.deleted and b.canonical_book_id is None
        and (parse_release_date(b.release_date) or today) <= today
    ]
    # User-triggered: bypass the failure cooldown — an explicit click may
    # always retry.  Only in-flight (queued/pending) attempts block.
    blocked = await _blocked_book_ids(
        session, [b.id for b in eligible], respect_cooldown=False
    )
    candidates = [b for b in eligible if b.id not in blocked]

    prefs = await _load_download_prefs(session)

    # Resolve author names for search queries in one query.
    author_ids = {b.primary_author_id for b in candidates if b.primary_author_id}
    names: dict[int, str] = {}
    if author_ids:
        aq = await session.execute(select(Author.id, Author.name).where(Author.id.in_(author_ids)))
        names = dict(aq.all())

    queued = 0
    no_match: list[str] = []
    async with httpx.AsyncClient() as client:
        for book in candidates:
            author_name = names.get(book.primary_author_id or 0, "")
            outcome = await _search_and_record(
                session, client, config, prefs, book, author_name,
                book.primary_author_id, mode="approval", redis_client=redis_client,
            )
            if outcome == "pending":
                queued += 1
            else:
                no_match.append(book.title)

    return {
        "requested": len(book_ids),
        "candidates": len(candidates),
        "skipped": len(book_ids) - len(candidates),
        "queued": queued,
        "no_match": no_match,
    }


async def _load_download_prefs(session: AsyncSession) -> dict[str, Any]:
    from db.models import AppSetting

    row = await session.get(AppSetting, "download_preferences")
    return dict(row.value) if row and isinstance(row.value, dict) else {}


async def _publish(redis_client: Any, payload: dict[str, Any]) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.publish("bookscout:events", json.dumps(payload))
    except Exception as exc:
        logger.warning("Auto-download event publish failed", extra={"error": str(exc)})

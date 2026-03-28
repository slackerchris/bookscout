"""Audiobookshelf integration endpoints."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_config
from core.audiobookshelf import get_all_authors_from_audiobookshelf, get_all_books_from_audiobookshelf
from core.normalize import author_names_match, normalize_author_key, normalize_title_key, sort_name, sort_title
from core.scan import _is_contributor_only
from db.models import Author, Book, BookAuthor, Watchlist
from db.session import get_session

router = APIRouter(prefix="/audiobookshelf", tags=["audiobookshelf"])


@router.post(
    "/import-authors",
    summary="Bulk-import all Audiobookshelf authors into the watchlist",
)
async def import_authors(session: AsyncSession = Depends(get_session)) -> dict:
    """Fetch every author name from ABS libraries and add any unknown ones to the watchlist."""
    config = get_config()
    abs_cfg = getattr(config, "audiobookshelf", None)
    abs_url = getattr(abs_cfg, "url", "") if abs_cfg else ""
    abs_token = getattr(abs_cfg, "token", "") if abs_cfg else ""

    async with httpx.AsyncClient() as client:
        author_names = await get_all_authors_from_audiobookshelf(client, abs_url, abs_token)

    # Load all existing author names once so we can fuzzy-match against them.
    # This prevents "J.N. Chaney" and "JN Chaney" from creating two rows.
    existing_result = await session.execute(select(Author.name))
    existing_names: list[str] = [row[0] for row in existing_result.all()]

    added = 0
    for name in author_names:
        if _is_contributor_only(name):
            continue
        if any(author_names_match(name, ex) for ex in existing_names):
            continue
        author = Author(name=name, name_sort=sort_name(name), name_normalized=normalize_author_key(name))
        session.add(author)
        await session.flush()
        session.add(Watchlist(author_id=author.id))
        existing_names.append(name)  # guard against two variants in the same batch
        added += 1

    await session.commit()
    return {
        "imported": added,
        "skipped": len(author_names) - added,
        "total_from_abs": len(author_names),
    }


@router.post(
    "/sync-books",
    summary="Import all Audiobookshelf books and enqueue metadata scans",
)
async def sync_books(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Walk every ABS library and import owned books into the DB.

    For each item found in ABS:
    - Creates the author (+ watchlist entry) if not already known
    - Creates the book with ``have_it=True`` if not already present,
      using ABS series / position / ISBN / ASIN data directly
    - If the book already exists, marks it as owned and fills in any
      missing series/identifier fields

    After the import a metadata scan is enqueued for every affected author
    so that descriptions, cover URLs, and additional series info are filled
    in by the normal scan pipeline.

    Returns a summary dict with counts and the list of enqueued job IDs.
    """
    config = get_config()
    abs_cfg = getattr(config, "audiobookshelf", None)
    abs_url = getattr(abs_cfg, "url", "") if abs_cfg else ""
    abs_token = getattr(abs_cfg, "token", "") if abs_cfg else ""

    async with httpx.AsyncClient() as client:
        abs_books = await get_all_books_from_audiobookshelf(client, abs_url, abs_token)

    # Load all existing authors for fuzzy matching
    existing_result = await session.execute(select(Author))
    existing_authors: list[Author] = list(existing_result.scalars().all())

    # Build a normalised-key index for O(1) lookups; fall back to fuzzy only on miss
    _author_key_index: dict[str, Author] = {
        normalize_author_key(a.name): a for a in existing_authors
    }

    def _find_author_fast(name: str) -> Author | None:
        key = normalize_author_key(name)
        hit = _author_key_index.get(key)
        if hit:
            return hit
        # Fuzzy fallback for initial-expansion variants
        for a in existing_authors:
            if author_names_match(name, a.name):
                _author_key_index[key] = a  # cache for next lookup
                return a
        return None

    new_books = 0
    updated_books = 0
    affected_author_ids: set[int] = set()

    for item in abs_books:
        title: str = item["title"]
        author_name: str | None = item["author_name"]
        if not author_name or _is_contributor_only(author_name):
            continue

        # ---- find or create the author --------------------------------
        author_obj: Author | None = _find_author_fast(author_name)
        if author_obj is None:
            author_obj = Author(
                name=author_name,
                name_sort=sort_name(author_name),
                name_normalized=normalize_author_key(author_name),
            )
            session.add(author_obj)
            await session.flush()
            session.add(Watchlist(author_id=author_obj.id))
            existing_authors.append(author_obj)
            _author_key_index[normalize_author_key(author_name)] = author_obj

        affected_author_ids.add(author_obj.id)

        # ---- check for existing book (asin > isbn > norm title) -------
        existing_book: Book | None = None
        for field, value in (
            (Book.asin,   item.get("asin")),
            (Book.isbn,   item.get("isbn")),
        ):
            if value:
                q = await session.execute(
                    select(Book).where(field == value, Book.deleted.is_(False))
                )
                existing_book = q.scalar_one_or_none()
                if existing_book:
                    break

        if existing_book is None:
            # Title + author fallback
            tkey = normalize_title_key(title)
            q = await session.execute(
                select(Book)
                .join(BookAuthor, and_(
                    BookAuthor.book_id == Book.id,
                    BookAuthor.author_id == author_obj.id,
                    BookAuthor.role == "author",
                ))
                .where(Book.deleted.is_(False))
            )
            for candidate in q.scalars().all():
                if normalize_title_key(candidate.title) == tkey:
                    existing_book = candidate
                    break

        if existing_book:
            # Update ownership and fill missing fields
            changed = False
            if not existing_book.have_it:
                existing_book.have_it = True
                existing_book.match_method = "audiobookshelf"
                changed = True
            for attr, val in (
                ("series_name",     item.get("series_name")),
                ("series_position", item.get("series_position")),
                ("asin",            item.get("asin")),
                ("isbn",            item.get("isbn")),
                ("cover_url",       item.get("cover_url")),
            ):
                if not getattr(existing_book, attr) and val:
                    setattr(existing_book, attr, val)
                    changed = True
            if changed:
                updated_books += 1
        else:
            new_book = Book(
                title=title,
                title_sort=sort_title(title),
                series_name=item.get("series_name"),
                series_position=item.get("series_position"),
                asin=item.get("asin"),
                isbn=item.get("isbn"),
                cover_url=item.get("cover_url"),
                have_it=True,
                match_method="audiobookshelf",
                source='["audiobookshelf"]',
                score=0,
                confidence_band="medium",
            )
            session.add(new_book)
            await session.flush()
            session.add(BookAuthor(book_id=new_book.id, author_id=author_obj.id, role="author"))
            new_books += 1

    await session.commit()

    # Enqueue metadata scans for all affected authors
    job_ids: list[str] = []
    arq = getattr(request.app.state, "arq_pool", None)
    if arq:
        for author_id in affected_author_ids:
            job = await arq.enqueue_job("scan_author_task", author_id)
            job_ids.append(job.job_id)

    return {
        "new_books": new_books,
        "updated_books": updated_books,
        "authors_affected": len(affected_author_ids),
        "scans_enqueued": len(job_ids),
        "total_from_abs": len(abs_books),
    }




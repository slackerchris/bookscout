"""Books CRUD."""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_config
from core.enqueue import author_scan_job_id, enqueue_unique
from core.metadata import normalize_language_code
from core.normalize import normalize_title_key, sort_title
from core.search import humanize_size, unified_search
from db.models import Author, Book, BookAuthor
from db.session import get_session

router = APIRouter(prefix="/books", tags=["books"])


def _normalise_book_language(book: Book) -> Book:
    book.language = normalize_language_code(book.language)
    return book


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class BookOut(BaseModel):
    id: int
    title: str
    title_sort: str
    subtitle: str | None = None
    isbn: str | None = None
    isbn13: str | None = None
    asin: str | None = None
    release_date: str | None = None
    published_year: int | None = None
    series_name: str | None = None
    series_position: str | None = None
    format: str | None = None
    source: str | None = None
    cover_url: str | None = None
    description: str | None = None
    narrator: str | None = None
    score: int
    confidence_band: str
    score_reasons: str | None = None
    language: str | None = None
    have_it: bool
    deleted: bool
    match_method: str
    primary_author_id: int | None = None
    primary_author_manual: bool = False
    canonical_book_id: int | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BookWithAuthorOut(BookOut):
    author_id: int
    author_name: str


class CoAuthorEntry(BaseModel):
    """Minimal author record used inside BookWithAllAuthorsOut."""
    id: int
    name: str
    author_order: int | None = None


class BookWithAllAuthorsOut(BookOut):
    """Extended response for endpoints that need all credited authors, not just primary."""
    primary_author_id: int
    primary_author_name: str
    all_authors: list[CoAuthorEntry]


class BookUpdate(BaseModel):
    have_it: bool | None = None
    title: str | None = None
    language: str | None = None
    series_name: str | None = None
    series_position: str | None = None
    subtitle: str | None = None
    deleted: bool | None = None
    asin: str | None = None
    isbn: str | None = None
    isbn13: str | None = None
    narrator: str | None = None
    release_date: str | None = None
    primary_author_id: int | None = None
    primary_author_manual: bool | None = None
    canonical_book_id: int | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _book_with_author_out(book: Book, author_id: int, author_name: str) -> BookWithAuthorOut:
    book = _normalise_book_language(book)
    data = BookOut.model_validate(book).model_dump()
    return BookWithAuthorOut(**data, author_id=author_id, author_name=author_name)


def _upcoming_release_condition(today: str):
    """Handle both full ISO dates and older year-only release values."""
    current_year = today[:4]
    return and_(
        Book.release_date.is_not(None),
        (
            (func.length(Book.release_date) == 4) & (Book.release_date >= current_year)
        ) | (
            (func.length(Book.release_date) > 4) & (Book.release_date >= today)
        ),
    )


@router.get("/recently-imported", summary="Books most recently imported")
async def recently_imported(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[BookOut]:
    """Return the most recently imported books (match_method='imported'), newest first.

    Ordered by created_at (immutable insert time) rather than updated_at, which
    changes on any subsequent metadata update and would produce incorrect ordering.
    """
    q = (
        select(Book)
        .where(Book.match_method == "imported", Book.deleted.is_(False))
        .order_by(Book.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(q)
    return [_normalise_book_language(book) for book in result.scalars().all()]


@router.get("/recently-discovered", response_model=list[BookWithAuthorOut], summary="Books most recently discovered")
async def recently_discovered(
    limit: int = Query(50, ge=1, le=200),
    missing_only: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> list[BookWithAuthorOut]:
    """Return recently created catalog rows, newest first.

    Joins via primary_author_id so each book appears exactly once regardless of
    how many watched co-authors it has.  Canonical duplicates are excluded.
    """
    q = (
        select(Book, Author.id, Author.name)
        .join(Author, Author.id == Book.primary_author_id)
        .where(Book.deleted.is_(False), Book.canonical_book_id.is_(None))
        .order_by(Book.created_at.desc())
        .limit(limit)
    )
    if missing_only:
        q = q.where(Book.have_it.is_(False))
    result = await session.execute(q)
    return [_book_with_author_out(book, author_id, author_name) for book, author_id, author_name in result.all()]


@router.get("/upcoming", response_model=list[BookWithAuthorOut], summary="Upcoming audiobook releases")
async def upcoming_books(
    limit: int = Query(100, ge=1, le=500),
    missing_only: bool = Query(True),
    confidence_band: str | None = Query(None, description="high | medium | low"),
    session: AsyncSession = Depends(get_session),
) -> list[BookWithAuthorOut]:
    """Return books with release dates today or later, ordered by release date.

    Joins via primary_author_id so co-authored books appear once under their
    primary author only.  Canonical duplicates (canonical_book_id IS NOT NULL)
    are excluded.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    q = (
        select(Book, Author.id, Author.name)
        .join(Author, Author.id == Book.primary_author_id)
        .where(
            Book.deleted.is_(False),
            Book.canonical_book_id.is_(None),
            _upcoming_release_condition(today),
        )
        .order_by(Book.release_date.asc(), Book.title_sort.asc())
        .limit(limit)
    )
    if missing_only:
        q = q.where(Book.have_it.is_(False))
    if confidence_band is not None:
        q = q.where(Book.confidence_band == confidence_band)
    result = await session.execute(q)
    return [_book_with_author_out(book, author_id, author_name) for book, author_id, author_name in result.all()]


@router.get("/summary", summary="Book catalog summary")
async def book_summary(session: AsyncSession = Depends(get_session)) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()

    async def count_where(*conditions) -> int:
        result = await session.execute(
            select(func.count(Book.id)).where(
                Book.deleted.is_(False),
                Book.canonical_book_id.is_(None),
                *conditions,
            )
        )
        return int(result.scalar_one())

    total = await count_where()
    missing = await count_where(Book.have_it.is_(False))
    high_conf_missing = await count_where(Book.have_it.is_(False), Book.confidence_band == "high")
    upcoming_missing = await count_where(
        Book.have_it.is_(False),
        _upcoming_release_condition(today),
    )
    no_release_date = await count_where(Book.release_date.is_(None))

    return {
        "total": total,
        "missing": missing,
        "high_confidence_missing": high_conf_missing,
        "upcoming_missing": upcoming_missing,
        "no_release_date": no_release_date,
    }


@router.get("/count", summary="Count books matching filter criteria")
async def count_books(
    author_id: int | None = Query(None, description="Filter by primary author"),
    confidence_band: str | None = Query(None, description="high | medium | low"),
    have_it: bool | None = Query(None, description="Owned flag filter"),
    missing_only: bool = Query(False, description="Shorthand for have_it=false"),
    include_deleted: bool = Query(False),
    updated_since: datetime | None = Query(
        None,
        description="ISO 8601 timestamp; count only books updated after this value.",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return ``{"count": N}`` — identical filter semantics to ``GET /books/``
    but cheap enough to use for dashboard stat cards."""
    q = select(func.count(Book.id))

    if not include_deleted:
        q = q.where(Book.deleted.is_(False))

    if author_id is not None:
        q = q.join(
            BookAuthor,
            and_(
                BookAuthor.book_id == Book.id,
                BookAuthor.author_id == author_id,
                BookAuthor.role == "author",
            ),
        )

    if confidence_band is not None:
        q = q.where(Book.confidence_band == confidence_band)

    if missing_only:
        q = q.where(Book.have_it.is_(False))
    elif have_it is not None:
        q = q.where(Book.have_it == have_it)

    if updated_since is not None:
        q = q.where(Book.updated_at > updated_since)

    result = await session.execute(q)
    return {"count": result.scalar_one()}


@router.get("/", response_model=list[BookOut], summary="List books")
async def list_books(
    author_id: int | None = Query(None, description="Filter by primary author"),
    confidence_band: str | None = Query(None, description="high | medium | low"),
    have_it: bool | None = Query(None, description="Owned flag filter"),
    missing_only: bool = Query(False, description="Shorthand for have_it=false"),
    include_deleted: bool = Query(False),
    updated_since: datetime | None = Query(
        None,
        description=(
            "ISO 8601 timestamp; return only books whose updated_at is after this "
            "value.  Useful for polling workflows (e.g. n8n) that need only the "
            "delta since their last run."
        ),
    ),
    limit: int = Query(500, ge=1, le=500, description="Max results to return (1–500)"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    session: AsyncSession = Depends(get_session),
) -> list[Book]:
    q = select(Book)

    if not include_deleted:
        q = q.where(Book.deleted.is_(False))

    if author_id is not None:
        q = q.join(
            BookAuthor,
            and_(
                BookAuthor.book_id == Book.id,
                BookAuthor.author_id == author_id,
                BookAuthor.role == "author",
            ),
        )

    if confidence_band is not None:
        q = q.where(Book.confidence_band == confidence_band)

    if missing_only:
        q = q.where(Book.have_it.is_(False))
    elif have_it is not None:
        q = q.where(Book.have_it == have_it)

    if updated_since is not None:
        q = q.where(Book.updated_at > updated_since)

    q = q.order_by(Book.title_sort).limit(limit).offset(offset)
    result = await session.execute(q)
    return [_normalise_book_language(book) for book in result.scalars().all()]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.get("/export", summary="Export all books as a downloadable JSON file")
async def export_books(
    session: AsyncSession = Depends(get_session),
) -> FastAPIResponse:
    """Download all non-deleted books with author info as a JSON file."""
    q = (
        select(Book, Author.id, Author.name)
        .join(BookAuthor, and_(BookAuthor.book_id == Book.id, BookAuthor.role == "author"))
        .join(Author, Author.id == BookAuthor.author_id)
        .where(Book.deleted.is_(False))
        .order_by(Author.name_sort, Book.title_sort)
    )
    result = await session.execute(q)
    books = []
    seen_book_ids: set[int] = set()
    for book, author_id, author_name in result.all():
        if book.id in seen_book_ids:
            continue
        seen_book_ids.add(book.id)
        bd = BookOut.model_validate(book).model_dump(mode="json")
        bd["author_id"] = author_id
        bd["author_name"] = author_name
        books.append(bd)

    payload = _json.dumps(
        {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "total": len(books),
            "books": books,
        },
        indent=2,
        default=str,
    ).encode()

    return FastAPIResponse(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="bookscout-export.json"'},
    )


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

@router.get("/duplicates", summary="Find likely duplicate book entries")
async def find_duplicates(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return groups of 2+ books that share the same normalised title + primary author."""
    q = (
        select(Book, Author.id, Author.name)
        .join(Author, Author.id == Book.primary_author_id)
        .where(Book.deleted.is_(False), Book.canonical_book_id.is_(None))
        .order_by(Author.name_sort, Book.title_sort)
    )
    result = await session.execute(q)

    groups: dict[tuple, list[dict]] = {}
    for book, author_id, author_name in result.all():
        key = (author_id, normalize_title_key(book.title))
        if key not in groups:
            groups[key] = []
        bd = BookOut.model_validate(book).model_dump(mode="json")
        bd["author_id"] = author_id
        bd["author_name"] = author_name
        groups[key].append(bd)

    return [
        {"author_id": aid, "author_name": books[0]["author_name"], "books": books}
        for (aid, _), books in sorted(groups.items(), key=lambda x: x[1][0]["author_name"])
        if len(books) >= 2
    ]


@router.get("/co-author-conflicts", summary="Books with multiple watched co-authors as primary authors")
async def co_author_conflicts(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return books where 2+ watched (watchlisted) authors all have role='author'.

    These are co-authored books where BookScout is tracking multiple credited
    authors.  The management UI uses this to let the user designate which author
    is truly primary so the book appears only once in list views.

    Each group contains the book's data, the current primary author, and the full
    list of co-primary authors with their billing order (author_order) if known.
    """
    from db.models import Watchlist

    # Find all books where 2+ watched authors have role='author'
    watched_author_subq = select(Watchlist.author_id).scalar_subquery()

    q = (
        select(
            Book.id,
            BookAuthor.author_id,
            Author.name,
            Author.name_sort,
            BookAuthor.author_order,
        )
        .join(BookAuthor, and_(BookAuthor.book_id == Book.id, BookAuthor.role == "author"))
        .join(Author, Author.id == BookAuthor.author_id)
        .where(
            Book.deleted.is_(False),
            Book.canonical_book_id.is_(None),
            BookAuthor.author_id.in_(watched_author_subq),
        )
        .order_by(Book.id, BookAuthor.author_order.asc().nullslast(), Author.name_sort)
    )
    rows = (await session.execute(q)).all()

    # Group by book_id; keep only books with 2+ watched authors
    book_author_map: dict[int, list[dict]] = {}
    for book_id, author_id, author_name, _, author_order in rows:
        book_author_map.setdefault(book_id, []).append({
            "author_id": author_id,
            "author_name": author_name,
            "author_order": author_order,
        })
    conflict_book_ids = [bid for bid, authors in book_author_map.items() if len(authors) >= 2]

    if not conflict_book_ids:
        return []

    # Fetch full book data for conflicting books
    books_q = (
        select(Book, Author.id, Author.name)
        .join(Author, Author.id == Book.primary_author_id)
        .where(Book.id.in_(conflict_book_ids))
        .order_by(Author.name_sort, Book.title_sort)
    )
    books_result = (await session.execute(books_q)).all()

    out = []
    for book, primary_author_id, primary_author_name in books_result:
        bd = BookOut.model_validate(_normalise_book_language(book)).model_dump(mode="json")
        out.append({
            **bd,
            "primary_author_id": primary_author_id,
            "primary_author_name": primary_author_name,
            "all_authors": book_author_map[book.id],
        })
    return out


@router.get("/{book_id}", response_model=BookOut, summary="Get a single book")
async def get_book(
    book_id: int,
    session: AsyncSession = Depends(get_session),
) -> Book:
    book = await _get_or_404(session, book_id)
    return _normalise_book_language(book)


@router.patch("/{book_id}", response_model=BookOut, summary="Update book fields")
async def update_book(
    book_id: int,
    body: BookUpdate,
    session: AsyncSession = Depends(get_session),
) -> Book:
    book = await _get_or_404(session, book_id)
    # exclude_unset (not exclude_none) so an explicit null clears a field,
    # e.g. {"canonical_book_id": null} un-merges a book.
    data = body.model_dump(exclude_unset=True)
    # Explicitly choosing a primary author pins it against scan reassignment
    # (billing-order rule).  Send {"primary_author_manual": false} to unpin.
    if data.get("primary_author_id") is not None and "primary_author_manual" not in data:
        data["primary_author_manual"] = True
    for field, value in data.items():
        if field == "language":
            value = normalize_language_code(value)
        elif field == "title":
            book.title_sort = sort_title(value)
        setattr(book, field, value)
    await session.commit()
    await session.refresh(book)
    return _normalise_book_language(book)


@router.delete(
    "/{book_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a book",
)
async def delete_book(
    book_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    book = await _get_or_404(session, book_id)
    book.deleted = True
    await session.commit()


@router.post("/{book_id}/search", summary="Search indexers for a specific book")
async def search_for_book(
    book_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """
    Auto-constructs an indexer search query from the book's title and primary
    author name, then queries all configured Prowlarr / Jackett indexers.
    Returns the same result shape as ``POST /api/v1/search/``.
    """
    book = await _get_or_404(session, book_id)

    author_result = await session.execute(
        select(Author.name)
        .where(Author.id == book.primary_author_id)
    )
    author_name = author_result.scalar_one_or_none() or ""

    query = f"{book.title} {author_name}".strip() if author_name else book.title

    config = get_config()
    prowlarr = getattr(config, "prowlarr", None)
    jackett = getattr(config, "jackett", None)

    async with httpx.AsyncClient() as client:
        results = await unified_search(
            client,
            query,
            prowlarr_url=getattr(prowlarr, "url", "") if prowlarr else "",
            prowlarr_key=getattr(prowlarr, "api_key", "") if prowlarr else "",
            jackett_url=getattr(jackett, "url", "") if jackett else "",
            jackett_key=getattr(jackett, "api_key", "") if jackett else "",
        )

    for r in results:
        r["size_human"] = humanize_size(r.get("size", 0))

    return results


# ---------------------------------------------------------------------------
# Rescan a single book's author
# ---------------------------------------------------------------------------

@router.post("/{book_id}/rescan", summary="Re-queue a metadata scan for this book's author")
async def rescan_book(
    book_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Enqueue a full author scan so metadata for this book is refreshed."""
    book = await _get_or_404(session, book_id)

    if book.primary_author_id is not None:
        author_result = await session.execute(
            select(Author).where(Author.id == book.primary_author_id)
        )
    else:
        author_result = await session.execute(
            select(Author)
            .join(BookAuthor, and_(
                BookAuthor.author_id == Author.id,
                BookAuthor.book_id == book_id,
                BookAuthor.role == "author",
            ))
            .order_by(BookAuthor.author_order.asc().nullslast(), Author.name_sort)
            .limit(1)
        )
    author = author_result.scalar_one_or_none()
    if not author:
        raise HTTPException(status_code=404, detail="No primary author found for this book")

    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Job queue unavailable")

    job_id = author_scan_job_id(author.id)
    job = await enqueue_unique(pool, "scan_author_task", author.id, job_id=job_id)
    return {
        "job_id": job.job_id if job else job_id,
        "author_id": author.id,
        "author_name": author.name,
        "book_id": book_id,
        "book_title": book.title,
        "status": "queued" if job else "already_queued",
    }


# ---------------------------------------------------------------------------
# Import (post-download organisation)
# ---------------------------------------------------------------------------

class ImportRequest(BaseModel):
    source_path: str


@router.post("/{book_id}/import", summary="Import a downloaded file into the library")
async def import_book(
    book_id: int,
    body: ImportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """
    Enqueues an ``import_download_task`` that extracts archives and moves
    audio files into ``<library_root>/<Author>/<Series>/<Title>/``.

    Requires ``postprocess.mode: bookscout`` and a non-empty
    ``postprocess.library_root`` in config.yaml.
    """
    config = get_config()
    pp = getattr(config, "postprocess", None)
    mode = getattr(pp, "mode", "client") if pp else "client"
    if mode != "bookscout":
        raise HTTPException(
            status_code=400,
            detail="Post-processing is set to 'client' mode. Set postprocess.mode: bookscout to use this endpoint.",
        )

    library_root = getattr(pp, "library_root", "") if pp else ""
    if not library_root:
        raise HTTPException(
            status_code=400,
            detail="postprocess.library_root is not configured.",
        )

    source = Path(body.source_path)
    if not source.is_absolute() or ".." in source.parts:
        raise HTTPException(
            status_code=400,
            detail="source_path must be an absolute path without traversal components.",
        )

    book = await _get_or_404(session, book_id)

    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Job queue unavailable")

    job = await pool.enqueue_job("import_download_task", book_id, body.source_path)
    return {
        "job_id": job.job_id,
        "book_id": book_id,
        "book_title": book.title,
        "source_path": body.source_path,
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(session: AsyncSession, book_id: int) -> Book:
    result = await session.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book

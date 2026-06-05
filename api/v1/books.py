"""Books CRUD."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_config
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
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BookWithAuthorOut(BookOut):
    author_id: int
    author_name: str


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
    """Return recently created catalog rows, newest first."""
    q = (
        select(Book, Author.id, Author.name)
        .join(BookAuthor, and_(BookAuthor.book_id == Book.id, BookAuthor.role == "author"))
        .join(Author, Author.id == BookAuthor.author_id)
        .where(Book.deleted.is_(False))
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
    """Return books with release dates today or later, ordered by release date."""
    today = datetime.utcnow().date().isoformat()
    q = (
        select(Book, Author.id, Author.name)
        .join(BookAuthor, and_(BookAuthor.book_id == Book.id, BookAuthor.role == "author"))
        .join(Author, Author.id == BookAuthor.author_id)
        .where(
            Book.deleted.is_(False),
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
    today = datetime.utcnow().date().isoformat()

    async def count_where(*conditions) -> int:
        result = await session.execute(select(func.count(Book.id)).where(Book.deleted.is_(False), *conditions))
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
    for field, value in body.model_dump(exclude_none=True).items():
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
        .join(BookAuthor, Author.id == BookAuthor.author_id)
        .where(BookAuthor.book_id == book_id, BookAuthor.role == "author")
        .limit(1)
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

    author_result = await session.execute(
        select(Author)
        .join(BookAuthor, and_(
            BookAuthor.author_id == Author.id,
            BookAuthor.book_id == book_id,
            BookAuthor.role == "author",
        ))
    )
    author = author_result.scalar_one_or_none()
    if not author:
        raise HTTPException(status_code=404, detail="No primary author found for this book")

    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Job queue unavailable")

    job = await pool.enqueue_job("scan_author_task", author.id)
    return {
        "job_id": job.job_id,
        "author_id": author.id,
        "author_name": author.name,
        "book_id": book_id,
        "book_title": book.title,
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

from fastapi.responses import Response as FastAPIResponse  # noqa: E402 (local import avoids circular)
import json as _json


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
    for book, author_id, author_name in result.all():
        bd = BookOut.model_validate(book).model_dump(mode="json")
        bd["author_id"] = author_id
        bd["author_name"] = author_name
        books.append(bd)

    payload = _json.dumps(
        {"exported_at": datetime.utcnow().isoformat() + "Z", "total": len(books), "books": books},
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
        .join(BookAuthor, and_(BookAuthor.book_id == Book.id, BookAuthor.role == "author"))
        .join(Author, Author.id == BookAuthor.author_id)
        .where(Book.deleted.is_(False))
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

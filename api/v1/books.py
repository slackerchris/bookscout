"""Books CRUD."""
from __future__ import annotations

from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_config
from core.search import unified_search
from db.models import Author, Book, BookAuthor
from db.session import get_session

router = APIRouter(prefix="/books", tags=["books"])


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
    score: int
    confidence_band: str
    have_it: bool
    deleted: bool
    match_method: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BookUpdate(BaseModel):
    have_it: bool | None = None
    series_name: str | None = None
    series_position: str | None = None
    subtitle: str | None = None
    deleted: bool | None = None
    asin: str | None = None
    isbn: str | None = None
    isbn13: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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

    q = q.order_by(Book.title_sort)
    result = await session.execute(q)
    return list(result.scalars().all())


@router.get("/{book_id}", response_model=BookOut, summary="Get a single book")
async def get_book(
    book_id: int,
    session: AsyncSession = Depends(get_session),
) -> Book:
    return await _get_or_404(session, book_id)


@router.patch("/{book_id}", response_model=BookOut, summary="Update book fields")
async def update_book(
    book_id: int,
    body: BookUpdate,
    session: AsyncSession = Depends(get_session),
) -> Book:
    book = await _get_or_404(session, book_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(book, field, value)
    await session.commit()
    await session.refresh(book)
    return book


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
        size = r.get("size", 0) or 0
        if size >= 1_073_741_824:
            r["size_human"] = f"{size / 1_073_741_824:.2f} GB"
        elif size >= 1_048_576:
            r["size_human"] = f"{size / 1_048_576:.2f} MB"
        else:
            r["size_human"] = f"{size / 1024:.1f} KB"

    return results


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

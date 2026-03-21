"""Books CRUD."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Book, BookAuthor
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(session: AsyncSession, book_id: int) -> Book:
    result = await session.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book

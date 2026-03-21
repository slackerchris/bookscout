"""Authors CRUD.

Watching an author means creating an ``Author`` row and attaching a ``Watchlist``
entry.  The two are always created together and deleted (soft-deleted) together.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Author, Book, BookAuthor, Watchlist
from db.session import get_session

router = APIRouter(prefix="/authors", tags=["authors"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AuthorCreate(BaseModel):
    name: str


class AuthorUpdate(BaseModel):
    name: str | None = None
    active: bool | None = None


class WatchlistSettings(BaseModel):
    scan_enabled: bool | None = None


class AuthorOut(BaseModel):
    id: int
    name: str
    name_sort: str
    asin: str | None = None
    openlibrary_key: str | None = None
    active: bool
    last_scanned: datetime | None = None

    class Config:
        from_attributes = True


class AuthorDetailOut(AuthorOut):
    book_count: int = 0
    owned_count: int = 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[AuthorOut], summary="List watched authors")
async def list_authors(
    active_only: bool = True,
    session: AsyncSession = Depends(get_session),
) -> list[Author]:
    q = select(Author)
    if active_only:
        q = q.where(Author.active.is_(True))
    q = q.order_by(Author.name_sort)
    result = await session.execute(q)
    return list(result.scalars().all())


@router.post(
    "/",
    response_model=AuthorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add author to watchlist",
)
async def create_author(
    body: AuthorCreate,
    session: AsyncSession = Depends(get_session),
) -> Author:
    # Duplicate check (case-sensitive — normalise if needed)
    existing = await session.execute(select(Author).where(Author.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Author already exists")

    author = Author(name=body.name, name_sort=_sort_name(body.name))
    session.add(author)
    await session.flush()  # populate author.id

    session.add(Watchlist(author_id=author.id))
    await session.commit()
    await session.refresh(author)
    return author


@router.get("/{author_id}", response_model=AuthorDetailOut, summary="Get author + stats")
async def get_author(
    author_id: int,
    session: AsyncSession = Depends(get_session),
) -> AuthorDetailOut:
    author = await _get_or_404(session, author_id)

    book_q = await session.execute(
        select(Book)
        .join(BookAuthor, Book.id == BookAuthor.book_id)
        .where(
            BookAuthor.author_id == author_id,
            BookAuthor.role == "author",
            Book.deleted.is_(False),
        )
    )
    books = list(book_q.scalars().all())

    # Attach watchlist last_scanned
    wl_q = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    wl = wl_q.scalar_one_or_none()

    out = AuthorDetailOut(
        id=author.id,
        name=author.name,
        name_sort=author.name_sort,
        asin=author.asin,
        openlibrary_key=author.openlibrary_key,
        active=author.active,
        last_scanned=wl.last_scanned if wl else None,
        book_count=len(books),
        owned_count=sum(1 for b in books if b.have_it),
    )
    return out


@router.patch("/{author_id}", response_model=AuthorOut, summary="Update author")
async def update_author(
    author_id: int,
    body: AuthorUpdate,
    session: AsyncSession = Depends(get_session),
) -> Author:
    author = await _get_or_404(session, author_id)
    if body.name is not None:
        author.name = body.name
        author.name_sort = _sort_name(body.name)
    if body.active is not None:
        author.active = body.active
    await session.commit()
    await session.refresh(author)
    return author


@router.delete(
    "/{author_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove author from watchlist (soft-delete)",
)
async def delete_author(
    author_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    author = await _get_or_404(session, author_id)
    author.active = False
    # Disable watchlist scanning too
    wl_q = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    wl = wl_q.scalar_one_or_none()
    if wl:
        wl.scan_enabled = False
    await session.commit()


@router.patch(
    "/{author_id}/watchlist",
    response_model=dict,
    summary="Toggle scan_enabled on watchlist entry",
)
async def update_watchlist(
    author_id: int,
    body: WatchlistSettings,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(session, author_id)  # validate author exists
    wl_q = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    wl = wl_q.scalar_one_or_none()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    if body.scan_enabled is not None:
        wl.scan_enabled = body.scan_enabled
    await session.commit()
    return {"author_id": author_id, "scan_enabled": wl.scan_enabled}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(session: AsyncSession, author_id: int) -> Author:
    result = await session.execute(select(Author).where(Author.id == author_id))
    author = result.scalar_one_or_none()
    if not author:
        raise HTTPException(status_code=404, detail="Author not found")
    return author


def _sort_name(name: str) -> str:
    parts = name.strip().rsplit(" ", 1)
    return f"{parts[1]}, {parts[0]}" if len(parts) == 2 else name

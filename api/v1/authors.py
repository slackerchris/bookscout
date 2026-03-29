"""Authors CRUD.

Watching an author means creating an ``Author`` row and attaching a ``Watchlist``
entry.  The two are always created together and deleted (soft-deleted) together.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.normalize import normalize_author_key, sort_name
from db.models import Author, AuthorAlias, Book, BookAuthor, Watchlist
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


class CoAuthorOut(BaseModel):
    id: int
    name: str
    on_watchlist: bool
    book_count: int


class AliasOut(BaseModel):
    id: int
    alias: str
    source: str
    created_at: datetime

    class Config:
        from_attributes = True


class AliasCreate(BaseModel):
    alias: str
    source: str = "manual"


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


class LanguageCount(BaseModel):
    language: str | None  # None means the book record pre-dates the language column
    count: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "/{author_id}/languages",
    response_model=list[LanguageCount],
    summary="Language breakdown for an author's catalog",
)
async def list_author_languages(
    author_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[LanguageCount]:
    """Return a per-language count of books in this author's catalog, ordered
    by count descending.  Useful for choosing an appropriate ``language_filter``
    before triggering a scan.  The ``language`` field is ``null`` for book rows
    that pre-date the v0.48.0 ``books.language`` column."""
    await _get_or_404(session, author_id)

    rows = await session.execute(
        select(
            Book.language,
            func.count(Book.id).label("count"),
        )
        .join(BookAuthor, Book.id == BookAuthor.book_id)
        .where(
            BookAuthor.author_id == author_id,
            BookAuthor.role == "author",
            Book.deleted.is_(False),
        )
        .group_by(Book.language)
        .order_by(func.count(Book.id).desc())
    )
    return [LanguageCount(language=r.language, count=r.count) for r in rows.all()]


@router.get(
    "/{author_id}/coauthors",
    response_model=list[CoAuthorOut],
    summary="List co-authors for an author",
)
async def list_coauthors(
    author_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[CoAuthorOut]:
    """Return authors who share at least one book with *author_id* as a
    co-author, ordered by shared-book count descending."""
    await _get_or_404(session, author_id)

    # Books where the requested author is the primary author
    authored_sq = (
        select(BookAuthor.book_id)
        .where(
            and_(
                BookAuthor.author_id == author_id,
                BookAuthor.role == "author",
            )
        )
        .scalar_subquery()
    )

    co_q = await session.execute(
        select(
            Author.id,
            Author.name,
            func.count(BookAuthor.book_id).label("book_count"),
        )
        .select_from(BookAuthor)
        .join(Author, Author.id == BookAuthor.author_id)
        .where(
            and_(
                BookAuthor.book_id.in_(authored_sq),
                BookAuthor.role == "co-author",
            )
        )
        .group_by(Author.id, Author.name)
        .order_by(func.count(BookAuthor.book_id).desc())
    )
    rows = co_q.all()

    if not rows:
        return []

    co_ids = [r.id for r in rows]
    wl_q = await session.execute(
        select(Watchlist.author_id).where(Watchlist.author_id.in_(co_ids))
    )
    watchlisted: set[int] = {r[0] for r in wl_q.fetchall()}

    return [
        CoAuthorOut(
            id=r.id,
            name=r.name,
            on_watchlist=r.id in watchlisted,
            book_count=r.book_count,
        )
        for r in rows
    ]


@router.get("/favorites", response_model=dict, summary="List favourite author IDs")
async def list_favorites(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return ``{"author_ids": [...]}`` — the set of author IDs marked as
    favourites.  Stored server-side so the list survives browser clears."""
    result = await session.execute(
        select(Watchlist.author_id).where(Watchlist.favorite.is_(True))
    )
    return {"author_ids": [r[0] for r in result.fetchall()]}


@router.post(
    "/{author_id}/favorite",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark author as favourite",
)
async def add_favorite(
    author_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    await _get_or_404(session, author_id)
    wl_q = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    wl = wl_q.scalar_one_or_none()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    wl.favorite = True
    await session.commit()


@router.delete(
    "/{author_id}/favorite",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unmark author as favourite",
)
async def remove_favorite(
    author_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    await _get_or_404(session, author_id)
    wl_q = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    wl = wl_q.scalar_one_or_none()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    wl.favorite = False
    await session.commit()


@router.get("/count", response_model=dict, summary="Count watched authors")
async def count_authors(
    active_only: bool = True,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return ``{"count": N}`` — cheap alternative to fetching the full author list
    just to read its length."""
    q = (
        select(func.count(Author.id))
        .join(Watchlist, Watchlist.author_id == Author.id)
    )
    if active_only:
        q = q.where(Author.active.is_(True))
    result = await session.execute(q)
    return {"count": result.scalar_one()}


@router.get("/", response_model=list[AuthorDetailOut], summary="List watched authors")
async def list_authors(
    active_only: bool = True,
    search: str | None = Query(None, description="Filter by name (case-insensitive contains)"),
    session: AsyncSession = Depends(get_session),
) -> list[AuthorDetailOut]:
    # Subquery: total catalog books per author
    bc_sq = (
        select(BookAuthor.author_id, func.count(Book.id).label("bc"))
        .join(Book, Book.id == BookAuthor.book_id)
        .where(BookAuthor.role == "author", Book.deleted.is_(False))
        .group_by(BookAuthor.author_id)
        .subquery()
    )
    # Subquery: owned books per author
    oc_sq = (
        select(BookAuthor.author_id, func.count(Book.id).label("oc"))
        .join(Book, Book.id == BookAuthor.book_id)
        .where(BookAuthor.role == "author", Book.deleted.is_(False), Book.have_it.is_(True))
        .group_by(BookAuthor.author_id)
        .subquery()
    )

    q = (
        select(
            Author,
            Watchlist.last_scanned,
            func.coalesce(bc_sq.c.bc, 0).label("book_count"),
            func.coalesce(oc_sq.c.oc, 0).label("owned_count"),
        )
        .outerjoin(Watchlist, Watchlist.author_id == Author.id)
        .outerjoin(bc_sq, bc_sq.c.author_id == Author.id)
        .outerjoin(oc_sq, oc_sq.c.author_id == Author.id)
    )
    if active_only:
        q = q.where(Author.active.is_(True))
    if search:
        q = q.where(Author.name.ilike(f"%{search}%"))
    q = q.order_by(Author.name_sort)

    result = await session.execute(q)
    rows = result.all()
    return [
        AuthorDetailOut(
            id=row[0].id,
            name=row[0].name,
            name_sort=row[0].name_sort,
            asin=row[0].asin,
            openlibrary_key=row[0].openlibrary_key,
            active=row[0].active,
            last_scanned=row[1],
            book_count=row[2],
            owned_count=row[3],
        )
        for row in rows
    ]


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

    author = Author(name=body.name, name_sort=sort_name(body.name), name_normalized=normalize_author_key(body.name))
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

    # Use COUNT queries instead of loading all book rows into Python.
    base_count = (
        select(func.count(Book.id))
        .join(BookAuthor, and_(
            BookAuthor.book_id == Book.id,
            BookAuthor.author_id == author_id,
            BookAuthor.role == "author",
        ))
        .where(Book.deleted.is_(False))
    )
    book_count = (await session.execute(base_count)).scalar_one()
    owned_count = (await session.execute(base_count.where(Book.have_it.is_(True)))).scalar_one()

    wl_q = await session.execute(
        select(Watchlist).where(Watchlist.author_id == author_id)
    )
    wl = wl_q.scalar_one_or_none()

    return AuthorDetailOut(
        id=author.id,
        name=author.name,
        name_sort=author.name_sort,
        asin=author.asin,
        openlibrary_key=author.openlibrary_key,
        active=author.active,
        last_scanned=wl.last_scanned if wl else None,
        book_count=book_count,
        owned_count=owned_count,
    )


@router.patch("/{author_id}", response_model=AuthorOut, summary="Update author")
async def update_author(
    author_id: int,
    body: AuthorUpdate,
    session: AsyncSession = Depends(get_session),
) -> Author:
    author = await _get_or_404(session, author_id)
    if body.name is not None:
        author.name = body.name
        author.name_sort = sort_name(body.name)
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


# ---------------------------------------------------------------------------
# Alias routes
# ---------------------------------------------------------------------------

@router.get(
    "/{author_id}/aliases",
    response_model=list[AliasOut],
    summary="List all known name aliases for an author",
)
async def list_aliases(
    author_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[AuthorAlias]:
    await _get_or_404(session, author_id)
    result = await session.execute(
        select(AuthorAlias)
        .where(AuthorAlias.author_id == author_id)
        .order_by(AuthorAlias.created_at)
    )
    return list(result.scalars().all())


@router.post(
    "/{author_id}/aliases",
    response_model=AliasOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a name alias for an author",
)
async def create_alias(
    author_id: int,
    body: AliasCreate,
    session: AsyncSession = Depends(get_session),
) -> AuthorAlias:
    await _get_or_404(session, author_id)
    existing = await session.execute(
        select(AuthorAlias).where(
            AuthorAlias.author_id == author_id,
            AuthorAlias.alias == body.alias,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Alias already exists for this author")
    alias = AuthorAlias(author_id=author_id, alias=body.alias, source=body.source)
    session.add(alias)
    await session.commit()
    await session.refresh(alias)
    return alias


@router.delete(
    "/{author_id}/aliases/{alias_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a name alias",
)
async def delete_alias(
    author_id: int,
    alias_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    await _get_or_404(session, author_id)
    result = await session.execute(
        select(AuthorAlias).where(
            AuthorAlias.id == alias_id,
            AuthorAlias.author_id == author_id,
        )
    )
    alias = result.scalar_one_or_none()
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    await session.delete(alias)
    await session.commit()




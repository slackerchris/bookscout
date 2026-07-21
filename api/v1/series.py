"""Series-centric view of the catalog.

Collectors think in series ("own Cradle 1–9, missing 10–12"), so this
endpoint regroups the flat book catalog by (series, primary author) and
reports ownership per position, including numeric holes where the catalog
has no entry at all (own 1, 2 and 4 → position 3 is an *unknown gap*).
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Author, Book
from db.session import get_session

router = APIRouter(prefix="/series", tags=["series"])

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


class SeriesBookOut(BaseModel):
    id: int
    title: str
    series_position: str | None = None
    position: float | None = None
    have_it: bool
    release_date: str | None = None
    confidence_band: str
    cover_url: str | None = None


class SeriesOut(BaseModel):
    series_name: str
    author_id: int | None = None
    author_name: str | None = None
    total: int
    owned: int
    books: list[SeriesBookOut]
    unknown_gaps: list[int] = []


def parse_position(raw: Any) -> float | None:
    """Extract the numeric position from free-text series_position ("1", "1.5", "Book 3")."""
    if raw is None:
        return None
    m = _NUM_RE.search(str(raw))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def group_series(
    rows: list[tuple[Book, int | None, str | None]],
) -> list[dict[str, Any]]:
    """Group (book, author_id, author_name) rows into series summaries.

    Series identity is (case-folded series name, author) so two authors'
    same-named series don't merge.  Within a series, books sort by numeric
    position (unknown positions last, then by title).
    """
    groups: dict[tuple[str, int | None], dict[str, Any]] = {}
    for book, author_id, author_name in rows:
        name = (book.series_name or "").strip()
        if not name:
            continue
        key = (name.casefold(), author_id)
        g = groups.setdefault(key, {
            "series_name": name,
            "author_id": author_id,
            "author_name": author_name,
            "books": [],
        })
        g["books"].append(book)

    out: list[dict[str, Any]] = []
    for g in groups.values():
        books: list[Book] = g["books"]
        entries = [
            {
                "id": b.id,
                "title": b.title,
                "series_position": b.series_position,
                "position": parse_position(b.series_position),
                "have_it": b.have_it,
                "release_date": b.release_date,
                "confidence_band": b.confidence_band,
                "cover_url": b.cover_url,
            }
            for b in books
        ]
        entries.sort(key=lambda e: (e["position"] is None, e["position"] or 0, e["title"]))

        # Whole-number positions present in the catalog; integers missing from
        # the 1..max range are gaps the metadata sources haven't surfaced.
        present = {int(e["position"]) for e in entries
                   if e["position"] is not None and float(e["position"]).is_integer()}
        unknown_gaps: list[int] = []
        if present:
            unknown_gaps = [n for n in range(1, max(present) + 1) if n not in present]

        out.append({
            "series_name": g["series_name"],
            "author_id": g["author_id"],
            "author_name": g["author_name"],
            "total": len(entries),
            "owned": sum(1 for e in entries if e["have_it"]),
            "books": entries,
            "unknown_gaps": unknown_gaps,
        })

    out.sort(key=lambda s: ((s["author_name"] or "").casefold(), s["series_name"].casefold()))
    return out


@router.get("/", response_model=list[SeriesOut], summary="Series with ownership and gaps")
async def list_series(
    missing_only: bool = Query(False, description="Only series with unowned books"),
    author_id: int | None = Query(None),
    min_books: int = Query(2, ge=1, description="Hide series with fewer catalog entries"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    q = (
        select(Book, Author.id, Author.name)
        .outerjoin(Author, Author.id == Book.primary_author_id)
        .where(
            Book.deleted.is_(False),
            Book.canonical_book_id.is_(None),
            Book.series_name.is_not(None),
            Book.series_name != "",
        )
    )
    if author_id is not None:
        q = q.where(Book.primary_author_id == author_id)

    rows = (await session.execute(q)).all()
    series = group_series([(b, aid, aname) for b, aid, aname in rows])

    series = [s for s in series if s["total"] >= min_books]
    if missing_only:
        series = [s for s in series if s["owned"] < s["total"]]
    return series

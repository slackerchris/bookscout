"""Download attempt history — records every send-to-client action."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DownloadAttempt
from db.session import get_session

router = APIRouter(prefix="/download-history", tags=["download-history"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DownloadAttemptOut(BaseModel):
    id: int
    book_id: int | None = None
    book_title: str | None = None
    query: str | None = None
    release_title: str
    indexer: str | None = None
    source: str | None = None
    type: str | None = None
    size_bytes: int | None = None
    seeders: int | None = None
    download_url: str | None = None
    status: str
    error_detail: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class DownloadAttemptCreate(BaseModel):
    book_id: int | None = None
    book_title: str | None = None
    query: str | None = None
    release_title: str
    indexer: str | None = None
    source: str | None = None
    type: str | None = None
    size_bytes: int | None = None
    seeders: int | None = None
    download_url: str | None = None
    status: str = "queued"
    error_detail: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[DownloadAttemptOut], summary="List recent download attempts")
async def list_history(
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[DownloadAttempt]:
    result = await session.execute(
        select(DownloadAttempt)
        .order_by(DownloadAttempt.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post("/", response_model=DownloadAttemptOut, summary="Record a download attempt")
async def create_attempt(
    body: DownloadAttemptCreate,
    session: AsyncSession = Depends(get_session),
) -> DownloadAttempt:
    attempt = DownloadAttempt(**body.model_dump())
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    return attempt


@router.delete("/", summary="Clear all download history")
async def clear_history(session: AsyncSession = Depends(get_session)) -> dict:
    result = await session.execute(select(DownloadAttempt))
    rows = result.scalars().all()
    count = len(rows)
    for row in rows:
        await session.delete(row)
    await session.commit()
    return {"deleted": count}

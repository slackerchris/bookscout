"""Download attempt history — records every send-to-client action."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select
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
    status: str | None = Query(None, description="Filter: queued | pending | failed | dismissed"),
    session: AsyncSession = Depends(get_session),
) -> list[DownloadAttempt]:
    q = select(DownloadAttempt).order_by(DownloadAttempt.created_at.desc()).limit(limit)
    if status:
        q = q.where(DownloadAttempt.status == status)
    result = await session.execute(q)
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


class RequestDownloadsBody(BaseModel):
    book_ids: list[int]


@router.post("/request", summary="Batch-search books and queue best matches for approval")
async def request_downloads(
    body: RequestDownloadsBody,
    request: Request,
) -> dict:
    """Enqueue a worker job that searches the indexers for each book and
    records the best match as a *pending* download attempt.

    Used by the Series page "search all missing" — results appear under
    Downloads → Pending approval (and emit ``autodownload.pending`` events).
    Unreleased books and books with an existing queued/pending attempt are
    skipped.
    """
    if not body.book_ids:
        raise HTTPException(status_code=422, detail="book_ids must not be empty")
    if len(body.book_ids) > 100:
        raise HTTPException(status_code=422, detail="Too many books in one request (max 100)")

    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Job queue unavailable")

    job = await pool.enqueue_job("request_downloads_task", body.book_ids)
    return {"job_id": job.job_id, "requested": len(body.book_ids), "status": "queued"}


@router.post(
    "/{attempt_id}/approve",
    response_model=DownloadAttemptOut,
    summary="Approve a pending auto-download and send it to the client",
)
async def approve_attempt(
    attempt_id: int,
    session: AsyncSession = Depends(get_session),
) -> DownloadAttempt:
    import httpx
    from config import get_config
    from core.search import send_release

    attempt = await session.get(DownloadAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Download attempt not found")
    if attempt.status != "pending":
        raise HTTPException(status_code=409, detail=f"Attempt is '{attempt.status}', not pending")
    if not attempt.download_url:
        raise HTTPException(status_code=422, detail="Attempt has no download URL")

    async with httpx.AsyncClient() as client:
        result = await send_release(
            client,
            get_config(),
            url=attempt.download_url,
            title=attempt.release_title,
            release_type=attempt.type or "torrent",
            book_id=attempt.book_id or 0,
        )

    success = bool(result.get("success"))
    attempt.status = "queued" if success else "failed"
    attempt.error_detail = None if success else result.get("detail")
    await session.commit()
    await session.refresh(attempt)
    if not success:
        raise HTTPException(status_code=502, detail=result.get("detail", "Download client returned an error"))
    return attempt


@router.post(
    "/{attempt_id}/dismiss",
    response_model=DownloadAttemptOut,
    summary="Dismiss a pending auto-download without sending it",
)
async def dismiss_attempt(
    attempt_id: int,
    session: AsyncSession = Depends(get_session),
) -> DownloadAttempt:
    attempt = await session.get(DownloadAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Download attempt not found")
    if attempt.status != "pending":
        raise HTTPException(status_code=409, detail=f"Attempt is '{attempt.status}', not pending")
    attempt.status = "dismissed"
    await session.commit()
    await session.refresh(attempt)
    return attempt


@router.delete("/", summary="Clear all download history")
async def clear_history(session: AsyncSession = Depends(get_session)) -> dict:
    count = (
        await session.execute(select(func.count(DownloadAttempt.id)))
    ).scalar_one()
    await session.execute(delete(DownloadAttempt))
    await session.commit()
    return {"deleted": count}

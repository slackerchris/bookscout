"""Scan trigger endpoints.

These enqueue arq jobs rather than running scans synchronously, so they
return immediately with a job ID that clients can poll.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Author, Book, Watchlist
from db.session import get_session

router = APIRouter(prefix="/scans", tags=["scans"])


def _arq_pool(request: Request):
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Job queue unavailable")
    return pool


@router.post("/author/{author_id}", summary="Scan a single author")
async def scan_author(
    author_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    arq = _arq_pool(request)

    result = await session.execute(select(Author).where(Author.id == author_id))
    author = result.scalar_one_or_none()
    if not author:
        raise HTTPException(status_code=404, detail="Author not found")

    job = await arq.enqueue_job("scan_author_task", author_id)
    return {
        "job_id": job.job_id,
        "author_id": author_id,
        "author_name": author.name,
        "status": "queued",
    }


@router.post("/all", summary="Scan all active watchlist entries")
async def scan_all(request: Request):
    arq = _arq_pool(request)
    job = await arq.enqueue_job("scan_all_authors_task")
    return {"job_id": job.job_id, "status": "queued"}


@router.get("/job/{job_id}", summary="Check job status")
async def job_status(job_id: str, request: Request):
    from arq.jobs import Job, JobStatus

    arq = _arq_pool(request)
    job = Job(job_id, arq)
    job_status_value = await job.status()

    response: dict = {"job_id": job_id, "status": job_status_value.value}

    if job_status_value == JobStatus.complete:
        try:
            info = await job.result_info()
            response["result"] = info.result if info else None
        except Exception:
            pass

    return response


@router.get("/stats", summary="Scan statistics for the dashboard")
async def scan_stats(session: AsyncSession = Depends(get_session)) -> dict:
    """
    Returns a snapshot of scan activity useful for dashboard stat cards:

    - **last_scan_time**: when any author was most recently scanned
    - **new_books_today**: books discovered since UTC midnight today
    - **total_books**: all non-deleted books
    - **total_missing**: non-deleted books not yet owned
    - **authors_scanned_today**: watchlist entries scanned since UTC midnight
    """
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    last_scan_time_row = await session.execute(
        select(func.max(Watchlist.last_scanned))
    )
    last_scan_time = last_scan_time_row.scalar_one_or_none()

    new_books_today_row = await session.execute(
        select(func.count(Book.id)).where(
            Book.deleted.is_(False),
            Book.created_at >= today,
        )
    )
    new_books_today = new_books_today_row.scalar_one()

    total_books_row = await session.execute(
        select(func.count(Book.id)).where(Book.deleted.is_(False))
    )
    total_books = total_books_row.scalar_one()

    total_missing_row = await session.execute(
        select(func.count(Book.id)).where(
            Book.deleted.is_(False),
            Book.have_it.is_(False),
        )
    )
    total_missing = total_missing_row.scalar_one()

    authors_scanned_today_row = await session.execute(
        select(func.count(Watchlist.id)).where(Watchlist.last_scanned >= today)
    )
    authors_scanned_today = authors_scanned_today_row.scalar_one()

    return {
        "last_scan_time": last_scan_time,
        "new_books_today": new_books_today,
        "total_books": total_books,
        "total_missing": total_missing,
        "authors_scanned_today": authors_scanned_today,
    }

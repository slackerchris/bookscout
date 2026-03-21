"""Scan trigger endpoints.

These enqueue arq jobs rather than running scans synchronously, so they
return immediately with a job ID that clients can poll.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Author, Watchlist
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

"""Library path management endpoints (v0.37.0).

Provides CRUD for the filesystem paths BookScout scans for owned audiobooks,
plus a trigger endpoint to enqueue a scan for a specific path.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import LibraryPath
from db.session import get_session

router = APIRouter(prefix="/library-paths", tags=["library-paths"])


class LibraryPathCreate(BaseModel):
    path: str
    name: str | None = None


@router.get("", summary="List all configured library paths")
async def list_library_paths(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(LibraryPath).order_by(LibraryPath.id))
    paths = result.scalars().all()
    return [
        {
            "id": lp.id,
            "path": lp.path,
            "name": lp.name,
            "scan_enabled": lp.scan_enabled,
            "last_scanned": lp.last_scanned,
            "created_at": lp.created_at,
        }
        for lp in paths
    ]


@router.post("", summary="Register a new library path", status_code=201)
async def add_library_path(
    body: LibraryPathCreate,
    session: AsyncSession = Depends(get_session),
):
    p = Path(body.path)
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {body.path}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {body.path}")

    resolved = str(p.resolve())
    existing = await session.execute(
        select(LibraryPath).where(LibraryPath.path == resolved)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Path already registered")

    lp = LibraryPath(path=resolved, name=body.name or p.name)
    session.add(lp)
    await session.commit()
    await session.refresh(lp)
    return {
        "id": lp.id,
        "path": lp.path,
        "name": lp.name,
        "scan_enabled": lp.scan_enabled,
        "created_at": lp.created_at,
    }


@router.delete("/{path_id}", summary="Remove a library path", status_code=204)
async def remove_library_path(
    path_id: int,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(LibraryPath).where(LibraryPath.id == path_id))
    lp = result.scalar_one_or_none()
    if not lp:
        raise HTTPException(status_code=404, detail="Library path not found")
    await session.delete(lp)
    await session.commit()


@router.post("/{path_id}/scan", summary="Enqueue a filesystem scan for a library path")
async def enqueue_library_scan(
    path_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(LibraryPath).where(LibraryPath.id == path_id))
    lp = result.scalar_one_or_none()
    if not lp:
        raise HTTPException(status_code=404, detail="Library path not found")

    arq = getattr(request.app.state, "arq_pool", None)
    if arq is None:
        raise HTTPException(status_code=503, detail="Job queue unavailable")

    job = await arq.enqueue_job("scan_library_path_task", path_id)
    return {
        "job_id": job.job_id,
        "library_path_id": path_id,
        "path": lp.path,
        "status": "queued",
    }


@router.post("/scan-all", summary="Enqueue a filesystem scan for all enabled library paths")
async def enqueue_all_library_scans(request: Request):
    arq = getattr(request.app.state, "arq_pool", None)
    if arq is None:
        raise HTTPException(status_code=503, detail="Job queue unavailable")

    job = await arq.enqueue_job("scan_all_library_paths_task")
    return {"job_id": job.job_id, "status": "queued"}

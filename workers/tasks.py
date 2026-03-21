"""arq task functions.

Each function follows the arq convention: first arg is ``ctx`` (the worker
context dict), remaining args are the task payload.
"""
from __future__ import annotations

from typing import Any

from config import get_config
from db.session import AsyncSessionFactory
from core.scan import scan_author_by_id
from core.scanner import scan_library_path as _scan_library_path


async def scan_author_task(ctx: dict, author_id: int) -> dict[str, Any]:
    """arq task: run the full scan pipeline for a single watchlisted author."""
    config = get_config()
    redis_client = ctx.get("redis")
    async with AsyncSessionFactory() as session:
        return await scan_author_by_id(
            session, author_id, config=config, redis_client=redis_client
        )


async def scan_all_authors_task(ctx: dict) -> dict[str, Any]:
    """arq task: enqueue (or run) a scan for every active, enabled watchlist entry.

    Rather than doing all scans in a single task (which could time out), this
    task enqueues individual ``scan_author_task`` jobs via the arq pool that
    is stored in worker context.
    """
    from sqlalchemy import select
    from db.models import Author, Watchlist

    config = get_config()
    redis_client = ctx.get("redis")

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Watchlist)
            .join(Author, Watchlist.author_id == Author.id)
            .where(Author.active.is_(True), Watchlist.scan_enabled.is_(True))
        )
        entries = result.scalars().all()
        author_ids = [e.author_id for e in entries]

    scanned = 0
    errors: list[dict] = []

    for aid in author_ids:
        try:
            async with AsyncSessionFactory() as session:
                await scan_author_by_id(
                    session, aid, config=config, redis_client=redis_client
                )
            scanned += 1
        except Exception as exc:
            errors.append({"author_id": aid, "error": str(exc)})

    return {
        "total": len(author_ids),
        "scanned": scanned,
        "errors": errors,
    }


async def scan_library_path_task(ctx: dict, library_path_id: int) -> dict[str, Any]:
    """arq task: walk a single library path and match audio files to DB books."""
    async with AsyncSessionFactory() as session:
        return await _scan_library_path(session, library_path_id)


async def scan_all_library_paths_task(ctx: dict) -> dict[str, Any]:
    """arq task: scan every enabled library path sequentially."""
    from sqlalchemy import select
    from db.models import LibraryPath

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(LibraryPath).where(LibraryPath.scan_enabled.is_(True))
        )
        paths = result.scalars().all()
        path_ids = [lp.id for lp in paths]

    results: list[dict] = []
    errors: list[dict] = []

    for pid in path_ids:
        try:
            async with AsyncSessionFactory() as session:
                summary = await _scan_library_path(session, pid)
            results.append(summary)
        except Exception as exc:
            errors.append({"library_path_id": pid, "error": str(exc)})

    return {"paths_scanned": len(results), "results": results, "errors": errors}

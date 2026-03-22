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
    """arq task: enqueue an individual scan_author_task for every active watchlist entry.

    Dispatches one job per author so each runs within its own timeout budget
    rather than running all authors inline and hitting the single-job limit.
    """
    from arq import ArqRedis
    from sqlalchemy import select
    from db.models import Author, Watchlist

    redis_client = ctx.get("redis")

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Watchlist)
            .join(Author, Watchlist.author_id == Author.id)
            .where(Author.active.is_(True), Watchlist.scan_enabled.is_(True))
        )
        entries = result.scalars().all()
        author_ids = [e.author_id for e in entries]

    enqueued = 0
    if redis_client is not None:
        arq_redis = ArqRedis(redis_client)
        for aid in author_ids:
            await arq_redis.enqueue_job("scan_author_task", aid)
            enqueued += 1
    else:
        # Fallback: run inline if no redis context (e.g. CLI usage)
        config = get_config()
        for aid in author_ids:
            async with AsyncSessionFactory() as session:
                await scan_author_by_id(session, aid, config=config, redis_client=None)
            enqueued += 1

    return {"total": len(author_ids), "enqueued": enqueued}


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

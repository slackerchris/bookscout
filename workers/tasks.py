"""arq task functions.

Each function follows the arq convention: first arg is ``ctx`` (the worker
context dict), remaining args are the task payload.
"""
from __future__ import annotations

import logging
from typing import Any

from config import get_config
from db.session import AsyncSessionFactory
from core.scan import scan_author_by_id
from core.scanner import scan_library_path as _scan_library_path

logger = logging.getLogger(__name__)


async def scan_author_task(ctx: dict, author_id: int) -> dict[str, Any]:
    """arq task: run the full scan pipeline for a single watchlisted author."""
    from core.auto_download import run_auto_download_for_author

    config = get_config()
    redis_client = ctx.get("redis")
    async with AsyncSessionFactory() as session:
        result = await scan_author_by_id(
            session, author_id, config=config, redis_client=redis_client
        )

    # After the scan lands, grab/queue any newly eligible books for authors
    # with auto_download enabled (cheap no-op otherwise).
    try:
        async with AsyncSessionFactory() as session:
            auto = await run_auto_download_for_author(
                session, author_id, config, redis_client=redis_client
            )
        if auto.get("enabled"):
            result["auto_download"] = auto
    except Exception:
        logger.exception("Auto-download pass failed", extra={"author_id": author_id})

    return result


async def scan_all_authors_task(ctx: dict) -> dict[str, Any]:
    """arq task: enqueue an individual scan_author_task for every active watchlist entry.

    Dispatches one job per author so each runs within its own timeout budget
    rather than running all authors inline and hitting the single-job limit.
    """
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
        from arq.connections import create_pool
        from core.enqueue import author_scan_job_id, enqueue_unique
        from workers.settings import _redis_settings
        arq_redis = await create_pool(_redis_settings())
        try:
            for aid in author_ids:
                job = await enqueue_unique(
                    arq_redis, "scan_author_task", aid, job_id=author_scan_job_id(aid)
                )
                if job:
                    enqueued += 1
        finally:
            # Without this the pool leaks once per cron run for the life of
            # the worker process.
            await arq_redis.aclose()
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


async def poll_completed_downloads_task(ctx: dict) -> dict[str, Any]:
    """arq cron task: import completed qBittorrent downloads automatically.

    Native replacement for the external n8n poller workflow.  Finds completed
    torrents tagged ``bookscout-<book_id>`` (stamped when BookScout sent
    them), runs the normal import pipeline for each, and tags the torrent
    ``bs-imported`` / ``bs-failed`` so it is processed exactly once.
    """
    import httpx
    from core.qbittorrent import (
        TAG_FAILED,
        TAG_IMPORTED,
        fetch_completed_torrents,
        login,
        select_import_candidates,
        set_tags,
    )

    config = get_config()
    pp = getattr(config, "postprocess", None)
    mode = getattr(pp, "mode", "client") if pp else "client"
    library_root = getattr(pp, "library_root", "") if pp else ""
    auto_import = getattr(pp, "auto_import", True) if pp else True
    if mode != "bookscout" or not library_root or not auto_import:
        return {"skipped": "auto-import disabled (postprocess.mode/library_root/auto_import)"}

    dl = getattr(config, "download", None)
    torrent = getattr(dl, "torrent", None) if dl else None
    qbt_url = getattr(torrent, "url", "") if torrent else ""
    qbt_type = getattr(torrent, "type", "qbittorrent") if torrent else "qbittorrent"
    if not qbt_url or qbt_type != "qbittorrent":
        return {"skipped": "no qBittorrent client configured"}

    username = getattr(torrent, "username", "")
    password = getattr(torrent, "password", "")
    category = getattr(torrent, "default_category", "")

    imported: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        cookies = await login(client, qbt_url, username, password)
        if cookies is None:
            return {"error": "qBittorrent authentication failed"}

        torrents = await fetch_completed_torrents(client, qbt_url, cookies, category)
        candidates = select_import_candidates(torrents)

        for cand in candidates:
            result = await import_download_task(ctx, cand["book_id"], cand["content_path"])
            # files skipped-as-already-present still means the book is in the
            # library — mark imported so the torrent isn't retried forever.
            ok = bool(result.get("success")) and bool(
                result.get("files_copied") or result.get("skipped")
            )
            if ok:
                await set_tags(
                    client, qbt_url, cookies, cand["hash"],
                    add=TAG_IMPORTED, remove=TAG_FAILED,
                )
                imported.append({"book_id": cand["book_id"], "name": cand["name"]})
            else:
                await set_tags(client, qbt_url, cookies, cand["hash"], add=TAG_FAILED)
                failed.append({
                    "book_id": cand["book_id"],
                    "name": cand["name"],
                    "detail": result.get("detail") or (result.get("errors") or [None])[0],
                })

    if imported or failed:
        logger.info(
            "Auto-import poll complete",
            extra={"imported": len(imported), "failed": len(failed)},
        )

    return {"candidates": len(imported) + len(failed), "imported": imported, "failed": failed}


async def import_download_task(
    ctx: dict,
    book_id: int,
    source_path: str,
) -> dict[str, Any]:
    """arq task: extract and move a downloaded file into the library.

    Reads ``postprocess.library_root`` from config, then calls
    ``core.importer.import_download()`` to extract archives and arrange
    audio files into ``<library_root>/<Author>/<Series>/<Title>/``.
    On success the book record is updated: ``have_it=True``,
    ``match_method='imported'``.
    """
    import asyncio
    from sqlalchemy import select
    from db.models import Author, Book, BookAuthor
    from core.importer import import_download

    config = get_config()
    pp = getattr(config, "postprocess", None)
    library_root = getattr(pp, "library_root", "") if pp else ""
    if not library_root:
        return {"success": False, "detail": "postprocess.library_root not configured"}

    async with AsyncSessionFactory() as session:
        book_result = await session.execute(select(Book).where(Book.id == book_id))
        book = book_result.scalar_one_or_none()
        if book is None:
            return {"success": False, "detail": f"Book {book_id} not found"}

        author_result = await session.execute(
            select(Author.name)
            .join(BookAuthor, Author.id == BookAuthor.author_id)
            .where(BookAuthor.book_id == book_id, BookAuthor.role == "author")
            .limit(1)
        )
        author_name = author_result.scalar_one_or_none() or ""
        title = book.title or ""
        series = book.series_name or None

    # Run the blocking filesystem work in a thread pool
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: import_download(
            source=source_path,
            library_root=library_root,
            author=author_name,
            title=title,
            series=series,
        ),
    )

    if result.get("files_copied"):
        async with AsyncSessionFactory() as session:
            book_result = await session.execute(select(Book).where(Book.id == book_id))
            book = book_result.scalar_one_or_none()
            if book is not None:
                book.have_it = True
                book.match_method = "imported"
                await session.commit()

        redis_client = ctx.get("redis")
        if redis_client:
            import json as _json
            await redis_client.publish(
                "bookscout:events",
                _json.dumps({
                    "event": "import.complete",
                    "book_id": book_id,
                    "book_title": title,
                    "author_name": author_name,
                    "destination": result.get("destination", ""),
                    "files_copied": result.get("files_copied", []),
                }),
            )

    return {"success": True, "book_id": book_id, **result}

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
        from workers.settings import _redis_settings
        arq_redis = await create_pool(_redis_settings())
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

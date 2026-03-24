"""Audiobookshelf integration endpoints."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_config
from core.audiobookshelf import get_all_authors_from_audiobookshelf
from core.normalize import author_names_match, sort_name
from db.models import Author, Watchlist
from db.session import get_session

router = APIRouter(prefix="/audiobookshelf", tags=["audiobookshelf"])


@router.post(
    "/import-authors",
    summary="Bulk-import all Audiobookshelf authors into the watchlist",
)
async def import_authors(session: AsyncSession = Depends(get_session)) -> dict:
    """Fetch every author name from ABS libraries and add any unknown ones to the watchlist."""
    config = get_config()
    abs_cfg = getattr(config, "audiobookshelf", None)
    abs_url = getattr(abs_cfg, "url", "") if abs_cfg else ""
    abs_token = getattr(abs_cfg, "token", "") if abs_cfg else ""

    async with httpx.AsyncClient() as client:
        author_names = await get_all_authors_from_audiobookshelf(client, abs_url, abs_token)

    # Load all existing author names once so we can fuzzy-match against them.
    # This prevents "J.N. Chaney" and "JN Chaney" from creating two rows.
    existing_result = await session.execute(select(Author.name))
    existing_names: list[str] = [row[0] for row in existing_result.all()]

    added = 0
    for name in author_names:
        if any(author_names_match(name, ex) for ex in existing_names):
            continue
        author = Author(name=name, name_sort=sort_name(name))
        session.add(author)
        await session.flush()
        session.add(Watchlist(author_id=author.id))
        existing_names.append(name)  # guard against two variants in the same batch
        added += 1

    await session.commit()
    return {
        "imported": added,
        "skipped": len(author_names) - added,
        "total_from_abs": len(author_names),
    }




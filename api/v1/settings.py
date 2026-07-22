"""Application settings — user-configurable preferences stored in app_settings."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AppSetting
from db.session import get_session

router = APIRouter(prefix="/settings", tags=["settings"])

_DOWNLOAD_PREFS_KEY = "download_preferences"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DownloadPreferences(BaseModel):
    min_seeders: int = 1
    preferred_format: str = ""        # "m4b" | "mp3" | "" (any)
    language: str = "en"
    require_unabridged: bool = False
    max_size_gb: float = 0            # 0 = no limit
    # Behavior for authors with watchlist.auto_download enabled:
    # "approval" — record best match as a pending attempt for one-click approval
    # "auto"     — send the best match straight to the download client
    auto_download_mode: str = "approval"
    # Comma-separated indexer/tracker names. Results from preferred indexers
    # (e.g. private trackers) get a scoring bonus; fallback indexers (e.g. a
    # Jackett instance full of public trackers) get a penalty so they only
    # win when nothing better exists.
    preferred_indexers: str = ""
    fallback_indexers: str = ""
    # Indexer-politeness dials for automatic searching:
    # re-search an unfound book at most every N hours…
    search_cooldown_hours: int = 6
    # …wait this long between consecutive searches in one pass…
    search_delay_seconds: float = 3
    # …and search at most this many books per automatic pass.
    max_searches_per_run: int = 5


class DownloadPreferencesUpdate(BaseModel):
    min_seeders: int | None = None
    preferred_format: str | None = None
    language: str | None = None
    require_unabridged: bool | None = None
    max_size_gb: float | None = None
    auto_download_mode: str | None = None
    preferred_indexers: str | None = None
    fallback_indexers: str | None = None
    search_cooldown_hours: int | None = None
    search_delay_seconds: float | None = None
    max_searches_per_run: int | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/download-preferences", response_model=DownloadPreferences, summary="Get download quality preferences")
async def get_download_prefs(session: AsyncSession = Depends(get_session)) -> DownloadPreferences:
    row = await session.get(AppSetting, _DOWNLOAD_PREFS_KEY)
    if not row or not isinstance(row.value, dict):
        return DownloadPreferences()
    return DownloadPreferences(**{k: v for k, v in row.value.items() if k in DownloadPreferences.model_fields})


@router.patch("/download-preferences", response_model=DownloadPreferences, summary="Update download quality preferences")
async def update_download_prefs(
    body: DownloadPreferencesUpdate,
    session: AsyncSession = Depends(get_session),
) -> DownloadPreferences:
    row = await session.get(AppSetting, _DOWNLOAD_PREFS_KEY)
    current = DownloadPreferences()
    if row and isinstance(row.value, dict):
        current = DownloadPreferences(**{k: v for k, v in row.value.items() if k in DownloadPreferences.model_fields})

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(current, field, value)

    stmt = pg_insert(AppSetting).values(key=_DOWNLOAD_PREFS_KEY, value=current.model_dump())
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": current.model_dump(), "updated_at": func.now()},
    )
    await session.execute(stmt)
    await session.commit()
    return current

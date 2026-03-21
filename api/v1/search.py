"""Unified search (Prowlarr + Jackett) and download-client routing."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import httpx

from config import get_config
from core.search import send_to_sabnzbd, send_to_torrent_client, unified_search

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str


class DownloadRequest(BaseModel):
    url: str
    title: str
    type: str = "torrent"  # "nzb" | "torrent"


@router.post("/", summary="Unified Prowlarr + Jackett search")
async def search(body: SearchRequest) -> list[dict]:
    config = get_config()
    prowlarr = getattr(config, "prowlarr", None)
    jackett = getattr(config, "jackett", None)

    async with httpx.AsyncClient() as client:
        results = await unified_search(
            client,
            body.query,
            prowlarr_url=getattr(prowlarr, "url", "") if prowlarr else "",
            prowlarr_key=getattr(prowlarr, "api_key", "") if prowlarr else "",
            jackett_url=getattr(jackett, "url", "") if jackett else "",
            jackett_key=getattr(jackett, "api_key", "") if jackett else "",
        )

    # Annotate human-readable file size
    for r in results:
        size = r.get("size", 0) or 0
        if size >= 1_073_741_824:
            r["size_human"] = f"{size / 1_073_741_824:.2f} GB"
        elif size >= 1_048_576:
            r["size_human"] = f"{size / 1_048_576:.2f} MB"
        else:
            r["size_human"] = f"{size / 1024:.1f} KB"

    return results


@router.post("/download", summary="Send a result to the configured download client")
async def download(body: DownloadRequest) -> dict:
    config = get_config()
    dl = getattr(config, "download", None)
    preferred = getattr(dl, "preferred", "") if dl else ""

    async with httpx.AsyncClient() as client:
        if body.type == "nzb" or preferred == "sabnzbd":
            sabnzbd = getattr(dl, "sabnzbd", None) if dl else None
            success = await send_to_sabnzbd(
                client,
                body.url,
                body.title,
                sabnzbd_url=getattr(sabnzbd, "url", "") if sabnzbd else "",
                api_key=getattr(sabnzbd, "api_key", "") if sabnzbd else "",
            )
        else:
            torrent = getattr(dl, "torrent", None) if dl else None
            success = await send_to_torrent_client(
                client,
                body.url,
                body.title,
                client_type=getattr(torrent, "type", "qbittorrent") if torrent else "qbittorrent",
                client_url=getattr(torrent, "url", "") if torrent else "",
                username=getattr(torrent, "username", "") if torrent else "",
                password=getattr(torrent, "password", "") if torrent else "",
            )

    if not success:
        raise HTTPException(status_code=502, detail="Download client returned an error")
    return {"success": True}

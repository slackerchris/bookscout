"""Unified search (Prowlarr + Jackett) and download-client routing."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import httpx

from config import get_config
from core.search import (
    send_to_sabnzbd,
    send_to_torrent_client,
    unified_search,
    fetch_download_queue,
    check_prowlarr_status,
    check_jackett_status,
    check_sabnzbd_status,
    check_qbittorrent_status,
    check_transmission_status,
)

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str


class DownloadRequest(BaseModel):
    url: str
    title: str
    type: str = "torrent"          # "nzb" | "torrent"
    category: str = ""             # SABnzbd: category name; qBittorrent: label/category
    save_path: str = ""            # Explicit destination directory (all clients)


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
            result = await send_to_sabnzbd(
                client,
                body.url,
                body.title,
                sabnzbd_url=getattr(sabnzbd, "url", "") if sabnzbd else "",
                api_key=getattr(sabnzbd, "api_key", "") if sabnzbd else "",
                category=body.category or getattr(sabnzbd, "default_category", "") if sabnzbd else body.category,
            )
        else:
            torrent = getattr(dl, "torrent", None) if dl else None
            result = await send_to_torrent_client(
                client,
                body.url,
                body.title,
                client_type=getattr(torrent, "type", "qbittorrent") if torrent else "qbittorrent",
                client_url=getattr(torrent, "url", "") if torrent else "",
                username=getattr(torrent, "username", "") if torrent else "",
                password=getattr(torrent, "password", "") if torrent else "",
                category=body.category or getattr(torrent, "default_category", "") if torrent else body.category,
                save_path=body.save_path or getattr(torrent, "save_path", "") if torrent else body.save_path,
            )

    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("detail", "Download client returned an error"))
    return result


@router.get("/status", summary="Check indexer and download client connectivity")
async def download_status() -> dict:
    """
    Pings every configured indexer and download client and returns their
    reachability status — analogous to ``GET /api/v1/abs/status``.
    """
    config = get_config()
    prowlarr = getattr(config, "prowlarr", None)
    jackett = getattr(config, "jackett", None)
    dl = getattr(config, "download", None)
    preferred = getattr(dl, "preferred", "") if dl else ""
    sabnzbd = getattr(dl, "sabnzbd", None) if dl else None
    torrent = getattr(dl, "torrent", None) if dl else None
    torrent_type = getattr(torrent, "type", "qbittorrent") if torrent else "qbittorrent"

    async with httpx.AsyncClient() as client:
        indexer_checks = await asyncio.gather(
            check_prowlarr_status(
                client,
                getattr(prowlarr, "url", "") if prowlarr else "",
                getattr(prowlarr, "api_key", "") if prowlarr else "",
            ),
            check_jackett_status(
                client,
                getattr(jackett, "url", "") if jackett else "",
                getattr(jackett, "api_key", "") if jackett else "",
            ),
        )

        if preferred == "sabnzbd":
            dl_result = await check_sabnzbd_status(
                client,
                getattr(sabnzbd, "url", "") if sabnzbd else "",
                getattr(sabnzbd, "api_key", "") if sabnzbd else "",
            )
            dl_name = "sabnzbd"
        elif torrent_type == "transmission":
            dl_result = await check_transmission_status(
                client,
                getattr(torrent, "url", "") if torrent else "",
                getattr(torrent, "username", "") if torrent else "",
                getattr(torrent, "password", "") if torrent else "",
            )
            dl_name = "transmission"
        else:
            dl_result = await check_qbittorrent_status(
                client,
                getattr(torrent, "url", "") if torrent else "",
                getattr(torrent, "username", "") if torrent else "",
                getattr(torrent, "password", "") if torrent else "",
            )
            dl_name = "qbittorrent"

    return {
        "indexers": {
            "prowlarr": indexer_checks[0],
            "jackett": indexer_checks[1],
        },
        "download_client": {
            dl_name: dl_result,
        },
    }


@router.get("/download/queue", summary="Fetch the current download client queue")
async def download_queue() -> list[dict]:
    """
    Returns every item currently in the configured download client's queue,
    including progress, status, ETA, and destination path.

    - **SABnzbd**: active NZB slots with MB remaining + percentage
    - **qBittorrent**: all torrents with state, progress (0–100 %), ETA, save path
    - **Transmission**: all torrents with state, progress, ETA, download dir
    """
    config = get_config()
    dl = getattr(config, "download", None)
    preferred = getattr(dl, "preferred", "") if dl else ""
    sabnzbd = getattr(dl, "sabnzbd", None) if dl else None
    torrent = getattr(dl, "torrent", None) if dl else None

    async with httpx.AsyncClient() as client:
        return await fetch_download_queue(
            client,
            preferred=preferred,
            sabnzbd_url=getattr(sabnzbd, "url", "") if sabnzbd else "",
            sabnzbd_key=getattr(sabnzbd, "api_key", "") if sabnzbd else "",
            torrent_type=getattr(torrent, "type", "qbittorrent") if torrent else "qbittorrent",
            torrent_url=getattr(torrent, "url", "") if torrent else "",
            torrent_username=getattr(torrent, "username", "") if torrent else "",
            torrent_password=getattr(torrent, "password", "") if torrent else "",
        )

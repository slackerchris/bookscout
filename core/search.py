"""Async Prowlarr / Jackett search and download-client routing."""
from __future__ import annotations

import io
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_prowlarr(
    client: httpx.AsyncClient,
    query: str,
    prowlarr_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    if not prowlarr_url or not api_key:
        return []
    try:
        r = await client.get(
            f"{prowlarr_url}/api/v1/search",
            params={"query": query, "type": "search", "categories": [3030], "protocol": "torrent"},
            headers={"X-Api-Key": api_key},
            timeout=30,
        )
        if r.status_code != 200:
            return []
        return [
            {
                "title": item.get("title", ""),
                "source": "Prowlarr",
                "type": "NZB" if item.get("protocol") == "usenet" else "Torrent",
                "size": item.get("size", 0),
                "indexer": item.get("indexer", ""),
                "download_url": item.get("downloadUrl", ""),
                "magnet_url": item.get("magnetUrl", ""),
                "guid": item.get("guid", ""),
                "seeders": item.get("seeders", 0),
                "publish_date": item.get("publishDate", ""),
            }
            for item in r.json()
        ]
    except Exception as exc:
        logger.error("Prowlarr search failed", extra={"query": query, "error": str(exc)})
        return []


async def search_jackett(
    client: httpx.AsyncClient,
    query: str,
    jackett_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    if not jackett_url or not api_key:
        return []
    try:
        r = await client.get(
            f"{jackett_url}/api/v2.0/indexers/all/results",
            params={"apikey": api_key, "Query": query, "Category[]": [3030]},
            timeout=30,
        )
        if r.status_code != 200:
            return []
        return [
            {
                "title": item.get("Title", ""),
                "source": "Jackett",
                "type": "Torrent",
                "size": item.get("Size", 0),
                "indexer": item.get("Tracker", ""),
                "download_url": item.get("Link", ""),
                "magnet_url": item.get("MagnetUri", ""),
                "seeders": item.get("Seeders", 0),
                "leechers": item.get("Peers", 0),
                "publish_date": item.get("PublishDate", ""),
            }
            for item in r.json().get("Results", [])
        ]
    except Exception as exc:
        logger.error("Jackett search failed", extra={"query": query, "error": str(exc)})
        return []


async def unified_search(
    client: httpx.AsyncClient,
    query: str,
    prowlarr_url: str,
    prowlarr_key: str,
    jackett_url: str,
    jackett_key: str,
) -> list[dict[str, Any]]:
    import asyncio

    prowlarr_results, jackett_results = await asyncio.gather(
        search_prowlarr(client, query, prowlarr_url, prowlarr_key),
        search_jackett(client, query, jackett_url, jackett_key),
    )
    combined = prowlarr_results + jackett_results
    combined.sort(key=lambda x: (x.get("seeders", 0), x.get("size", 0)), reverse=True)
    return combined


# ---------------------------------------------------------------------------
# Download clients
# ---------------------------------------------------------------------------

async def send_to_sabnzbd(
    client: httpx.AsyncClient,
    download_url: str,
    title: str,
    sabnzbd_url: str,
    api_key: str,
    category: str = "",
) -> dict[str, Any]:
    """Send an NZB to SABnzbd.  *category* maps to a SABnzbd category (which
    controls the destination folder configured inside SABnzbd itself).

    Returns ``{"success": True, "nzo_id": "SABnzbd_nzo_abc123"}`` on success,
    or ``{"success": False, "detail": "..."}`` on failure.  The NZO ID can be
    used to poll SABnzbd's queue/history for download progress.
    """
    if not sabnzbd_url or not api_key:
        return {"success": False, "detail": "SABnzbd not configured"}
    try:
        nzb_r = await client.get(download_url, timeout=30, follow_redirects=True)
        if nzb_r.status_code != 200:
            return {"success": False, "detail": f"Failed to fetch NZB: HTTP {nzb_r.status_code}"}

        content = nzb_r.content
        base_params: dict[str, Any] = {
            "apikey": api_key,
            "output": "json",
            "nzbname": title,
        }
        if category:
            base_params["cat"] = category

        if not content.startswith(b"<?xml"):
            # Fallback: addurl mode
            r = await client.get(
                f"{sabnzbd_url}/api",
                params={"mode": "addurl", "name": download_url, **base_params},
                timeout=10,
            )
            if r.status_code == 200 and r.json().get("status"):
                nzo_ids = r.json().get("nzo_ids", [])
                return {"success": True, "nzo_id": nzo_ids[0] if nzo_ids else None}
            return {"success": False, "detail": r.json().get("error", "Unknown error")}

        r = await client.post(
            f"{sabnzbd_url}/api",
            params={"mode": "addfile", **base_params},
            files={"nzbfile": (f"{title}.nzb", io.BytesIO(content), "application/x-nzb")},
            timeout=10,
        )
        if r.status_code == 200 and r.json().get("status"):
            nzo_ids = r.json().get("nzo_ids", [])
            return {"success": True, "nzo_id": nzo_ids[0] if nzo_ids else None}
        return {"success": False, "detail": r.json().get("error", "Unknown error")}
    except Exception as exc:
        logger.error("SABnzbd send failed", extra={"title": title, "error": str(exc)})
        return {"success": False, "detail": str(exc)}


async def send_to_torrent_client(
    client: httpx.AsyncClient,
    download_url: str,
    title: str,
    client_type: str,
    client_url: str,
    username: str = "",
    password: str = "",
    category: str = "",
    tag: str = "",
    save_path: str = "",
    book_id: int = 0,
) -> dict[str, Any]:
    """Route to the configured torrent client.

    *category* — qBittorrent label / category (maps to a folder configured
    inside qBittorrent).  Ignored by Transmission.
    *save_path* — explicit download directory.  Supported by both
    qBittorrent (``savepath``) and Transmission (``download-dir``).
    When both are provided for qBittorrent, *save_path* takes precedence.
    *book_id* — when non-zero, qBittorrent torrents are tagged with
    ``bookscout-{book_id}`` so the post-process script can call back to
    the correct import endpoint.

    Returns ``{"success": True, "hash": "..."}`` where available (Transmission
    always returns a hash; qBittorrent does not expose one on add), or
    ``{"success": False, "detail": "..."}`` on failure.
    """
    if client_type == "qbittorrent":
        return await _send_qbittorrent(
            client, download_url, client_url, username, password,
            category=category, tag=tag, save_path=save_path, book_id=book_id,
        )
    if client_type == "transmission":
        return await _send_transmission(
            client, download_url, client_url, username, password,
            save_path=save_path,
        )
    logger.warning("Unsupported download client type", extra={"client_type": client_type})
    return {"success": False, "detail": f"Unsupported client type: {client_type}"}


async def _send_qbittorrent(
    client: httpx.AsyncClient,
    download_url: str,
    qbt_url: str,
    username: str,
    password: str,
    category: str = "",
    tag: str = "",
    save_path: str = "",
    book_id: int = 0,
) -> dict[str, Any]:
    try:
        lr = await client.post(
            f"{qbt_url}/api/v2/auth/login",
            data={"username": username, "password": password},
            timeout=10,
        )
        if lr.status_code != 200 or lr.text.strip() != "Ok.":
            return {"success": False, "detail": "Authentication failed"}
        payload: dict[str, str] = {"urls": download_url}
        if save_path:
            payload["savepath"] = save_path
        elif category:
            payload["category"] = category
        tag_parts = [p for p in [tag, f"bookscout-{book_id}" if book_id else ""] if p]
        if tag_parts:
            payload["tags"] = ",".join(tag_parts)
        r = await client.post(
            f"{qbt_url}/api/v2/torrents/add",
            data=payload,
            cookies=dict(lr.cookies),
            timeout=10,
        )
        if r.status_code == 200 and r.text.strip() == "Ok.":
            return {"success": True}
        return {"success": False, "detail": r.text.strip()}
    except Exception as exc:
        logger.error("qBittorrent send failed", extra={"error": str(exc)})
        return {"success": False, "detail": str(exc)}


async def _send_transmission(
    client: httpx.AsyncClient,
    download_url: str,
    tr_url: str,
    username: str,
    password: str,
    save_path: str = "",
) -> dict[str, Any]:
    try:
        auth = (username, password) if username else None
        sr = await client.get(f"{tr_url}/transmission/rpc", auth=auth, timeout=10)
        session_id = sr.headers.get("X-Transmission-Session-Id", "")
        arguments: dict[str, Any] = {"filename": download_url}
        if save_path:
            arguments["download-dir"] = save_path
        r = await client.post(
            f"{tr_url}/transmission/rpc",
            json={"method": "torrent-add", "arguments": arguments},
            headers={"X-Transmission-Session-Id": session_id},
            auth=auth,
            timeout=10,
        )
        body = r.json()
        if body.get("result") == "success":
            torrent = body.get("arguments", {}).get("torrent-added") or body.get("arguments", {}).get("torrent-duplicate")
            return {"success": True, "hash": torrent.get("hashString") if torrent else None}
        return {"success": False, "detail": body.get("result", "Unknown error")}
    except Exception as exc:
        logger.error("Transmission send failed", extra={"error": str(exc)})
        return {"success": False, "detail": str(exc)}


# ---------------------------------------------------------------------------
# Download queue
# ---------------------------------------------------------------------------

async def fetch_download_queue(
    client: httpx.AsyncClient,
    preferred: str,
    sabnzbd_url: str,
    sabnzbd_key: str,
    torrent_type: str,
    torrent_url: str,
    torrent_username: str,
    torrent_password: str,
) -> list[dict[str, Any]]:
    """Fetch the active download queue from whichever client is configured."""
    if preferred == "sabnzbd":
        return await _queue_sabnzbd(client, sabnzbd_url, sabnzbd_key)
    if torrent_type == "transmission":
        return await _queue_transmission(client, torrent_url, torrent_username, torrent_password)
    return await _queue_qbittorrent(client, torrent_url, torrent_username, torrent_password)


async def _queue_sabnzbd(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    if not url or not api_key:
        return []
    try:
        r = await client.get(
            f"{url}/api",
            params={"mode": "queue", "apikey": api_key, "output": "json"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        slots = r.json().get("queue", {}).get("slots", [])
        return [
            {
                "nzo_id": s.get("nzo_id"),
                "title": s.get("filename"),
                "status": s.get("status"),
                "size": s.get("mb", ""),
                "remaining": s.get("mbleft", ""),
                "percentage": s.get("percentage", ""),
                "eta": s.get("eta", ""),
            }
            for s in slots
        ]
    except Exception as exc:
        logger.error("SABnzbd queue fetch failed", extra={"error": str(exc)})
        return []


async def _queue_qbittorrent(
    client: httpx.AsyncClient,
    url: str,
    username: str,
    password: str,
) -> list[dict[str, Any]]:
    if not url:
        return []
    try:
        lr = await client.post(
            f"{url}/api/v2/auth/login",
            data={"username": username, "password": password},
            timeout=10,
        )
        if lr.status_code != 200 or lr.text.strip() != "Ok.":
            return []
        r = await client.get(
            f"{url}/api/v2/torrents/info",
            params={"filter": "all"},
            cookies=dict(lr.cookies),
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return [
            {
                "hash": t.get("hash"),
                "title": t.get("name"),
                "status": t.get("state"),
                "size": t.get("size"),
                "downloaded": t.get("downloaded"),
                "progress": round(t.get("progress", 0) * 100, 1),
                "eta": t.get("eta"),
                "category": t.get("category"),
                "save_path": t.get("save_path"),
            }
            for t in r.json()
        ]
    except Exception as exc:
        logger.error("qBittorrent queue fetch failed", extra={"error": str(exc)})
        return []


async def _queue_transmission(
    client: httpx.AsyncClient,
    url: str,
    username: str,
    password: str,
) -> list[dict[str, Any]]:
    if not url:
        return []
    try:
        auth = (username, password) if username else None
        sr = await client.get(f"{url}/transmission/rpc", auth=auth, timeout=10)
        session_id = sr.headers.get("X-Transmission-Session-Id", "")
        r = await client.post(
            f"{url}/transmission/rpc",
            json={
                "method": "torrent-get",
                "arguments": {
                    "fields": [
                        "hashString", "name", "status", "totalSize",
                        "downloadedEver", "percentDone", "eta",
                        "downloadDir", "error", "errorString",
                    ]
                },
            },
            headers={"X-Transmission-Session-Id": session_id},
            auth=auth,
            timeout=10,
        )
        if r.json().get("result") != "success":
            return []
        torrents = r.json().get("arguments", {}).get("torrents", [])
        _STATUS_MAP = {0: "stopped", 1: "check_wait", 2: "checking", 3: "download_wait", 4: "downloading", 5: "seed_wait", 6: "seeding"}
        return [
            {
                "hash": t.get("hashString"),
                "title": t.get("name"),
                "status": _STATUS_MAP.get(t.get("status", -1), "unknown"),
                "size": t.get("totalSize"),
                "downloaded": t.get("downloadedEver"),
                "progress": round(t.get("percentDone", 0) * 100, 1),
                "eta": t.get("eta"),
                "save_path": t.get("downloadDir"),
                "error": t.get("errorString") or None,
            }
            for t in torrents
        ]
    except Exception as exc:
        logger.error("Transmission queue fetch failed", extra={"error": str(exc)})
        return []


# ---------------------------------------------------------------------------
# Connectivity checks
# ---------------------------------------------------------------------------

async def check_prowlarr_status(
    client: httpx.AsyncClient,
    prowlarr_url: str,
    api_key: str,
) -> dict[str, Any]:
    if not prowlarr_url or not api_key:
        return {"configured": False}
    try:
        r = await client.get(
            f"{prowlarr_url}/api/v1/system/status",
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            return {"configured": True, "status": "ok", "version": r.json().get("version", "")}
        return {"configured": True, "status": "error", "detail": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"configured": True, "status": "error", "detail": str(exc)}


async def check_jackett_status(
    client: httpx.AsyncClient,
    jackett_url: str,
    api_key: str,
) -> dict[str, Any]:
    if not jackett_url or not api_key:
        return {"configured": False}
    try:
        r = await client.get(
            f"{jackett_url}/api/v2.0/indexers",
            params={"apikey": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            return {"configured": True, "status": "ok"}
        return {"configured": True, "status": "error", "detail": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"configured": True, "status": "error", "detail": str(exc)}


async def check_sabnzbd_status(
    client: httpx.AsyncClient,
    sabnzbd_url: str,
    api_key: str,
) -> dict[str, Any]:
    if not sabnzbd_url or not api_key:
        return {"configured": False}
    try:
        r = await client.get(
            f"{sabnzbd_url}/api",
            params={"mode": "version", "apikey": api_key, "output": "json"},
            timeout=10,
        )
        if r.status_code == 200:
            return {"configured": True, "status": "ok", "version": r.json().get("version", "")}
        return {"configured": True, "status": "error", "detail": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"configured": True, "status": "error", "detail": str(exc)}


async def check_qbittorrent_status(
    client: httpx.AsyncClient,
    qbt_url: str,
    username: str,
    password: str,
) -> dict[str, Any]:
    if not qbt_url:
        return {"configured": False}
    try:
        lr = await client.post(
            f"{qbt_url}/api/v2/auth/login",
            data={"username": username, "password": password},
            timeout=10,
        )
        if lr.status_code == 200 and lr.text.strip() == "Ok.":
            vr = await client.get(
                f"{qbt_url}/api/v2/app/version",
                cookies=dict(lr.cookies),
                timeout=10,
            )
            version = vr.text.strip() if vr.status_code == 200 else ""
            return {"configured": True, "status": "ok", "version": version}
        return {"configured": True, "status": "error", "detail": "Authentication failed"}
    except Exception as exc:
        return {"configured": True, "status": "error", "detail": str(exc)}


async def check_transmission_status(
    client: httpx.AsyncClient,
    tr_url: str,
    username: str,
    password: str,
) -> dict[str, Any]:
    if not tr_url:
        return {"configured": False}
    try:
        auth = (username, password) if username else None
        # Transmission responds with 409 + session-id header when auth is required — that means it's up.
        r = await client.get(f"{tr_url}/transmission/rpc", auth=auth, timeout=10)
        if r.status_code in (200, 409):
            return {"configured": True, "status": "ok"}
        return {"configured": True, "status": "error", "detail": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"configured": True, "status": "error", "detail": str(exc)}

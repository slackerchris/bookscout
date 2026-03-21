"""Async Prowlarr / Jackett search and download-client routing."""
from __future__ import annotations

import io
from typing import Any

import httpx


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
            params={"query": query, "type": "book"},
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
        print(f"[Prowlarr] search error: {exc}")
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
            params={"apikey": api_key, "Query": query},
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
        print(f"[Jackett] search error: {exc}")
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
) -> bool:
    if not sabnzbd_url or not api_key:
        return False
    try:
        nzb_r = await client.get(download_url, timeout=30, follow_redirects=True)
        if nzb_r.status_code != 200:
            return False

        content = nzb_r.content
        if not content.startswith(b"<?xml"):
            # Fallback: addurl mode
            r = await client.get(
                f"{sabnzbd_url}/api",
                params={
                    "mode": "addurl",
                    "name": download_url,
                    "nzbname": title,
                    "apikey": api_key,
                    "output": "json",
                },
                timeout=10,
            )
            return r.status_code == 200 and bool(r.json().get("status"))

        r = await client.post(
            f"{sabnzbd_url}/api",
            params={
                "mode": "addfile",
                "apikey": api_key,
                "output": "json",
                "nzbname": title,
            },
            files={"nzbfile": (f"{title}.nzb", io.BytesIO(content), "application/x-nzb")},
            timeout=10,
        )
        return r.status_code == 200 and bool(r.json().get("status"))
    except Exception as exc:
        print(f"[SABnzbd] error: {exc}")
        return False


async def send_to_torrent_client(
    client: httpx.AsyncClient,
    download_url: str,
    title: str,
    client_type: str,
    client_url: str,
    username: str = "",
    password: str = "",
) -> bool:
    """Route to the configured torrent client."""
    if client_type == "qbittorrent":
        return await _send_qbittorrent(client, download_url, client_url, username, password)
    if client_type == "transmission":
        return await _send_transmission(client, download_url, client_url, username, password)
    print(f"[download] unsupported client type: {client_type}")
    return False


async def _send_qbittorrent(
    client: httpx.AsyncClient,
    download_url: str,
    qbt_url: str,
    username: str,
    password: str,
) -> bool:
    try:
        lr = await client.post(
            f"{qbt_url}/api/v2/auth/login",
            data={"username": username, "password": password},
            timeout=10,
        )
        if lr.status_code != 200 or lr.text.strip() != "Ok.":
            return False
        r = await client.post(
            f"{qbt_url}/api/v2/torrents/add",
            data={"urls": download_url},
            cookies=dict(lr.cookies),
            timeout=10,
        )
        return r.status_code == 200 and r.text.strip() == "Ok."
    except Exception as exc:
        print(f"[qBittorrent] error: {exc}")
        return False


async def _send_transmission(
    client: httpx.AsyncClient,
    download_url: str,
    tr_url: str,
    username: str,
    password: str,
) -> bool:
    try:
        auth = (username, password) if username else None
        sr = await client.get(f"{tr_url}/transmission/rpc", auth=auth, timeout=10)
        session_id = sr.headers.get("X-Transmission-Session-Id", "")
        r = await client.post(
            f"{tr_url}/transmission/rpc",
            json={"method": "torrent-add", "arguments": {"filename": download_url}},
            headers={"X-Transmission-Session-Id": session_id},
            auth=auth,
            timeout=10,
        )
        return r.json().get("result") == "success"
    except Exception as exc:
        print(f"[Transmission] error: {exc}")
        return False

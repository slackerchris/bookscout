"""qBittorrent completed-download poller.

Native replacement for the external n8n "qBittorrent Poller" workflow.
Every poll cycle:

1. Log in to the qBittorrent Web API.
2. List completed torrents in the configured category.
3. Select candidates: torrents carrying a ``bookscout-<book_id>`` tag
   (stamped by ``_send_qbittorrent`` when the torrent was added) that have
   not already been marked ``bs-imported`` or ``bs-failed``.
4. Import each candidate into the library via the normal import pipeline.
5. Tag the torrent ``bs-imported`` on success or ``bs-failed`` on failure,
   so it is never re-processed.  (To retry a failed import, remove the
   ``bs-failed`` tag in qBittorrent.)

Credentials come from ``config.yaml`` (``download.torrent``) — never from a
workflow file.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BOOK_TAG_RE = re.compile(r"^bookscout-(\d+)$")
TAG_IMPORTED = "bs-imported"
TAG_FAILED = "bs-failed"


def login_ok(status_code: int, text: str) -> bool:
    """True when a qBittorrent ``auth/login`` response indicates success.

    Password auth answers ``200 Ok.`` (or ``200 Fails.`` on bad credentials).
    When the client IP is on the WebUI's auth-bypass whitelist, qBittorrent
    instead replies ``204`` with an empty body — no SID cookie is issued or
    needed for subsequent calls.
    """
    return 200 <= status_code < 300 and text.strip() != "Fails."


async def login(client: httpx.AsyncClient, url: str, username: str, password: str) -> dict | None:
    """Return session cookies on success, None on auth failure."""
    try:
        r = await client.post(
            f"{url}/api/v2/auth/login",
            data={"username": username, "password": password},
            timeout=10,
        )
        if login_ok(r.status_code, r.text):
            return dict(r.cookies)  # empty when auth was bypassed — that's fine
    except Exception as exc:
        logger.error("qBittorrent login failed", extra={"error": str(exc)})
    return None


async def fetch_completed_torrents(
    client: httpx.AsyncClient, url: str, cookies: dict, category: str
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"filter": "completed"}
    if category:
        params["category"] = category
    r = await client.get(
        f"{url}/api/v2/torrents/info", params=params, cookies=cookies, timeout=15
    )
    if r.status_code != 200:
        logger.warning("qBittorrent torrent list failed", extra={"status": r.status_code})
        return []
    return r.json()


def select_import_candidates(torrents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick torrents that are ready to import.

    A candidate has a ``bookscout-<id>`` tag and neither the imported nor the
    failed marker tag.  Returns dicts with ``hash``, ``name``, ``book_id``,
    and ``content_path`` (falling back to ``save_path/name`` when qBittorrent
    doesn't report a content path).
    """
    candidates: list[dict[str, Any]] = []
    for t in torrents:
        if not isinstance(t, dict):
            continue
        tags = [s.strip() for s in str(t.get("tags") or "").split(",") if s.strip()]
        book_id: int | None = None
        for tag in tags:
            m = _BOOK_TAG_RE.match(tag)
            if m:
                book_id = int(m.group(1))
                break
        if book_id is None or TAG_IMPORTED in tags or TAG_FAILED in tags:
            continue

        content_path = str(t.get("content_path") or "").strip()
        if not content_path:
            save_path = str(t.get("save_path") or "")
            name = str(t.get("name") or "")
            if save_path and name:
                content_path = f"{save_path.rstrip('/')}/{name}"
        if not content_path:
            continue

        candidates.append({
            "hash": t.get("hash", ""),
            "name": t.get("name", ""),
            "book_id": book_id,
            "content_path": content_path,
        })
    return candidates


async def set_tags(
    client: httpx.AsyncClient,
    url: str,
    cookies: dict,
    torrent_hash: str,
    add: str | None = None,
    remove: str | None = None,
) -> None:
    """Add/remove tags on a torrent; failures are logged, not raised."""
    for endpoint, tag in (("addTags", add), ("removeTags", remove)):
        if not tag:
            continue
        try:
            r = await client.post(
                f"{url}/api/v2/torrents/{endpoint}",
                data={"hashes": torrent_hash, "tags": tag},
                cookies=cookies,
                timeout=10,
            )
            if r.status_code != 200:
                logger.warning(
                    "qBittorrent tag update failed",
                    extra={"endpoint": endpoint, "hash": torrent_hash, "status": r.status_code},
                )
        except Exception as exc:
            logger.warning(
                "qBittorrent tag update failed",
                extra={"endpoint": endpoint, "hash": torrent_hash, "error": str(exc)},
            )

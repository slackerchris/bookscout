"""Async Audiobookshelf API client.

Accepts pre-configured URL and token so callers remain in control of config.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def check_audiobookshelf(
    client: httpx.AsyncClient,
    book_title: str,
    author_name: str,
    abs_url: str,
    abs_token: str,
) -> tuple[bool, str | None, str | None]:
    """Check whether *book_title* exists in any Audiobookshelf library.

    Returns ``(has_book, series_name, series_position)``.
    """
    if not abs_url or not abs_token:
        return False, None, None

    headers = {"Authorization": f"Bearer {abs_token}"}
    normalized = book_title.lower().replace(":", "").replace(",", "").replace("-", " ").strip()
    title_words = set(normalized.split())

    try:
        r = await client.get(f"{abs_url}/api/libraries", headers=headers, timeout=10)
        if r.status_code != 200:
            return False, None, None

        libraries: list[dict] = r.json().get("libraries", [])

        for library in libraries:
            sr = await client.get(
                f"{abs_url}/api/libraries/{library['id']}/search",
                params={"q": book_title},
                headers=headers,
                timeout=10,
            )
            if sr.status_code != 200:
                continue

            for item in sr.json().get("book", []):
                metadata = (
                    item.get("libraryItem", {})
                    .get("media", {})
                    .get("metadata", {})
                )
                abs_title = metadata.get("title", "").lower().strip()

                matched = False
                if book_title.lower() in abs_title or abs_title in book_title.lower():
                    matched = True
                else:
                    # Fuzzy: ≥75 % word overlap
                    abs_words = set(
                        abs_title.replace(":", "").replace(",", "").replace("-", " ").split()
                    )
                    if title_words and len(title_words & abs_words) / len(title_words) >= 0.75:
                        matched = True

                if matched:
                    series_list: list[dict] = metadata.get("series", [])
                    sn = series_list[0].get("name") if series_list else None
                    sp = series_list[0].get("sequence") if series_list else None
                    return True, sn, sp

    except Exception as exc:
        logger.error("ABS ownership check failed", extra={"title": book_title, "author": author_name, "error": str(exc)})

    return False, None, None


async def get_all_authors_from_audiobookshelf(
    client: httpx.AsyncClient,
    abs_url: str,
    abs_token: str,
) -> list[str]:
    """Return a sorted list of every unique author name found in ABS libraries."""
    if not abs_url or not abs_token:
        return []

    headers = {"Authorization": f"Bearer {abs_token}"}
    authors: set[str] = set()

    try:
        r = await client.get(f"{abs_url}/api/libraries", headers=headers, timeout=10)
        if r.status_code != 200:
            return []

        for library in r.json().get("libraries", []):
            page, limit, total_processed = 0, 100, 0
            while True:
                ir = await client.get(
                    f"{abs_url}/api/libraries/{library['id']}/items",
                    params={"limit": limit, "page": page},
                    headers=headers,
                    timeout=30,
                )
                if ir.status_code != 200:
                    break

                data = ir.json()
                items: list[dict] = data.get("results", [])
                if not items:
                    break

                for item in items:
                    raw = item.get("media", {}).get("metadata", {}).get("authorName", "")
                    if raw:
                        # Normalise multi-author strings
                        for sep in (" & ", " and ", ", "):
                            raw = raw.replace(sep, "||")
                        for part in raw.split("||"):
                            part = part.strip()
                            if len(part) > 1:
                                authors.add(part)

                total_processed += len(items)
                if total_processed >= data.get("total", 0):
                    break
                page += 1

    except Exception as exc:
        logger.error("ABS authors fetch failed", extra={"error": str(exc)})

    return sorted(authors)

"""Async Audiobookshelf API client.

Accepts pre-configured URL and token so callers remain in control of config.
"""
from __future__ import annotations

import logging
import re

import httpx

from core.normalize import normalize_author_name

# Matches role annotations appended to author names in ABS metadata, e.g.:
#   "Christopher Tolkien - editor"
#   "Jane Doe (narrator)"
#   "John Smith - Author & Narrator"
#   "Someone - Translator & Editor"
_ROLE_SUFFIX_RE = re.compile(
    r"\s*[-–(]\s*(?:editor|narrator|author|translator|illustrator|foreword|afterword|introduction|contributor)"
    r"(?:\s*[&,]\s*(?:editor|narrator|author|translator|illustrator|foreword|afterword|introduction|contributor))*"
    r"\s*\)?$",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)

# Author name strings that appear in ABS metadata but are not real authors.
_NOISE_AUTHORS: frozenset[str] = frozenset({
    "others", "various", "various authors", "unknown", "unknown author",
    "multiple authors", "multiple narrators", "narrators",
})


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
    # Map normalized key → best display-name seen so far.  Using a dict lets
    # us deduplicate "J.N. Chaney", "JN Chaney", "j.n. chaney" etc. into a
    # single canonical entry while keeping the most informative display name.
    seen: dict[str, str] = {}  # normalize_author_name(name) → display name

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
                            if len(part) <= 1:
                                continue
                            # Strip role annotations: "- editor", "(narrator)", etc.
                            part = _ROLE_SUFFIX_RE.sub("", part).strip()
                            if len(part) <= 1:
                                continue
                            if part.lower() in _NOISE_AUTHORS:
                                continue
                            key = normalize_author_name(part)
                            if key not in seen:
                                seen[key] = part
                            elif len(part) > len(seen[key]):
                                # Prefer the more detailed display form
                                seen[key] = part

                total_processed += len(items)
                if total_processed >= data.get("total", 0):
                    break
                page += 1

    except Exception as exc:
        logger.error("ABS authors fetch failed", extra={"error": str(exc)})

    return sorted(seen.values())

"""Async Audiobookshelf API client.

Accepts pre-configured URL and token so callers remain in control of config.
"""
from __future__ import annotations

import logging
import re

import httpx

from core.normalize import abs_search_title, author_names_match, normalize_author_name, normalize_title_key

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

# Audiobook-specific parentheticals that should be stripped from titles at import.
# e.g. "The Land: Founding (Unabridged)" → "The Land: Founding"
_ABS_TITLE_NOISE_RE = re.compile(
    r"\s*\(\s*(?:un)?abridged(?:\s+edition)?\s*\)",
    re.IGNORECASE,
)


def _clean_abs_title(title: str) -> str:
    """Strip audiobook-specific noise from an ABS item title."""
    return _ABS_TITLE_NOISE_RE.sub("", title).strip()

logger = logging.getLogger(__name__)

# Cache ABS author ID resolutions to avoid re-fetching and re-scanning the
# full /authors list on every scan.  Key: (abs_url, lib_id, normalized_author_name)
# Value: ABS author ID string.  Cleared on worker restart.
_abs_author_id_cache: dict[tuple[str, str, str], str] = {}

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
    # Use a simplified title for both the ABS search query and word-overlap
    # comparison.  Verbose metadata API titles like "The Land: Founding: A
    # LitRPG Saga (Chaos Seeds) (Volume 1)" would otherwise produce a large
    # word set that swamps the overlap ratio against ABS's shorter stored title.
    query_title = abs_search_title(book_title)
    normalized = query_title.lower().replace(":", "").replace(",", "").replace("-", " ").strip()
    title_words = set(normalized.split())

    try:
        r = await client.get(f"{abs_url}/api/libraries", headers=headers, timeout=10)
        if r.status_code != 200:
            return False, None, None

        libraries: list[dict] = r.json().get("libraries", [])

        for library in libraries:
            sr = await client.get(
                f"{abs_url}/api/libraries/{library['id']}/search",
                params={"q": query_title},
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
                if query_title.lower() in abs_title or abs_title in query_title.lower():
                    matched = True
                else:
                    # Fuzzy: ≥75 % word overlap — divide by the *shorter* word
                    # set so a concise ABS title matches a longer metadata title.
                    abs_words = set(
                        abs_title.replace(":", "").replace(",", "").replace("-", " ").split()
                    )
                    denom = min(len(title_words), len(abs_words)) if abs_words else 0
                    if denom and len(title_words & abs_words) / denom >= 0.75:
                        matched = True

                if matched:
                    series_list: list[dict] = metadata.get("series", [])
                    sn = series_list[0].get("name") if series_list else None
                    sp = series_list[0].get("sequence") if series_list else None
                    return True, sn, sp

    except Exception as exc:
        logger.error("ABS ownership check failed", extra={"title": book_title, "author": author_name, "error": str(exc)})

    return False, None, None


async def fetch_abs_books_for_author(
    client: httpx.AsyncClient,
    author_name: str,
    abs_url: str,
    abs_token: str,
) -> dict[str, dict]:
    """Fetch all ABS library items for *author_name* in one paginated pass.

    Finds the ABS-internal author ID via ``/api/libraries/{id}/authors``, then
    pages through ``/api/libraries/{id}/items?filter=authors.<base64_id>`` to
    retrieve only that author's items rather than searching per-title.

    Returns a dict mapping ``normalize_title_key(title)`` → metadata dict::

        {
            "series_name": str | None,
            "series_position": str | None,
            "asin": str | None,
        }

    An empty dict is returned when ABS is not configured, the author is not
    found in any library, or communication fails.
    """
    import base64

    if not abs_url or not abs_token:
        return {}

    headers = {"Authorization": f"Bearer {abs_token}"}
    result: dict[str, dict] = {}

    try:
        r = await client.get(f"{abs_url}/api/libraries", headers=headers, timeout=10)
        if r.status_code != 200:
            return {}

        for library in r.json().get("libraries", []):
            if library.get("mediaType") not in (None, "book"):
                continue

            lib_id = library["id"]

            # ── Resolve ABS author ID (cached) ─────────────────────────────
            cache_key = (abs_url, lib_id, normalize_author_name(author_name))
            abs_author_id: str | None = _abs_author_id_cache.get(cache_key)

            if abs_author_id is None:
                ar = await client.get(
                    f"{abs_url}/api/libraries/{lib_id}/authors",
                    headers=headers,
                    timeout=10,
                )
                if ar.status_code != 200:
                    continue

                for abs_author in ar.json().get("authors", []):
                    if author_names_match(author_name, abs_author.get("name", "")):
                        abs_author_id = abs_author["id"]
                        _abs_author_id_cache[cache_key] = abs_author_id
                        break

            if not abs_author_id:
                continue

            # ── Page through items filtered to this author ─────────────────
            filter_val = base64.b64encode(abs_author_id.encode()).decode()
            page, limit, total_processed = 0, 100, 0

            while True:
                ir = await client.get(
                    f"{abs_url}/api/libraries/{lib_id}/items",
                    params={"filter": f"authors.{filter_val}", "limit": limit, "page": page},
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
                    meta = item.get("media", {}).get("metadata", {})
                    raw_title = meta.get("title", "").strip()
                    if not raw_title:
                        continue
                    title = _clean_abs_title(raw_title)
                    series_list: list[dict] = meta.get("series", []) or []
                    sn = series_list[0].get("name") if series_list else None
                    sp = series_list[0].get("sequence") if series_list else None
                    key = normalize_title_key(title)
                    result[key] = {
                        "series_name": sn,
                        "series_position": sp,
                        "asin": meta.get("asin") or None,
                    }

                total_processed += len(items)
                if total_processed >= data.get("total", 0):
                    break
                page += 1

    except Exception as exc:
        logger.error(
            "ABS author books fetch failed",
            extra={"author": author_name, "error": str(exc)},
        )

    return result


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


async def get_all_books_from_audiobookshelf(
    client: httpx.AsyncClient,
    abs_url: str,
    abs_token: str,
) -> list[dict]:
    """Return every book in all ABS libraries as a list of dicts.

    Each dict contains:
      title, author_name, series_name, series_position, asin, isbn, cover_url
    """
    if not abs_url or not abs_token:
        return []

    headers = {"Authorization": f"Bearer {abs_token}"}
    books: list[dict] = []

    try:
        r = await client.get(f"{abs_url}/api/libraries", headers=headers, timeout=10)
        if r.status_code != 200:
            return []

        for library in r.json().get("libraries", []):
            # Only process book libraries, not podcast libraries
            if library.get("mediaType") not in (None, "book"):
                continue

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
                    meta = item.get("media", {}).get("metadata", {})
                    title = _clean_abs_title(meta.get("title", "").strip())
                    if not title:
                        continue

                    # Split multi-author strings and take the first clean name,
                    # filtering out role annotations and noise values.
                    raw_author = meta.get("authorName", "").strip()
                    for sep in (" & ", " and ", ", "):
                        raw_author = raw_author.replace(sep, "||")
                    primary_author: str | None = None
                    for part in raw_author.split("||"):
                        part = _ROLE_SUFFIX_RE.sub("", part.strip()).strip()
                        if len(part) <= 1 or part.lower() in _NOISE_AUTHORS:
                            continue
                        primary_author = part
                        break

                    series_list = meta.get("series", []) or []
                    series_name = series_list[0].get("name") if series_list else None
                    series_pos = series_list[0].get("sequence") if series_list else None

                    books.append({
                        "title": title,
                        "author_name": primary_author,
                        "series_name": series_name,
                        "series_position": series_pos,
                        "asin": meta.get("asin") or None,
                        "isbn": meta.get("isbn") or None,
                        "cover_url": item.get("media", {}).get("coverPath") or None,
                    })

                total_processed += len(items)
                if total_processed >= data.get("total", 0):
                    break
                page += 1

    except Exception as exc:
        logger.error("ABS books fetch failed", extra={"error": str(exc)})

    return books

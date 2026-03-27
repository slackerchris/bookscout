"""Async wrappers around external book-metadata APIs.

All functions accept an ``httpx.AsyncClient`` so they share connection pools
with the rest of the scan pipeline.  They are intentionally stateless — no DB
access, no config reads — so they're easy to test in isolation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from core.normalize import author_names_match

logger = logging.getLogger(__name__)

OPENLIBRARY_API = "https://openlibrary.org/search.json"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
AUDNEXUS_API = "https://api.audnex.us"
AUDIBLE_CATALOG_API = "https://api.audible.com/1.0/catalog/products"
ISBNDB_API = "https://api2.isbndb.com"

# OpenLibrary returns ISO 639-2 (3-letter) codes; normalise to ISO 639-1 (2-letter)
# so all sources share the same code convention used by language_filter.
_LANG_639_2_TO_1: dict[str, str] = {
    "eng": "en",
    "ger": "de",
    "deu": "de",
    "fre": "fr",
    "fra": "fr",
    "spa": "es",
    "por": "pt",
    "ita": "it",
    "dut": "nl",
    "nld": "nl",
    "pol": "pl",
    "rus": "ru",
    "jpn": "ja",
    "chi": "zh",
    "zho": "zh",
    "kor": "ko",
    "swe": "sv",
    "dan": "da",
    "nor": "no",
    "fin": "fi",
    "cze": "cs",
    "ces": "cs",
    "hun": "hu",
    "rum": "ro",
    "ron": "ro",
    "tur": "tr",
    "ara": "ar",
    "heb": "he",
    "hin": "hi",
}

# Audnexus returns full language names; normalise to ISO 639-1 codes to match
# the language_filter convention used by OpenLibrary and Google Books.
_LANG_NAME_TO_ISO: dict[str, str] = {
    "english": "en",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "portuguese": "pt",
    "italian": "it",
    "dutch": "nl",
    "polish": "pl",
    "russian": "ru",
    "japanese": "ja",
    "chinese": "zh",
    "korean": "ko",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "czech": "cs",
    "hungarian": "hu",
    "romanian": "ro",
    "turkish": "tr",
}


async def query_openlibrary(
    client: httpx.AsyncClient,
    author_name: str,
    language_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch up to 200 books from Open Library for *author_name*."""
    try:
        r = await client.get(
            OPENLIBRARY_API,
            params={"author": author_name, "limit": 200},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.error("OpenLibrary query failed", extra={"author": author_name, "error": str(exc), "exc_type": type(exc).__name__})
        return []

    books: list[dict[str, Any]] = []
    for doc in data.get("docs", []):
        author_names = doc.get("author_name", [])
        if author_names and not any(
            author_names_match(author_name, n) for n in author_names
        ):
            continue

        # OpenLibrary uses ISO 639-2 (3-letter) codes; normalise to ISO 639-1.
        # OL rolls up all editions so a book may list multiple languages.
        raw_languages: list[str] = doc.get("language", [])
        book_languages: list[str] = [_LANG_639_2_TO_1.get(c, c) for c in raw_languages]
        if (
            language_filter
            and language_filter != "all"
            and language_filter not in book_languages
        ):
            continue
        # If filtering by a specific language and the book matched, store that
        # language (not whichever OL happened to list first).
        if language_filter and language_filter != "all" and language_filter in book_languages:
            primary_language = language_filter
        else:
            primary_language = book_languages[0] if book_languages else None

        isbn_list: list[str] = doc.get("isbn") or []
        book: dict[str, Any] = {
            "title": doc.get("title", ""),
            "subtitle": doc.get("subtitle", ""),
            "isbn": isbn_list[0] if isbn_list else None,
            "isbn13": next((i for i in isbn_list if len(i) == 13), None),
            "release_date": str(doc.get("first_publish_year", "")),
            "cover_url": (
                f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-M.jpg"
                if doc.get("cover_i")
                else None
            ),
            "language": primary_language,
            "source": "OpenLibrary",
            "authors": author_names,
        }
        if book["title"]:
            books.append(book)

    return books


async def query_google_books(
    client: httpx.AsyncClient,
    author_name: str,
    language_filter: str | None = None,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch up to 120 books (3 pages × 40) from Google Books for *author_name*."""
    params: dict[str, Any] = {
        "q": f'inauthor:"{author_name}"',
        "maxResults": 40,
    }
    if api_key:
        params["key"] = api_key
    if language_filter and language_filter != "all":
        params["langRestrict"] = language_filter

    try:
        r = await client.get(GOOGLE_BOOKS_API, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        logger.error(
            "GoogleBooks HTTP error",
            extra={"author": author_name, "status": exc.response.status_code, "body": body},
        )
        return []
    except Exception as exc:
        logger.error("GoogleBooks query failed", extra={"author": author_name, "error": str(exc)})
        return []

    all_items: list[dict] = list(data.get("items", []))
    total = data.get("totalItems", 0)

    # Fetch pages 2 and 3 if more results are available
    if total > 40:
        for start_index in (40, 80):
            if start_index >= total:
                break
            try:
                pr = await client.get(
                    GOOGLE_BOOKS_API,
                    params={**params, "startIndex": start_index},
                    timeout=15,
                )
                pr.raise_for_status()
                all_items.extend(pr.json().get("items", []))
            except Exception:
                break

    books: list[dict[str, Any]] = []
    for item in all_items:
        vi = item.get("volumeInfo", {})
        book_authors: list[str] = vi.get("authors", [])
        if book_authors and not any(
            author_names_match(author_name, a) for a in book_authors
        ):
            continue

        ids = {
            x["type"]: x["identifier"]
            for x in vi.get("industryIdentifiers", [])
        }
        lang = vi.get("language", "en")
        if language_filter and language_filter != "all" and lang != language_filter:
            continue

        book: dict[str, Any] = {
            "title": vi.get("title", ""),
            "subtitle": vi.get("subtitle", ""),
            "isbn": ids.get("ISBN_10"),
            "isbn13": ids.get("ISBN_13"),
            "release_date": vi.get("publishedDate", ""),
            "cover_url": vi.get("imageLinks", {}).get("thumbnail"),
            "description": vi.get("description", ""),
            "language": lang,
            "source": "GoogleBooks",
            "authors": book_authors,
        }
        if book["title"]:
            books.append(book)

    return books


async def query_audnexus(
    client: httpx.AsyncClient,
    author_name: str,
    language_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch audiobooks via the Audible catalog API with Audnexus per-book enrichment.

    The Audnexus ``/search`` endpoint is gone (HTTP 404).  We now use the
    Audible catalog API to discover audiobooks (with series data included) and
    call Audnexus ``/books/{asin}`` per result for cover, ISBN, and runtime.
    """
    # --- Step 1: collect products from Audible catalog (paginated) -----------
    all_products: list[dict] = []
    per_page = 50
    max_pages = 20  # hard ceiling of 1000 results — more than any author needs

    for page in range(max_pages):
        try:
            r = await client.get(
                AUDIBLE_CATALOG_API,
                params={
                    "author": author_name,
                    "num_results": per_page,
                    "page": page,
                    "products_sort_by": "Relevance",
                    "response_groups": "product_desc,contributors,series",
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.error("Audible page fetch failed", extra={"author": author_name, "page": page, "error": str(exc)})
            break

        products = data.get("products", [])
        if not products:
            break

        for product in products:
            product_authors = [
                a.get("name", "") for a in (product.get("authors") or [])
            ]
            if product_authors and not any(
                author_names_match(author_name, a) for a in product_authors
            ):
                continue
            all_products.append(product)

        if (page + 1) * per_page >= data.get("total_results", 0):
            break

    if not all_products:
        return []

    # --- Step 2: enrich each book with Audnexus /books/{asin} ----------------
    sem = asyncio.Semaphore(8)

    async def _enrich(product: dict) -> dict[str, Any] | None:
        asin = product.get("asin", "")
        title = product.get("title", "")
        if not asin or not title:
            return None

        product_authors = [
            a.get("name", "") for a in (product.get("authors") or [])
        ]
        product_narrators = [
            n.get("name", "") for n in (product.get("narrators") or [])
            if n.get("name")
        ]
        series_list: list[dict] = product.get("series") or []
        # Prefer a series entry that carries a sequence number
        primary = next(
            (s for s in series_list if s.get("sequence")),
            series_list[0] if series_list else None,
        )

        book: dict[str, Any] = {
            "title": title,
            "subtitle": product.get("subtitle", ""),
            "asin": asin,
            "release_date": None,
            "cover_url": None,
            "format": "audiobook",
            "language": None,  # overwritten by Audnexus detail; None = unknown
            "source": "Audnexus",
            "authors": product_authors or [author_name],
            "narrators": product_narrators,
            "series": primary.get("title") if primary else None,
            "series_position": primary.get("sequence") if primary else None,
        }

        # Best-effort enrichment: cover, ISBN, release date, language, canonical series
        async with sem:
            try:
                dr = await client.get(
                    f"{AUDNEXUS_API}/books/{asin}", timeout=10
                )
                if dr.status_code == 200:
                    detail = dr.json()
                    book["cover_url"] = detail.get("image")
                    book["release_date"] = detail.get("releaseDate")
                    book["isbn"] = detail.get("isbn")
                    book["description"] = (
                        detail.get("summary") or detail.get("description", "")
                    )
                    if detail.get("language"):
                        raw_lang = detail["language"].lower()
                        book["language"] = _LANG_NAME_TO_ISO.get(raw_lang, raw_lang)
                    # Audnexus seriesPrimary is the canonical source — override
                    sp = detail.get("seriesPrimary")
                    if sp:
                        book["series"] = sp.get("name")
                        book["series_position"] = sp.get("position")
                    # Audnexus narrator list (overrides Audible's if present)
                    detail_narrators = [
                        n.get("name", "") for n in (detail.get("narrators") or [])
                        if n.get("name")
                    ]
                    if detail_narrators:
                        book["narrators"] = detail_narrators
            except Exception:
                pass

        # Apply language filter (post-enrichment, since Audible API has no lang filter).
        # Books whose language is still None (Audnexus lookup failed / not in Audnexus)
        # are excluded when a filter is active rather than assumed to be English.
        if language_filter and language_filter != "all":
            if book["language"] != language_filter:
                return None

        return book

    results = await asyncio.gather(
        *[_enrich(p) for p in all_products],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, dict)]


async def query_isbndb(
    client: httpx.AsyncClient,
    author_name: str,
    api_key: str,
    language_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch books from ISBNdb (premium API key required)."""
    if not api_key:
        return []
    try:
        r = await client.get(
            f"{ISBNDB_API}/author/{author_name}",
            headers={"Authorization": api_key},
            params={"page": 1, "pageSize": 100},
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning("ISBNdb non-200 response", extra={"author": author_name, "status": r.status_code})
            return []
        data = r.json()
    except Exception as exc:
        logger.error("ISBNdb query failed", extra={"author": author_name, "error": str(exc)})
        return []

    books: list[dict[str, Any]] = []
    for item in data.get("books", []):
        lang = item.get("language") or None
        # Exclude books with unknown language when a filter is active rather than
        # assuming English.
        if language_filter and language_filter != "all" and lang != language_filter:
            continue
        title = item.get("title", "")
        if not title:
            continue
        books.append(
            {
                "title": title,
                "subtitle": item.get("title_long", "").replace(title, "").strip(),
                "isbn": item.get("isbn"),
                "isbn13": item.get("isbn13"),
                "release_date": item.get("date_published", ""),
                "cover_url": item.get("image"),
                "description": item.get("synopsis", ""),
                "language": lang,
                "source": "ISBNdb",
                "authors": item.get("authors") or [author_name],
            }
        )
    return books


async def search_audible_metadata_direct(
    client: httpx.AsyncClient,
    book_title: str,
    author_name: str,
) -> tuple[str | None, str | None]:
    """Two-step series lookup: Audible API → ASIN → Audnexus full metadata.

    Returns ``(series_name, series_position)`` or ``(None, None)``.
    """
    try:
        params: dict[str, Any] = {
            "num_results": "1",
            "products_sort_by": "Relevance",
            "title": book_title,
        }
        if author_name:
            params["author"] = author_name

        r = await client.get(
            AUDIBLE_CATALOG_API,
            params=params,
            timeout=10,
        )
        if r.status_code != 200:
            return None, None

        products = r.json().get("products", [])
        if not products:
            return None, None

        asin = products[0].get("asin")
        if not asin:
            return None, None

        dr = await client.get(f"{AUDNEXUS_API}/books/{asin}", timeout=10)
        if dr.status_code != 200:
            return None, None

        series_primary = dr.json().get("seriesPrimary")
        if series_primary:
            return series_primary.get("name"), series_primary.get("position")

    except Exception as exc:
        logger.warning("Audible direct metadata lookup failed", extra={"title": book_title, "error": str(exc)})

    return None, None

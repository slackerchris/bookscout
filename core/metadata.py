"""Async wrappers around external book-metadata APIs.

All functions accept an ``httpx.AsyncClient`` so they share connection pools
with the rest of the scan pipeline.  They are intentionally stateless — no DB
access, no config reads — so they're easy to test in isolation.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from core.normalize import author_names_match

OPENLIBRARY_API = "https://openlibrary.org/search.json"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
AUDNEXUS_API = "https://api.audnex.us"
ISBNDB_API = "https://api2.isbndb.com"


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
        print(f"[OpenLibrary] error: {exc}")
        return []

    books: list[dict[str, Any]] = []
    for doc in data.get("docs", []):
        author_names = doc.get("author_name", [])
        if author_names and not any(
            author_names_match(author_name, n) for n in author_names
        ):
            continue

        book_languages: list[str] = doc.get("language", ["en"])
        if (
            language_filter
            and language_filter != "all"
            and language_filter not in book_languages
        ):
            continue

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
            "language": book_languages[0] if book_languages else "en",
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
    except Exception as exc:
        print(f"[GoogleBooks] error: {exc}")
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
    """Fetch audiobooks from Audnexus.  Fetches per-book details concurrently."""
    try:
        r = await client.get(
            f"{AUDNEXUS_API}/search",
            params={"name": author_name},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        search_data = r.json()
    except Exception as exc:
        print(f"[Audnexus] error: {exc}")
        return []

    sem = asyncio.Semaphore(10)

    async def _fetch_one(item: dict) -> dict[str, Any] | None:
        asin = item.get("asin", "")
        authors_list: list[str] = [author_name]
        async with sem:
            if asin:
                try:
                    dr = await client.get(
                        f"{AUDNEXUS_API}/books/{asin}", timeout=10
                    )
                    if dr.status_code == 200:
                        api_authors = dr.json().get("authors", [])
                        if api_authors:
                            authors_list = api_authors
                except Exception:
                    pass
        book: dict[str, Any] = {
            "title": item.get("title", ""),
            "subtitle": item.get("subtitle", ""),
            "asin": asin,
            "release_date": item.get("releaseDate", ""),
            "cover_url": item.get("image"),
            "format": "audiobook",
            "language": "en",
            "source": "Audnexus",
            "authors": authors_list,
        }
        return book if book["title"] else None

    results = await asyncio.gather(
        *[_fetch_one(item) for item in search_data.get("results", [])[:100]],
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
            print(f"[ISBNdb] HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as exc:
        print(f"[ISBNdb] error: {exc}")
        return []

    books: list[dict[str, Any]] = []
    for item in data.get("books", []):
        lang = item.get("language", "en")
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
            "https://api.audible.com/1.0/catalog/products",
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
        print(f"[Audible] metadata error for '{book_title}': {exc}")

    return None, None

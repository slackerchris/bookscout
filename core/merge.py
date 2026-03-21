"""Merge and deduplicate book records from multiple API sources."""
from __future__ import annotations

from typing import Any

from core.normalize import extract_series_from_title


def merge_books(books_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Deduplicate and merge books from multiple API result lists.

    Deduplication priority: isbn13 > isbn > asin > normalised title.
    When the same book appears in multiple sources, missing fields are filled
    in from later matches and sources are accumulated into a list.
    """
    merged: dict[str, dict[str, Any]] = {}

    for books in books_lists:
        for raw_book in books:
            book = dict(raw_book)  # shallow copy to avoid mutating originals

            # Parse series info embedded in the title
            title, series, pos = extract_series_from_title(book["title"])
            book["title"] = title
            book["series"] = book.get("series") or series
            book["series_position"] = book.get("series_position") or pos

            # Build deduplication key
            if book.get("isbn13"):
                key = f"isbn13:{book['isbn13']}"
            elif book.get("isbn"):
                key = f"isbn:{book['isbn']}"
            elif book.get("asin"):
                key = f"asin:{book['asin']}"
            else:
                key = f"title:{book['title'].lower().strip()}"

            if key not in merged:
                # Normalise source to a list immediately for consistent handling
                raw_src = book.get("source", "")
                book["source"] = [raw_src] if raw_src else []
                merged[key] = book
            else:
                existing = merged[key]

                # Fill in fields missing from the first-seen record
                for field in (
                    "subtitle", "isbn", "isbn13", "asin",
                    "cover_url", "description", "series", "series_position",
                ):
                    if not existing.get(field) and book.get(field):
                        existing[field] = book[field]

                # Accumulate sources
                new_src = book.get("source", "")
                if new_src and new_src not in existing["source"]:
                    existing["source"].append(new_src)

                # Accumulate author names
                existing_authors: set[str] = set(existing.get("authors") or [])
                for a in book.get("authors") or []:
                    existing_authors.add(a)
                existing["authors"] = list(existing_authors)

    return list(merged.values())

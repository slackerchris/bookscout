"""Merge and deduplicate book records from multiple API sources."""
from __future__ import annotations

from typing import Any

from core.normalize import extract_series_from_title, normalize_title_key


def _merge_into(existing: dict[str, Any], book: dict[str, Any]) -> None:
    """Merge *book* fields into *existing* (coalesce missing fields, accumulate sources/authors)."""
    for field in (
        "subtitle", "isbn", "isbn13", "asin",
        "cover_url", "description", "series", "series_position",
    ):
        if not existing.get(field) and book.get(field):
            existing[field] = book[field]

    new_src = book.get("source", [])
    if isinstance(new_src, str):
        new_src = [new_src] if new_src else []
    for s in new_src:
        if s and s not in existing["source"]:
            existing["source"].append(s)

    existing_authors: set[str] = set(existing.get("authors") or [])
    for a in book.get("authors") or []:
        existing_authors.add(a)
    existing["authors"] = list(existing_authors)

    # Prefer whichever record has a shorter (cleaner) title
    if len(book.get("title", "")) < len(existing.get("title", "")):
        existing["title"] = book["title"]


def merge_books(books_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Deduplicate and merge books from multiple API result lists.

    Pass 1 — identifier dedup: isbn13 > isbn > asin > normalised title.
    Pass 2 — title dedup: group identifier-distinct editions of the same book
    by normalised title and keep the best record (shortest/cleanest title,
    with fields coalesced from all editions).
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
                key = f"title:{normalize_title_key(book['title'])}"

            if key not in merged:
                # Normalise source to a list immediately for consistent handling
                raw_src = book.get("source", "")
                book["source"] = [raw_src] if raw_src else []
                merged[key] = book
            else:
                _merge_into(merged[key], book)

    # ------------------------------------------------------------------
    # Pass 2: cross-identifier title dedup
    # Different API sources often return different editions of the same book,
    # each with a unique ISBN/ASIN but an equivalent normalised title (e.g.
    # "God's Eye : Awakening", "God's Eye: Awakening: A Labyrinth World Novel",
    # "God's Eye: Awakening: A Labyrinth World LitRPG Novel").  Collapse those
    # into a single record, keeping the shortest/cleanest title.
    # ------------------------------------------------------------------
    by_title: dict[str, dict[str, Any]] = {}
    for book in merged.values():
        tkey = normalize_title_key(book["title"])
        if tkey not in by_title:
            by_title[tkey] = book
        else:
            _merge_into(by_title[tkey], book)

    return list(by_title.values())

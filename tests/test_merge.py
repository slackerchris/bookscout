"""Tests for core/merge.py — merge_books() and extract_series_from_title()."""
from __future__ import annotations

import pytest

from core.merge import merge_books
from core.normalize import extract_series_from_title


# ---------------------------------------------------------------------------
# extract_series_from_title
# ---------------------------------------------------------------------------

class TestExtractSeriesFromTitle:
    def test_parenthesized_hash(self):
        title, series, pos = extract_series_from_title("Backyard Starship (Backyard Starship #1)")
        assert series == "Backyard Starship"
        assert pos == "1"
        assert "#1" not in title

    def test_parenthesized_book_number(self):
        title, series, pos = extract_series_from_title("The Land (Chaos Seeds, Book 1)")
        assert series == "Chaos Seeds"
        assert pos == "1"

    def test_parenthesized_volume(self):
        title, series, pos = extract_series_from_title("Overlord (Overlord, Vol. 1)")
        assert series == "Overlord"
        assert pos == "1"

    def test_dash_book_pattern(self):
        title, series, pos = extract_series_from_title("Awakening (The Land - Book 1)")
        assert series == "The Land"
        assert pos == "1"

    def test_colon_book_pattern(self):
        title, series, pos = extract_series_from_title("Chaos Seeds: Book 1 - The Land")
        assert series == "Chaos Seeds"
        assert pos == "1"

    def test_trailing_book_number(self):
        title, series, pos = extract_series_from_title("Chaos Seeds Book 1")
        assert series == "Chaos Seeds"
        assert pos == "1"

    def test_trailing_hash(self):
        title, series, pos = extract_series_from_title("Chaos Seeds #3")
        assert series == "Chaos Seeds"
        assert pos == "3"

    def test_decimal_position(self):
        title, series, pos = extract_series_from_title("Side Quest (Backyard Starship #2.5)")
        assert series == "Backyard Starship"
        assert pos == "2.5"

    def test_no_series(self):
        title, series, pos = extract_series_from_title("A Standalone Novel")
        assert title == "A Standalone Novel"
        assert series is None
        assert pos is None


# ---------------------------------------------------------------------------
# merge_books
# ---------------------------------------------------------------------------

class TestMergeBooks:
    def _book(self, **overrides) -> dict:
        base = {
            "title": "Test Book",
            "authors": ["Author A"],
            "isbn": None,
            "isbn13": None,
            "asin": None,
            "source": "source1",
            "subtitle": None,
            "cover_url": None,
            "description": None,
            "series": None,
            "series_position": None,
            "narrators": [],
            "narrator": None,
        }
        base.update(overrides)
        return base

    def test_dedup_by_isbn13(self):
        books = merge_books([
            [self._book(title="Book A", isbn13="9780000000001", source="src1")],
            [self._book(title="Book A: Extended", isbn13="9780000000001", source="src2")],
        ])
        assert len(books) == 1
        assert "src1" in books[0]["source"]
        assert "src2" in books[0]["source"]

    def test_dedup_by_asin(self):
        books = merge_books([
            [self._book(title="Book A", asin="B001234567", source="src1")],
            [self._book(title="Book A", asin="B001234567", source="src2")],
        ])
        assert len(books) == 1

    def test_title_fallback_dedup(self):
        books = merge_books([
            [self._book(title="The Same Book", source="src1")],
            [self._book(title="The Same Book", source="src2")],
        ])
        assert len(books) == 1

    def test_different_books_not_merged(self):
        books = merge_books([
            [self._book(title="Book A", source="src1")],
            [self._book(title="Book B", source="src2")],
        ])
        assert len(books) == 2

    def test_coalesce_missing_fields(self):
        books = merge_books([
            [self._book(title="Book A", isbn13="9780000000001", cover_url=None, description="A desc")],
            [self._book(title="Book A", isbn13="9780000000001", cover_url="http://img.jpg", description=None)],
        ])
        assert len(books) == 1
        assert books[0]["cover_url"] == "http://img.jpg"
        assert books[0]["description"] == "A desc"

    def test_pass2_cross_identifier_title_dedup(self):
        """Different ISBNs but same normalized title → merged in pass 2."""
        books = merge_books([
            [self._book(title="God's Eye: Awakening", isbn13="9780000000001", source="src1")],
            [self._book(title="God's Eye: Awakening", isbn="0000000002", source="src2")],
        ])
        assert len(books) == 1

    def test_authors_accumulated(self):
        books = merge_books([
            [self._book(title="Collab", isbn13="978X", authors=["A"])],
            [self._book(title="Collab", isbn13="978X", authors=["B"])],
        ])
        assert len(books) == 1
        assert set(books[0]["authors"]) == {"A", "B"}

    def test_series_extracted_from_title(self):
        books = merge_books([
            [self._book(title="Backyard Starship (Backyard Starship #1)")],
        ])
        assert len(books) == 1
        assert books[0]["series"] == "Backyard Starship"
        assert books[0]["series_position"] == "1"

    def test_empty_input(self):
        assert merge_books([]) == []
        assert merge_books([[]]) == []

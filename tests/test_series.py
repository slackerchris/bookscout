"""Tests for the series grouping and gap detection."""
from __future__ import annotations

from api.v1.series import group_series, parse_position
from db.models import Book


def _book(title: str, series: str | None, pos: str | None, have_it: bool = False) -> Book:
    return Book(
        title=title,
        title_sort=title,
        series_name=series,
        series_position=pos,
        have_it=have_it,
        score=0,
        confidence_band="high",
        match_method="api",
        deleted=False,
    )


def test_parse_position_variants():
    assert parse_position("3") == 3.0
    assert parse_position("1.5") == 1.5
    assert parse_position("Book 7") == 7.0
    assert parse_position("") is None
    assert parse_position(None) is None


def test_groups_by_series_and_author_with_gap_detection():
    rows = [
        (_book("Cradle 1", "Cradle", "1", have_it=True), 1, "Will Wight"),
        (_book("Cradle 2", "Cradle", "2", have_it=True), 1, "Will Wight"),
        (_book("Cradle 4", "cradle", "4"), 1, "Will Wight"),          # case-insensitive merge
        (_book("Other Book", "Other Series", "1"), 2, "Someone Else"),
        (_book("No Series", None, None), 1, "Will Wight"),            # skipped
    ]
    series = group_series(rows)
    assert len(series) == 2

    other, cradle = series  # sorted by author name: "Someone Else" < "Will Wight"
    assert other["series_name"] == "Other Series"

    assert cradle["series_name"] == "Cradle"
    assert cradle["total"] == 3
    assert cradle["owned"] == 2
    assert [b["title"] for b in cradle["books"]] == ["Cradle 1", "Cradle 2", "Cradle 4"]
    assert cradle["unknown_gaps"] == [3]  # catalog has 1, 2, 4 — position 3 unknown


def test_same_series_name_different_authors_do_not_merge():
    rows = [
        (_book("A1", "Legacy", "1"), 1, "Author A"),
        (_book("B1", "Legacy", "1"), 2, "Author B"),
    ]
    assert len(group_series(rows)) == 2


def test_unknown_positions_sort_last():
    rows = [
        (_book("Novella", "S", None), 1, "A"),
        (_book("Book 1", "S", "1"), 1, "A"),
    ]
    series = group_series(rows)
    assert [b["title"] for b in series[0]["books"]] == ["Book 1", "Novella"]

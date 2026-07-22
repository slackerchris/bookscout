"""Tests for auto-download eligibility and best-result selection."""
from __future__ import annotations

from datetime import date

from core.auto_download import book_is_eligible, parse_release_date, select_best_result
from db.models import Book

TODAY = date(2026, 7, 21)


def _book(**overrides) -> Book:
    base = dict(
        title="Test",
        title_sort="Test",
        score=90,
        confidence_band="high",
        match_method="api",
        have_it=False,
        deleted=False,
        canonical_book_id=None,
        release_date="2026-01-01",
    )
    base.update(overrides)
    return Book(**base)


# ── release date parsing ────────────────────────────────────────────────────

def test_parse_iso_and_year_dates():
    assert parse_release_date("2026-07-21") == date(2026, 7, 21)
    assert parse_release_date("2026") == date(2026, 1, 1)
    assert parse_release_date("July 2026") is None
    assert parse_release_date(None) is None
    assert parse_release_date("2026-13-45") is None


# ── eligibility ─────────────────────────────────────────────────────────────

def test_eligible_book():
    assert book_is_eligible(_book(), TODAY) is True


def test_ineligible_variants():
    assert book_is_eligible(_book(have_it=True), TODAY) is False
    assert book_is_eligible(_book(deleted=True), TODAY) is False
    assert book_is_eligible(_book(canonical_book_id=1), TODAY) is False
    assert book_is_eligible(_book(confidence_band="medium"), TODAY) is False
    assert book_is_eligible(_book(release_date="2027-01-01"), TODAY) is False  # unreleased
    assert book_is_eligible(_book(release_date=None), TODAY) is False          # unknown date


# ── result selection ────────────────────────────────────────────────────────

def _result(**overrides) -> dict:
    base = {
        "title": "Test Book [M4B] Unabridged",
        "type": "torrent",
        "seeders": 10,
        "size": 500 * 1024**2,
        "download_url": "http://indexer/dl/1",
        "indexer": "idx",
        "source": "Prowlarr",
    }
    base.update(overrides)
    return base


def test_min_seeders_filters_torrents_but_not_nzbs():
    prefs = {"min_seeders": 5}
    assert select_best_result([_result(seeders=2)], prefs) is None
    nzb = _result(seeders=0, type="nzb")
    assert select_best_result([nzb], prefs) == nzb


def test_max_size_filter():
    prefs = {"max_size_gb": 1}
    too_big = _result(size=2 * 1024**3)
    assert select_best_result([too_big], prefs) is None
    assert select_best_result([_result()], prefs) is not None


def test_preferred_format_is_soft():
    prefs = {"preferred_format": "m4b"}
    mp3 = _result(title="Test Book MP3")
    m4b = _result(title="Test Book M4B")
    # Prefers the matching format when available…
    assert select_best_result([mp3, m4b], prefs) == m4b
    # …but falls back rather than skipping the book entirely.
    assert select_best_result([mp3], prefs) == mp3


def test_requires_a_download_url():
    assert select_best_result([_result(download_url="", url="")], {}) is None


# ── quality ranking ─────────────────────────────────────────────────────────

def test_quality_beats_raw_seeders():
    m4b = _result(title="Titan War - B.V. Larson [M4B] [128 Kbps]", seeders=2)
    mp3 = _result(title="Titan War - B.V. Larson [MP3] [32 Kbps]", seeders=5)
    assert select_best_result([mp3, m4b], {}) == m4b


def test_higher_bitrate_wins_within_format():
    hi = _result(title="Titan War [MP3] [128 Kbps]", seeders=3)
    lo = _result(title="Titan War [MP3] [32 Kbps]", seeders=3)
    assert select_best_result([lo, hi], {}) == hi


def test_wrong_book_is_discarded_when_title_known():
    wrong = _result(title="Completely Different Novel [M4B]", seeders=50)
    right = _result(title="Titan War - B.V. Larson [MP3]", seeders=1)
    best = select_best_result([wrong, right], {}, book_title="Titan War", author_name="B.V. Larson")
    assert best == right


def test_require_unabridged_excludes_abridged():
    abridged = _result(title="Titan War (Abridged) [M4B]", seeders=10)
    full = _result(title="Titan War (Unabridged) [MP3]", seeders=1)
    prefs = {"require_unabridged": True}
    assert select_best_result([abridged, full], prefs) == full
    # Without the flag, abridged still ranks below via the score penalty
    assert select_best_result([abridged, full], {}) == full


def test_nzb_gets_availability_credit():
    nzb = _result(title="Titan War [M4B]", type="nzb", seeders=0)
    torrent = _result(title="Titan War [M4B]", seeders=1)
    # NZB flat credit (+10) beats a 1-seeder torrent of equal quality
    assert select_best_result([torrent, nzb], {}) == nzb


def test_known_narrator_edition_wins():
    # Catalog knows the book's narrator (e.g. from the archived Audible
    # edition) — the release naming that narrator is the right EDITION,
    # even against a better-seeded re-record.
    original = _result(title="Titan War [M4B] read by Ray Porter", seeders=2)
    rerecord = _result(title="Titan War [M4B] narrated by Someone Else", seeders=20)
    best = select_best_result(
        [rerecord, original], {}, book_title="Titan War", narrator="Ray Porter"
    )
    assert best == original


def test_multi_narrator_field_matches_any():
    r = _result(title="Titan War [M4B] - Julia Whelan", seeders=1)
    other = _result(title="Titan War [M4B]", seeders=1)
    best = select_best_result(
        [other, r], {}, book_title="Titan War", narrator="Ray Porter, Julia Whelan"
    )
    assert best == r

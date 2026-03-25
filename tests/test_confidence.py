"""Tests for confidence.py — score_book() and score_books().

Converted from test_confidence.py (manual runner) to pytest.
Covers every scoring rule: title matching, author matching, ISBN/ASIN bonus,
publication year, multi-source, audiobook format, penalties, confidence bands,
and list sorting.
"""
import pytest
from confidence import score_book, score_books


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def book(
    title,
    authors=None,
    isbn=None,
    asin=None,
    release_date=None,
    source=None,
    format="",
):
    return {
        "title": title,
        "authors": authors or [],
        "isbn": isbn,
        "isbn13": None,
        "asin": asin,
        "release_date": release_date or "",
        "source": source or "OpenLibrary",
        "format": format,
    }


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------

class TestTitleMatching:
    def test_exact_title(self):
        r = score_book(book("The Eye of the World", ["Robert Jordan"]),
                       "The Eye of the World", "Robert Jordan")
        assert "exact_title_match" in r["reasons"]
        assert r["score"] >= 50

    def test_normalized_title_subtitle_stripped(self):
        r = score_book(book("The Eye of the World: A Novel", ["Robert Jordan"]),
                       "The Eye of the World", "Robert Jordan")
        assert "normalized_title_match" in r["reasons"]

    def test_accented_chars_normalized(self):
        r = score_book(book("Thé Eye of the Wörld", ["Robert Jordan"]),
                       "The Eye of the World", "Robert Jordan")
        assert "normalized_title_match" in r["reasons"]


# ---------------------------------------------------------------------------
# Author matching
# ---------------------------------------------------------------------------

class TestAuthorMatching:
    def test_exact_author(self):
        r = score_book(book("Dune", ["Frank Herbert"]), "Dune", "Frank Herbert")
        assert "exact_author_match" in r["reasons"]

    def test_fuzzy_author_reversed(self):
        r = score_book(book("Dune", ["Herbert, Frank"]), "Dune", "Frank Herbert")
        assert r["score"] >= 20

    def test_initial_expansion(self):
        r = score_book(book("The Way of Kings", ["Brandon Sanderson"]),
                       "The Way of Kings", "B. Sanderson")
        assert "exact_author_match" in r["reasons"]

    def test_coauthor_field(self):
        import json
        b = book("Cradle", ["Will Wight"])
        b["co_authors"] = json.dumps(["Travis Baldree"])
        r = score_book(b, "Cradle", "Travis Baldree")
        assert r["score"] > 0


# ---------------------------------------------------------------------------
# ISBN / ASIN bonus
# ---------------------------------------------------------------------------

class TestIdentifierBonus:
    def test_isbn_bonus(self):
        r = score_book(book("Mistborn", ["Brandon Sanderson"], isbn="0765311844"),
                       "Mistborn", "Brandon Sanderson")
        assert "isbn_or_asin_present" in r["reasons"]
        assert r["score"] >= 100

    def test_asin_bonus(self):
        r = score_book(book("Mistborn", ["Brandon Sanderson"], asin="B002U3CQCU"),
                       "Mistborn", "Brandon Sanderson")
        assert "isbn_or_asin_present" in r["reasons"]


# ---------------------------------------------------------------------------
# Publication year
# ---------------------------------------------------------------------------

class TestPublicationYear:
    def test_exact_year(self):
        r = score_book(book("Dune", ["Frank Herbert"], release_date="1965"),
                       "Dune", "Frank Herbert", reference_year=1965)
        assert "exact_year_match" in r["reasons"]

    def test_year_within_one(self):
        r = score_book(book("Dune", ["Frank Herbert"], release_date="1966-01-01"),
                       "Dune", "Frank Herbert", reference_year=1965)
        assert "year_close_match" in r["reasons"]

    def test_year_far_off_no_bonus(self):
        r = score_book(book("Dune", ["Frank Herbert"], release_date="1990"),
                       "Dune", "Frank Herbert", reference_year=1965)
        assert "exact_year_match" not in r["reasons"]
        assert "year_close_match" not in r["reasons"]


# ---------------------------------------------------------------------------
# Multi-source bonus
# ---------------------------------------------------------------------------

class TestMultiSourceBonus:
    def test_two_sources(self):
        r = score_book(book("Oathbringer", ["Brandon Sanderson"],
                            source=["OpenLibrary", "GoogleBooks"]),
                       "Oathbringer", "Brandon Sanderson")
        assert "found_in_2_sources" in r["reasons"]

    def test_three_sources(self):
        r = score_book(book("Oathbringer", ["Brandon Sanderson"],
                            source=["OpenLibrary", "GoogleBooks", "Audnexus"]),
                       "Oathbringer", "Brandon Sanderson")
        assert "found_in_3_sources" in r["reasons"]
        assert "found_in_2_sources" not in r["reasons"]


# ---------------------------------------------------------------------------
# Audiobook format
# ---------------------------------------------------------------------------

class TestAudiobookFormat:
    def test_audiobook_match(self):
        r = score_book(book("The Name of the Wind", ["Patrick Rothfuss"],
                            asin="B002V0KFPW", format="audiobook"),
                       "The Name of the Wind", "Patrick Rothfuss", want_audiobook=True)
        assert "audiobook_format_match" in r["reasons"]

    def test_no_audiobook_context(self):
        r = score_book(book("The Name of the Wind", ["Patrick Rothfuss"],
                            format="hardcover"),
                       "The Name of the Wind", "Patrick Rothfuss", want_audiobook=False)
        assert "audiobook_format_match" not in r["reasons"]


# ---------------------------------------------------------------------------
# Penalties
# ---------------------------------------------------------------------------

class TestPenalties:
    def test_bad_keyword_summary(self):
        r = score_book(book("Dune: A Summary and Analysis", ["Frank Herbert"]),
                       "Dune", "Frank Herbert")
        assert any("bad_keyword" in rr for rr in r["reasons"])

    def test_bad_keyword_companion(self):
        r = score_book(book("Dune: Companion Guide", ["Frank Herbert"]),
                       "Dune", "Frank Herbert")
        assert any("bad_keyword" in rr for rr in r["reasons"])

    def test_suspicious_edition_abridged(self):
        r = score_book(book("Dune: Abridged Edition", ["Frank Herbert"]),
                       "Dune", "Frank Herbert")
        assert any("suspicious_edition" in rr for rr in r["reasons"])

    def test_bad_keyword_still_applies_on_exact_match(self):
        r = score_book(book("Dune Summary", ["Frank Herbert"]),
                       "Dune Summary", "Frank Herbert")
        assert any("bad_keyword" in rr for rr in r["reasons"])


# ---------------------------------------------------------------------------
# Confidence bands
# ---------------------------------------------------------------------------

class TestConfidenceBands:
    def test_strong_book_is_high(self):
        r = score_book(
            book("Words of Radiance", ["Brandon Sanderson"],
                 isbn="0765326361", source=["OpenLibrary", "GoogleBooks"]),
            "Words of Radiance", "Brandon Sanderson", reference_year=2014,
        )
        assert r["confidence_band"] == "high"

    def test_title_only_not_high(self):
        r = score_book(book("Words of Radiance", [], source="OpenLibrary"),
                       "Words of Radiance", "Brandon Sanderson")
        assert r["confidence_band"] in ("medium", "low")

    def test_mismatch_is_low(self):
        r = score_book(book("Some Random Book", []),
                       "Words of Radiance", "Brandon Sanderson")
        assert r["confidence_band"] == "low"


# ---------------------------------------------------------------------------
# score_books — list sorting
# ---------------------------------------------------------------------------

class TestScoreBooks:
    def test_best_match_first(self):
        books = [
            book("The Final Empire", ["Brandon Sanderson"]),
            book("The Final Empire: A Summary", ["Unknown"]),
            book("The Final Empire", ["Brandon Sanderson"],
                 isbn="0765316889", source=["OpenLibrary", "GoogleBooks"]),
        ]
        scored = score_books(books, "Brandon Sanderson")
        assert scored[0]["isbn"] == "0765316889"

    def test_penalty_book_is_last(self):
        books = [
            book("The Final Empire", ["Brandon Sanderson"]),
            book("The Final Empire: A Summary", ["Unknown"]),
            book("The Final Empire", ["Brandon Sanderson"],
                 isbn="0765316889", source=["OpenLibrary", "GoogleBooks"]),
        ]
        scored = score_books(books, "Brandon Sanderson")
        assert any("bad_keyword" in rr for rr in scored[-1].get("score_reasons", []))

"""
Tests for BookScout confidence scoring engine.
Run with: python test_confidence.py
"""
import sys
import json
from confidence import score_book, score_books, _normalize, _word_overlap

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    results.append(condition)
    print(f"  [{status}] {label}" + (f"  ({detail})" if detail else ""))

def section(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")


# ---------------------------------------------------------------------------
# Helper to build a minimal book dict
# ---------------------------------------------------------------------------
def book(title, authors=None, isbn=None, asin=None, release_date=None,
         source=None, format=None, co_authors=None):
    return {
        "title": title,
        "authors": authors or [],
        "isbn": isbn,
        "isbn13": None,
        "asin": asin,
        "release_date": release_date or "",
        "source": source or "OpenLibrary",
        "format": format or "",
        "co_authors": json.dumps(co_authors) if co_authors else None,
    }


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------
section("Title Matching")

b = book("The Eye of the World", ["Robert Jordan"])
r = score_book(b, "The Eye of the World", "Robert Jordan")
check("Exact title → +50", "exact_title_match" in r["reasons"])
check("Score >= 50", r["score"] >= 50, f"score={r['score']}")

b = book("The Eye of the World: A Novel", ["Robert Jordan"])
r = score_book(b, "The Eye of the World", "Robert Jordan")
check("Normalized title (subtitle stripped) → +35", "normalized_title_match" in r["reasons"])

b = book("Thé Eye of the Wörld", ["Robert Jordan"])   # accented chars
r = score_book(b, "The Eye of the World", "Robert Jordan")
check("Accented chars normalized → +35", "normalized_title_match" in r["reasons"])


# ---------------------------------------------------------------------------
# Author matching
# ---------------------------------------------------------------------------
section("Author Matching")

b = book("Dune", ["Frank Herbert"])
r = score_book(b, "Dune", "Frank Herbert")
check("Exact author → +40", "exact_author_match" in r["reasons"])

b = book("Dune", ["Herbert, Frank"])
r = score_book(b, "Dune", "Frank Herbert")
check("Fuzzy author (reversed) → >=20 pts", r["score"] >= 20, f"score={r['score']}")

b = book("The Way of Kings", ["Brandon Sanderson"])
r = score_book(b, "The Way of Kings", "B. Sanderson")
check("Initial expansion B. Sanderson → Brandon Sanderson", "exact_author_match" in r["reasons"])

b = book("Cradle", ["Will Wight"], co_authors=["Travis Baldree"])
r = score_book(b, "Cradle", "Travis Baldree")
check("Co-author picked up from co_authors field", r["score"] > 0, f"reasons={r['reasons']}")


# ---------------------------------------------------------------------------
# ISBN / ASIN
# ---------------------------------------------------------------------------
section("ISBN / ASIN Bonus")

b = book("Mistborn", ["Brandon Sanderson"], isbn="0765311844")
r = score_book(b, "Mistborn", "Brandon Sanderson")
check("ISBN present → +100", "isbn_or_asin_present" in r["reasons"])
check("Score >= 100 (isbn alone pushes over threshold)", r["score"] >= 100, f"score={r['score']}")

b = book("Mistborn", ["Brandon Sanderson"], asin="B002U3CQCU")
r = score_book(b, "Mistborn", "Brandon Sanderson")
check("ASIN present also counts", "isbn_or_asin_present" in r["reasons"])


# ---------------------------------------------------------------------------
# Publication year
# ---------------------------------------------------------------------------
section("Publication Year")

b = book("Dune", ["Frank Herbert"], release_date="1965")
r = score_book(b, "Dune", "Frank Herbert", reference_year=1965)
check("Exact year → +15", "exact_year_match" in r["reasons"])

b = book("Dune", ["Frank Herbert"], release_date="1966-01-01")
r = score_book(b, "Dune", "Frank Herbert", reference_year=1965)
check("Year ±1 → +8", "year_close_match" in r["reasons"])

b = book("Dune", ["Frank Herbert"], release_date="1990")
r = score_book(b, "Dune", "Frank Herbert", reference_year=1965)
check("Year far off → no year bonus", "exact_year_match" not in r["reasons"] and "year_close_match" not in r["reasons"])


# ---------------------------------------------------------------------------
# Multi-source
# ---------------------------------------------------------------------------
section("Multi-Source Bonus")

b = book("Oathbringer", ["Brandon Sanderson"], source=["OpenLibrary", "GoogleBooks"])
r = score_book(b, "Oathbringer", "Brandon Sanderson")
check("2 sources → +20", "found_in_2_sources" in r["reasons"])

b = book("Oathbringer", ["Brandon Sanderson"], source=["OpenLibrary", "GoogleBooks", "Audnexus"])
r = score_book(b, "Oathbringer", "Brandon Sanderson")
check("3 sources → +35 (not +20)", "found_in_3_sources" in r["reasons"] and "found_in_2_sources" not in r["reasons"])


# ---------------------------------------------------------------------------
# Audiobook format
# ---------------------------------------------------------------------------
section("Audiobook Format")

b = book("The Name of the Wind", ["Patrick Rothfuss"], asin="B002V0KFPW", format="audiobook")
r = score_book(b, "The Name of the Wind", "Patrick Rothfuss", want_audiobook=True)
check("Audiobook format match → +20", "audiobook_format_match" in r["reasons"])

b = book("The Name of the Wind", ["Patrick Rothfuss"], format="hardcover")
r = score_book(b, "The Name of the Wind", "Patrick Rothfuss", want_audiobook=False)
check("No audiobook context → no audiobook bonus", "audiobook_format_match" not in r["reasons"])


# ---------------------------------------------------------------------------
# Penalties
# ---------------------------------------------------------------------------
section("Penalties")

b = book("Dune: A Summary and Analysis", ["Frank Herbert"])
r = score_book(b, "Dune", "Frank Herbert")
check("Bad keyword 'summary' → -60", any("bad_keyword" in rr for rr in r["reasons"]))

b = book("Dune: Companion Guide", ["Frank Herbert"])
r = score_book(b, "Dune", "Frank Herbert")
check("Bad keyword 'companion' → -60", any("bad_keyword" in rr for rr in r["reasons"]))

b = book("Dune: Abridged Edition", ["Frank Herbert"])
r = score_book(b, "Dune", "Frank Herbert")
check("Suspicious keyword 'abridged' → -25", any("suspicious_edition" in rr for rr in r["reasons"]))

b = book("Dune Summary", ["Frank Herbert"])
r = score_book(b, "Dune Summary", "Frank Herbert")
# Exact title still fires, but penalty applies — net should be positive if ISBN present
check("Bad keyword still applies even on exact title", any("bad_keyword" in rr for rr in r["reasons"]))


# ---------------------------------------------------------------------------
# Confidence bands
# ---------------------------------------------------------------------------
section("Confidence Bands")

b = book("Words of Radiance", ["Brandon Sanderson"], isbn="0765326361",
         source=["OpenLibrary", "GoogleBooks"])
r = score_book(b, "Words of Radiance", "Brandon Sanderson", reference_year=2014)
check("Strong book → HIGH band", r["confidence_band"] == "high", f"band={r['confidence_band']}, score={r['score']}")

b = book("Words of Radiance", [], source="OpenLibrary")
r = score_book(b, "Words of Radiance", "Brandon Sanderson")
check("Title only, no author/isbn → <= MEDIUM", r["confidence_band"] in ("medium", "low"), f"band={r['confidence_band']}, score={r['score']}")

b = book("Some Random Book", [])
r = score_book(b, "Words of Radiance", "Brandon Sanderson")
check("Mismatch → LOW band", r["confidence_band"] == "low", f"band={r['confidence_band']}, score={r['score']}")


# ---------------------------------------------------------------------------
# score_books (list sorting)
# ---------------------------------------------------------------------------
section("score_books() — List Sorting")

books = [
    book("The Final Empire", ["Brandon Sanderson"]),
    book("The Final Empire: A Summary", ["Unknown"]),
    book("The Final Empire", ["Brandon Sanderson"], isbn="0765316889",
         source=["OpenLibrary", "GoogleBooks"]),
]
scored = score_books(books, "Brandon Sanderson")
check("Best match is first", scored[0]["isbn"] == "0765316889")
check("Summary/penalty book is last", "bad_keyword" in " ".join(scored[-1].get("score_reasons", [])))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section("Summary")
passed = sum(results)
total = len(results)
print(f"\n  {passed}/{total} tests passed")
if passed < total:
    print("  *** Some tests failed — review output above ***")
sys.exit(0 if passed == total else 1)

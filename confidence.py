"""
BookScout Confidence Scoring Engine
====================================
Scores merged book results to surface the most reliable matches.

Score interpretation:
  HIGH   >= 100  — Strong multi-signal match, safe to trust
  MEDIUM  50-99  — Reasonable match, worth reviewing
  LOW    <  50   — Weak match, manual verification recommended

Scoring rules (v1):
  Exact title match           +50
  Normalized title match      +35
  Author exact match          +40
  Author fuzzy match          +20
  ISBN match                  +100   (any ISBN field)
  Publication year exact      +15
  Publication year ±1 year    +8
  Found in 2+ providers       +20
  Found in all 3 providers    +35
  Audiobook format match      +20    (when format context is 'audiobook')
  Bad keyword penalty         -60    (summary, workbook, companion, guide, analysis, unauthorized)
  Suspicious edition mismatch -25    (abridged, illustrated edition, etc.)
"""

import re
import unicodedata
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BAD_KEYWORDS = {
    "summary", "workbook", "companion", "analysis", "guide", "study guide",
    "unauthorized", "trivia", "quiz book", "cliff notes", "cliffsnotes",
    "sparknotes", "made easy", "review book",
}

SUSPICIOUS_EDITION_KEYWORDS = {
    "abridged", "illustrated edition", "movie tie-in", "graphic novel adaptation",
    "condensed", "large print",          # large print is fine but worth flagging
}

CONFIDENCE_BANDS = [
    (100, "high"),
    (50,  "medium"),
    (0,   "low"),
]


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace, remove punctuation."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _remove_subtitle(title: str) -> str:
    """Strip everything after the first colon or em-dash."""
    return re.split(r"[:\u2013\u2014]", title)[0].strip()


def _word_overlap(a: str, b: str) -> float:
    """Fraction of words from the shorter string that appear in the longer."""
    wa = set(_normalize(a).split())
    wb = set(_normalize(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _normalize_author(name: str) -> str:
    """Collapse initials, remove suffixes, lowercase."""
    name = _normalize(name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _author_match_score(search_author: str, book_authors: list[str]) -> tuple[int, Optional[str]]:
    """
    Return (points, reason) for the best author match found.
    """
    if not book_authors:
        return 0, None

    s_norm = _normalize_author(search_author)

    for author in book_authors:
        a_norm = _normalize_author(author)

        # Exact after normalization
        if s_norm == a_norm:
            return 40, "exact_author_match"

        # All words of search name present in book author or vice versa
        s_words = set(s_norm.split())
        a_words = set(a_norm.split())
        if s_words and a_words and (s_words.issubset(a_words) or a_words.issubset(s_words)):
            return 40, "exact_author_match"

        # Initials expansion  e.g. "j r r tolkien" matches "john ronald reuel tolkien"
        s_parts = s_norm.split()
        a_parts = a_norm.split()
        if s_parts and a_parts and s_parts[-1] == a_parts[-1]:
            initials = s_parts[:-1]
            full = a_parts[:-1]
            if initials and all(len(p) == 1 for p in initials):
                if len(initials) <= len(full) and all(
                    initials[i] == full[i][0] for i in range(len(initials))
                ):
                    return 40, "exact_author_match"

        # Fuzzy: high word overlap
        if _word_overlap(s_norm, a_norm) >= 0.6:
            return 20, "fuzzy_author_match"

    return 0, None


def _count_sources(book: dict) -> int:
    """Return the number of distinct providers that returned this book."""
    src = book.get("source", "")
    if isinstance(src, list):
        return len(src)
    if isinstance(src, str) and src.startswith("["):
        # stored as JSON-ish list string
        import json
        try:
            parsed = json.loads(src.replace("'", '"'))
            return len(parsed) if isinstance(parsed, list) else 1
        except Exception:
            pass
    return 1 if src else 0


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score_book(
    book: dict,
    search_title: str,
    search_author: str,
    reference_year: Optional[int] = None,
    want_audiobook: bool = False,
) -> dict:
    """
    Score a single merged book dict against what the user searched for.

    Parameters
    ----------
    book          : merged book dict (from merge_books)
    search_title  : the title the user/scan is looking for
    search_author : the author being scanned
    reference_year: expected publication year if known
    want_audiobook: True when the context expects audiobook format

    Returns
    -------
    dict with keys: score, confidence_band, reasons
    """
    score = 0
    reasons = []

    book_title = book.get("title", "")

    # ------------------------------------------------------------------ #
    # Title matching                                                        #
    # ------------------------------------------------------------------ #
    if search_title:
        if book_title.lower().strip() == search_title.lower().strip():
            score += 50
            reasons.append("exact_title_match")
        elif _normalize(book_title) == _normalize(search_title):
            score += 35
            reasons.append("normalized_title_match")
        else:
            # Try stripping subtitles before comparison
            bt_short = _remove_subtitle(book_title)
            st_short = _remove_subtitle(search_title)
            if _normalize(bt_short) == _normalize(st_short):
                score += 35
                reasons.append("normalized_title_match")

    # ------------------------------------------------------------------ #
    # Author matching                                                       #
    # ------------------------------------------------------------------ #
    if search_author:
        # Collect all authors for this book
        all_authors = list(book.get("authors", []))
        # co_authors may be a JSON string
        import json
        co_raw = book.get("co_authors")
        if co_raw:
            try:
                parsed_co = json.loads(co_raw) if isinstance(co_raw, str) else co_raw
                if isinstance(parsed_co, list):
                    all_authors.extend(parsed_co)
            except Exception:
                pass

        author_pts, author_reason = _author_match_score(search_author, all_authors)
        if author_pts:
            score += author_pts
            reasons.append(author_reason)

    # ------------------------------------------------------------------ #
    # ISBN / ASIN match (strong signal)                                     #
    # ------------------------------------------------------------------ #
    if book.get("isbn") or book.get("isbn13") or book.get("asin"):
        score += 100
        reasons.append("isbn_or_asin_present")

    # ------------------------------------------------------------------ #
    # Publication year                                                      #
    # ------------------------------------------------------------------ #
    if reference_year:
        raw_date = book.get("release_date", "") or ""
        m = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(raw_date))
        if m:
            book_year = int(m.group(1))
            diff = abs(book_year - reference_year)
            if diff == 0:
                score += 15
                reasons.append("exact_year_match")
            elif diff == 1:
                score += 8
                reasons.append("year_close_match")

    # ------------------------------------------------------------------ #
    # Multi-source bonus                                                    #
    # ------------------------------------------------------------------ #
    n_sources = _count_sources(book)
    if n_sources >= 3:
        score += 35
        reasons.append("found_in_3_sources")
    elif n_sources >= 2:
        score += 20
        reasons.append("found_in_2_sources")

    # ------------------------------------------------------------------ #
    # Audiobook format match                                               #
    # ------------------------------------------------------------------ #
    if want_audiobook:
        fmt = (book.get("format") or "").lower()
        if "audio" in fmt or book.get("asin"):
            score += 20
            reasons.append("audiobook_format_match")

    # ------------------------------------------------------------------ #
    # Penalties                                                            #
    # ------------------------------------------------------------------ #
    title_lower = book_title.lower()

    for kw in BAD_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", title_lower):
            score -= 60
            reasons.append(f"bad_keyword_penalty:{kw}")
            break  # one penalty per book is enough

    for kw in SUSPICIOUS_EDITION_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", title_lower):
            score -= 25
            reasons.append(f"suspicious_edition:{kw}")
            break

    # ------------------------------------------------------------------ #
    # Clamp and band                                                       #
    # ------------------------------------------------------------------ #
    score = max(score, 0)

    confidence_band = "low"
    for threshold, band in CONFIDENCE_BANDS:
        if score >= threshold:
            confidence_band = band
            break

    return {
        "score": score,
        "confidence_band": confidence_band,
        "reasons": reasons,
    }


def score_books(
    books: list[dict],
    search_author: str,
    want_audiobook: bool = False,
) -> list[dict]:
    """
    Score and sort a list of merged books.

    Each book dict is annotated in-place with:
      - score
      - confidence_band
      - score_reasons

    Returns the list sorted by score descending.
    """
    for book in books:
        result = score_book(
            book,
            search_title=book.get("title", ""),
            search_author=search_author,
            want_audiobook=want_audiobook,
        )
        book["score"] = result["score"]
        book["confidence_band"] = result["confidence_band"]
        book["score_reasons"] = result["reasons"]

    books.sort(key=lambda b: b.get("score", 0), reverse=True)
    return books

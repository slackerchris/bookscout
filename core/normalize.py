"""Author/book name normalisation and fuzzy matching helpers."""
from __future__ import annotations

import re

_VOWELS = frozenset("aeiou")

_PAREN_RE = re.compile(r"\s*\([^)]*\)")
_ARTICLE_PREFIX_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def normalize_title_key(title: str) -> str:
    """Return a normalised dedup key for *title*.

    Strips leading articles, parenthetical content (e.g. ``"(Chaos Seeds)"``),
    and text after a second colon (verbose subtitles added by some APIs), then
    strips all remaining punctuation and collapses whitespace.

    This lets ``"The Land: Founding: A LitRPG Saga (Chaos Seeds) (Volume 1)"``
    and ``"Land: Founding"`` produce the same key.
    """
    t = _PAREN_RE.sub("", title)
    t = re.sub(r"\s*:\s*", ":", t)          # normalise spaces around colons
    parts = t.split(":", 2)
    t = ":".join(parts[:2])                  # keep at most main title + first subtitle
    t = _ARTICLE_PREFIX_RE.sub("", t)
    t = re.sub(r"[^a-z0-9\s]", "", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def abs_search_title(title: str) -> str:
    """Return a simplified title suitable as an ABS full-text search query.

    Strips parenthetical content and text after a second colon so that verbose
    metadata API titles like ``"The Land: Founding: A LitRPG Saga (Chaos Seeds)
    (Volume 1)"`` are shortened to ``"The Land: Founding"`` before querying ABS.
    """
    t = _PAREN_RE.sub("", title).strip()
    t = re.sub(r"\s*:\s*", ": ", t)
    parts = t.split(": ", 2)
    return ": ".join(parts[:2]).strip()


def normalize_author_name(name: str) -> str:
    """Lower-case, remove periods, collapse whitespace, strip common suffixes."""
    n = name.lower().strip()
    # Ensure run-together initials like "D.E." are spaced out before stripping
    # periods, so "D.E. Sherman" and "D. E. Sherman" normalise identically.
    n = re.sub(r"(?<=[a-z])\.(?=[a-z])", " ", n)
    n = re.sub(r"\.", "", n)
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"\s+(jr|sr|ii|iii|iv)$", "", n)
    return n.strip()


def _expand_initials(tokens: list[str]) -> list[str]:
    """Expand combined initials in non-last tokens.

    Tokens of 2–3 all-consonant characters before the last token are almost
    certainly run-together initials (e.g. ``"jn"`` from ``"J.N."``).
    Expanding them to individual characters lets the initials logic below
    handle variations like ``"J.N. Chaney"`` ↔ ``"J. N. Chaney"`` ↔
    ``"John N. Chaney"``.
    """
    if len(tokens) < 2:
        return tokens
    expanded: list[str] = []
    for i, tok in enumerate(tokens):
        is_last = i == len(tokens) - 1
        if not is_last and 2 <= len(tok) <= 3 and all(c not in _VOWELS for c in tok):
            expanded.extend(list(tok))  # "jn" → ["j", "n"]
        else:
            expanded.append(tok)
    return expanded


def author_names_match(search_name: str, book_author: str) -> bool:
    """Return True if the two author names refer to the same person.

    Handles:
    - Exact match after normalisation
    - One name being a subset of the other
    - Abbreviated first names / initials (e.g. ``"J N Chaney"`` ↔ ``"Jason N. Chaney"``)
    - Combined initials (e.g. ``"J.N. Chaney"`` ↔ ``"J. N. Chaney"`` ↔ ``"John N. Chaney"``)
    """
    s = normalize_author_name(search_name)
    b = normalize_author_name(book_author)

    if s == b:
        return True

    sw, bw = set(s.split()), set(b.split())
    if sw.issubset(bw) or bw.issubset(sw):
        return True

    # Expand combined initials before the last-name token, then retry
    sp = _expand_initials(s.split())
    bp = _expand_initials(b.split())

    se = " ".join(sp)
    be = " ".join(bp)
    if se == be:
        return True

    sew, bew = set(sp), set(bp)
    if sew.issubset(bew) or bew.issubset(sew):
        return True

    # search uses initials → match against book's full first names
    if sp and bp and all(len(p) == 1 for p in sp[:-1]):
        if sp[-1] == bp[-1] and len(sp) - 1 <= len(bp) - 1:
            if all(sp[i] == bp[i][0] for i in range(len(sp) - 1)):
                return True

    # book uses initials → match against search's full first names
    if sp and bp and all(len(p) == 1 for p in bp[:-1]):
        if bp[-1] == sp[-1] and len(bp) - 1 <= len(sp) - 1:
            if all(bp[i] == sp[i][0] for i in range(len(bp) - 1)):
                return True

    return False


_SERIES_PATTERNS = [
    re.compile(r"\(([^)]+?)\s*#(\d+(?:\.\d+)?)\)", re.IGNORECASE),
    re.compile(r"\(([^)]+?),?\s*Book\s+(\d+(?:\.\d+)?)\)", re.IGNORECASE),
    re.compile(r"\(([^)]+?),?\s*Vol\.?\s+(\d+(?:\.\d+)?)\)", re.IGNORECASE),
    re.compile(r"\(([^)]+?)\s*-\s*Book\s+(\d+(?:\.\d+)?)\)", re.IGNORECASE),
    re.compile(r"^(.+?):\s*Book\s+(\d+(?:\.\d+)?)\s*[-:]", re.IGNORECASE),
    re.compile(r"^(.+?)\s*#(\d+(?:\.\d+)?)\s*[-:]", re.IGNORECASE),
    re.compile(r"(.+?)\s+Book\s+(\d+(?:\.\d+)?)$", re.IGNORECASE),
    re.compile(r"(.+?)\s+#(\d+(?:\.\d+)?)$", re.IGNORECASE),
]

_SERIES_CLEAN_RE = re.compile(r"^[-:\s]+|[-:\s]+$")


def extract_series_from_title(title: str) -> tuple[str, str | None, str | None]:
    """Parse a book title and return ``(clean_title, series_name, position)``.

    Returns the original title unchanged when no series pattern is detected.
    """
    for compiled in _SERIES_PATTERNS:
        m = compiled.search(title)
        if m:
            series = m.group(1).strip()
            position = m.group(2)
            clean = compiled.sub("", title).strip()
            clean = _SERIES_CLEAN_RE.sub("", clean).strip()
            if len(clean) < 3:
                clean = title
            return clean, series, position

    return title, None, None


def sort_name(name: str) -> str:
    """Return a sort-friendly ``"Last, First"`` form of *name*."""
    parts = name.strip().rsplit(" ", 1)
    return f"{parts[1]}, {parts[0]}" if len(parts) == 2 else name


def sort_title(title: str) -> str:
    """Strip leading articles for alphabetic sorting."""
    for article in ("The ", "A ", "An "):
        if title.startswith(article):
            return title[len(article):] + ", " + article.strip()
    return title


def normalize_author_key(name: str) -> str:
    """Strip all non-alphanumeric characters and lowercase.

    Used to populate ``Author.name_normalized`` and for indexed SQL lookups in
    ``_get_or_create_author``.  Handles punctuation/spacing variants:
    ``"J.N. Chaney"``, ``"J. N. Chaney"`` and ``"JN Chaney"`` all map to
    ``"jnchaney"``.

    Note — this does *not* handle initial expansion (``"J.N."`` ↔ ``"John N."``);
    see TODO v0.51.0 for the pg_trgm fallback that covers that case.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())

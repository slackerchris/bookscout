"""Author/book name normalisation and fuzzy matching helpers."""
from __future__ import annotations

import re

_VOWELS = frozenset("aeiou")


def normalize_author_name(name: str) -> str:
    """Lower-case, remove periods, collapse whitespace, strip common suffixes."""
    n = name.lower().strip()
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


def extract_series_from_title(title: str) -> tuple[str, str | None, str | None]:
    """Parse a book title and return ``(clean_title, series_name, position)``.

    Returns the original title unchanged when no series pattern is detected.
    """
    patterns = [
        r"\(([^)]+?)\s*#(\d+(?:\.\d+)?)\)",
        r"\(([^)]+?),?\s*Book\s+(\d+(?:\.\d+)?)\)",
        r"\(([^)]+?),?\s*Vol\.?\s+(\d+(?:\.\d+)?)\)",
        r"\(([^)]+?)\s*-\s*Book\s+(\d+(?:\.\d+)?)\)",
        r"^(.+?):\s*Book\s+(\d+(?:\.\d+)?)\s*[-:]",
        r"^(.+?)\s*#(\d+(?:\.\d+)?)\s*[-:]",
        r"(.+?)\s+Book\s+(\d+(?:\.\d+)?)$",
        r"(.+?)\s+#(\d+(?:\.\d+)?)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, title, re.IGNORECASE)
        if m:
            series = m.group(1).strip()
            position = m.group(2)
            clean = re.sub(pattern, "", title, flags=re.IGNORECASE).strip()
            clean = re.sub(r"^[-:\s]+|[-:\s]+$", "", clean).strip()
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

"""Filesystem scanner for local audiobook libraries (v0.37.0).

Walks configured ``LibraryPath`` directories, parses author + title from path
structure, and matches found audio files against known books in the database.
Matched books are updated with ``have_it=True`` and ``match_method='filesystem'``.

Supported directory structures
--------------------------------
  <root>/<Author>/<Title>/parts...       — ABS default (book-per-folder)
  <root>/<Author>/<Series>/<Title>/...   — ABS with series sub-folder
  <root>/<Author>/<Title>.m4b            — single-file audiobook
  <root>/<Author> - <Title>.m4b          — flat with dash separator
  <root>/<Author - Title>.m4b            — dash in filename, no author folder
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.normalize import author_names_match
from db.models import Author, Book, BookAuthor, LibraryPath

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".m4b", ".mp3", ".flac", ".opus", ".aac", ".ogg", ".wma", ".m4a"}
)

_DASH_SPLIT_RE = re.compile(r"\s+-\s+")


def _title_similarity(a: str, b: str) -> float:
    """Word-overlap ratio between two title strings (case-insensitive)."""
    wa = set(re.sub(r"[^a-z0-9\s]", "", a.lower()).split())
    wb = set(re.sub(r"[^a-z0-9\s]", "", b.lower()).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def _parse_audio_entries(root: Path) -> dict[Path, tuple[str | None, str | None]]:
    """Walk *root* and return a mapping of book directories → (author, title).

    Each audiobook is identified by the directory that directly contains its
    audio files.  Single-file books are also supported.  Multi-part books
    (multiple files in the same folder) are processed once per directory.
    """
    entries: dict[Path, tuple[str | None, str | None]] = {}

    for file in root.rglob("*"):
        if file.suffix.lower() not in AUDIO_EXTENSIONS:
            continue

        # One entry per parent directory — avoid processing parts multiple times
        book_dir = file.parent
        if book_dir in entries:
            continue

        rel = file.relative_to(root)
        parts = rel.parts

        author: str | None = None
        title: str | None = None

        if len(parts) >= 3:
            # root/Author/.../Title/file.ext
            # Author is always parts[0]; Title is the immediate parent of the file
            author = parts[0]
            title = parts[-2]

        elif len(parts) == 2:
            # root/Author/Title.m4b  OR  root/Author/Title/ (single file inside)
            author = parts[0]
            raw = Path(parts[1]).stem  # strip extension from filename
            if _DASH_SPLIT_RE.search(raw):
                # "Author Name - Book Title" filename pattern
                dash_parts = _DASH_SPLIT_RE.split(raw, maxsplit=1)
                if author_names_match(author, dash_parts[0]):
                    title = dash_parts[1]
                else:
                    title = raw
            else:
                title = raw

        elif len(parts) == 1:
            # Flat file at root level — only useful if name contains " - "
            raw = Path(parts[0]).stem
            if _DASH_SPLIT_RE.search(raw):
                dash_parts = _DASH_SPLIT_RE.split(raw, maxsplit=1)
                author = dash_parts[0]
                title = dash_parts[1]
            # else: can't determine author — skip

        entries[book_dir] = (author, title)

    return entries


async def scan_library_path(
    session: AsyncSession,
    library_path_id: int,
) -> dict[str, Any]:
    """Scan a single library path and match found audio files to DB books.

    Loads all known (book_id, title, author_name) records once, then walks the
    filesystem and compares using ``author_names_match`` + word-overlap title
    similarity.  Books scoring ≥ 0.75 similarity are marked as owned.

    Returns::

        {
            "path": str,
            "files_found": int,
            "matched": int,
            "unmatched": int,
        }
    """
    result = await session.execute(
        select(LibraryPath).where(LibraryPath.id == library_path_id)
    )
    lp: LibraryPath | None = result.scalar_one_or_none()
    if not lp:
        raise ValueError(f"LibraryPath {library_path_id} not found")

    root = Path(lp.path)
    if not root.exists():
        raise FileNotFoundError(f"Library path does not exist: {root}")

    # Load every (book_id, title, author_name) tuple in one shot
    rows = await session.execute(
        select(Book.id, Book.title, Author.name)
        .join(BookAuthor, BookAuthor.book_id == Book.id)
        .join(Author, Author.id == BookAuthor.author_id)
        .where(BookAuthor.role == "author", Book.deleted.is_(False))
    )
    db_books: list[tuple[int, str, str]] = rows.all()  # (id, title, author_name)

    entries = _parse_audio_entries(root)
    matched = 0
    unmatched = 0

    for book_dir, (fs_author, fs_title) in entries.items():
        if not fs_author or not fs_title:
            unmatched += 1
            continue

        best_book_id: int | None = None
        best_score = 0.0

        for book_id, db_title, db_author in db_books:
            if not author_names_match(fs_author, db_author):
                continue
            sim = _title_similarity(fs_title, db_title)
            if sim > best_score:
                best_score = sim
                best_book_id = book_id

        if best_book_id is not None and best_score >= 0.75:
            book_result = await session.execute(
                select(Book).where(Book.id == best_book_id)
            )
            book = book_result.scalar_one_or_none()
            if book:
                book.have_it = True
                book.match_method = "filesystem"
                book.file_path = str(book_dir)
                book.updated_at = datetime.now(timezone.utc)
                matched += 1
        else:
            unmatched += 1

    lp.last_scanned = datetime.now(timezone.utc)
    await session.commit()

    return {
        "path": str(root),
        "files_found": len(entries),
        "matched": matched,
        "unmatched": unmatched,
    }

"""Post-download importer: extract archives and organise audiobooks.

Given a source path (file or directory) and a book record, this module:
  1. Extracts any archive (zip / rar / 7z) it finds.
  2. Collects all audiobook files from the result.
  3. Builds a destination path:  <library_root>/<author>/<series>/<title>/
     (series segment is omitted when the book has no series_name).
  4. Moves all files to the destination, creating directories as needed.
  5. Returns a result dict describing what was moved.

External dependencies
---------------------
- ``rarfile`` — pip install rarfile + unrar/unar on PATH  (optional; zip/7z work without it)
- ``py7zr``   — pip install py7zr  (optional; zip/rar work without it)

If a dependency is absent the corresponding archive type is skipped and the
file is left in place — a warning is logged so the user can install the
missing library.
"""
from __future__ import annotations

import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".m4b", ".mp3", ".flac", ".opus", ".aac", ".ogg", ".wma", ".m4a"}
)
ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".zip", ".rar", ".7z"})

# Characters that are unsafe in filesystem paths on Linux/Windows
_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Collapse runs of whitespace / dots at the edges
_EDGE_RE = re.compile(r"^[\s.]+|[\s.]+$")


def _sanitise(name: str, max_len: int = 120) -> str:
    """Replace unsafe characters and trim the name to a safe filesystem component."""
    name = _UNSAFE_RE.sub("_", name)
    name = _EDGE_RE.sub("", name)
    return name[:max_len] or "_"


def _build_dest(library_root: Path, author: str, series: str | None, title: str) -> Path:
    """Return the target directory for this book.

    Structure: <library_root>/<Author>/<Series>/<Title>/
    The series segment is omitted when *series* is empty/None.
    """
    parts = [library_root, Path(_sanitise(author))]
    if series:
        parts.append(Path(_sanitise(series)))
    parts.append(Path(_sanitise(title)))
    dest = Path(*parts)
    return dest


# ---------------------------------------------------------------------------
# Archive extraction
# ---------------------------------------------------------------------------

def _extract_zip(archive: Path, dest_dir: Path) -> bool:
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest_dir)
        return True
    except Exception as exc:
        logger.error("zip extraction failed", extra={"path": str(archive), "error": str(exc)})
        return False


def _extract_rar(archive: Path, dest_dir: Path) -> bool:
    try:
        import rarfile  # type: ignore[import]
    except ImportError:
        logger.warning(
            "rarfile not installed — cannot extract .rar; run: pip install rarfile",
            extra={"path": str(archive)},
        )
        return False
    try:
        with rarfile.RarFile(archive) as rf:
            rf.extractall(dest_dir)
        return True
    except Exception as exc:
        logger.error("rar extraction failed", extra={"path": str(archive), "error": str(exc)})
        return False


def _extract_7z(archive: Path, dest_dir: Path) -> bool:
    try:
        import py7zr  # type: ignore[import]
    except ImportError:
        logger.warning(
            "py7zr not installed — cannot extract .7z; run: pip install py7zr",
            extra={"path": str(archive)},
        )
        return False
    try:
        with py7zr.SevenZipFile(archive, mode="r") as sz:
            sz.extractall(path=dest_dir)
        return True
    except Exception as exc:
        logger.error("7z extraction failed", extra={"path": str(archive), "error": str(exc)})
        return False


def _extract_archive(archive: Path, work_dir: Path) -> Path:
    """Extract *archive* into *work_dir* and return the extraction directory.

    A sub-directory named after the archive stem is created so multiple
    archives in the same work-dir don't collide.
    """
    extract_to = work_dir / archive.stem
    extract_to.mkdir(parents=True, exist_ok=True)

    suffix = archive.suffix.lower()
    if suffix == ".zip":
        _extract_zip(archive, extract_to)
    elif suffix == ".rar":
        _extract_rar(archive, extract_to)
    elif suffix == ".7z":
        _extract_7z(archive, extract_to)

    return extract_to


def _collect_audio_files(root: Path) -> list[Path]:
    """Recursively find all audiobook files under *root*."""
    return [p for p in root.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS]


def _collect_archives(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.suffix.lower() in ARCHIVE_EXTENSIONS]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def import_download(
    source: str | Path,
    library_root: str | Path,
    author: str,
    title: str,
    series: str | None = None,
) -> dict[str, Any]:
    """Extract (if needed) and organise an audiobook download.

    Parameters
    ----------
    source:
        Path to the downloaded file or directory (from the download client's
        save path / completed torrent folder).
    library_root:
        Root of the organised audiobook library (``postprocess.library_root``
        in config.yaml).
    author:
        Primary author name (taken from the BookScout book record).
    title:
        Book title (taken from the book record).
    series:
        Series name, or None.  When provided the destination becomes
        ``<library_root>/<author>/<series>/<title>/``.

    Returns
    -------
    dict with keys:
        ``destination``  — final directory the files landed in
        ``files_moved``  — list of filenames moved
        ``extracted``    — True if at least one archive was unpacked
        ``skipped``      — files that already existed at destination
        ``errors``       — list of error strings (non-fatal)
    """
    source = Path(source)
    library_root = Path(library_root)

    if not source.exists():
        return {
            "destination": None,
            "files_moved": [],
            "extracted": False,
            "skipped": [],
            "errors": [f"Source path does not exist: {source}"],
        }

    work_dirs: list[Path] = []
    extracted = False
    errors: list[str] = []

    # ── Step 1: gather candidate roots ──────────────────────────────────────
    if source.is_file():
        if source.suffix.lower() in ARCHIVE_EXTENSIONS:
            extract_dir = _extract_archive(source, source.parent / "_bookscout_work")
            work_dirs.append(extract_dir)
            extracted = True
        else:
            # Single audio file — treat its parent as the work dir
            work_dirs.append(source.parent)
    else:
        work_dirs.append(source)

    # ── Step 2: check for nested archives inside directories ─────────────────
    for wd in list(work_dirs):
        for archive in _collect_archives(wd):
            extract_dir = _extract_archive(archive, archive.parent / "_bookscout_work")
            work_dirs.append(extract_dir)
            extracted = True

    # ── Step 3: collect all audio files from all work dirs ──────────────────
    audio_files: list[Path] = []
    for wd in work_dirs:
        audio_files.extend(_collect_audio_files(wd))

    # Deduplicate by name (in case the same stem appears in multiple dirs)
    seen: set[str] = set()
    unique_audio: list[Path] = []
    for af in audio_files:
        if af.name not in seen:
            seen.add(af.name)
            unique_audio.append(af)

    if not unique_audio:
        # Nothing to move — could be download still in progress or wrong path
        return {
            "destination": None,
            "files_moved": [],
            "extracted": extracted,
            "skipped": [],
            "errors": [f"No audiobook files found under: {source}"],
        }

    # ── Step 4: build destination path ──────────────────────────────────────
    dest = _build_dest(library_root, author, series, title)
    dest.mkdir(parents=True, exist_ok=True)

    # ── Step 5: move files ──────────────────────────────────────────────────
    files_moved: list[str] = []
    skipped: list[str] = []

    for af in unique_audio:
        target = dest / af.name
        if target.exists():
            skipped.append(af.name)
            logger.info(
                "import: file already exists at destination — skipping",
                extra={"file": af.name, "dest": str(dest)},
            )
            continue
        try:
            shutil.move(str(af), str(target))
            files_moved.append(af.name)
            logger.info(
                "import: moved file",
                extra={"file": af.name, "dest": str(dest)},
            )
        except Exception as exc:
            err = f"Failed to move {af.name}: {exc}"
            errors.append(err)
            logger.error("import: move failed", extra={"file": af.name, "error": str(exc)})

    # ── Step 6: clean up empty work dirs ────────────────────────────────────
    for wd in work_dirs:
        work_root = wd.parent if wd.name == "_bookscout_work" else wd / "_bookscout_work"
        if work_root.exists():
            try:
                shutil.rmtree(work_root, ignore_errors=True)
            except Exception:
                pass

    logger.info(
        "import: complete",
        extra={
            "author": author,
            "title": title,
            "series": series,
            "destination": str(dest),
            "moved": len(files_moved),
            "skipped": len(skipped),
        },
    )

    return {
        "destination": str(dest),
        "files_moved": files_moved,
        "extracted": extracted,
        "skipped": skipped,
        "errors": errors,
    }

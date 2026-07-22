"""Post-download importer: extract archives and organise audiobooks.

Given a source path (file or directory) and a book record, this module:
  1. Extracts any archive (zip / rar / 7z) it finds.
  2. Collects all audiobook files from the result.
  3. Builds a destination path:  <library_root>/<author>/<series>/<title>/
     (series segment is omitted when the book has no series_name).
  4. Copies all files to the destination, creating directories as needed.
     The originals are left intact so torrents continue seeding.
  5. Returns a result dict describing what was copied.

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
from collections import Counter
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

def _natural_key(path: Path) -> tuple:
    """Sort key with numeric awareness so 'Track 2' orders before 'Track 10'."""
    parts = re.split(r"(\d+)", str(path).lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def import_download(
    source: str | Path,
    library_root: str | Path,
    author: str,
    title: str,
    series: str | None = None,
    rename_files: bool = True,
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
        ``files_copied``  — list of filenames copied
        ``extracted``    — True if at least one archive was unpacked
        ``skipped``      — files that already existed at destination
        ``errors``       — list of error strings (non-fatal)
    """
    source = Path(source)
    library_root = Path(library_root)

    if not source.exists():
        return {
            "destination": None,
            "files_copied": [],
            "extracted": False,
            "skipped": [],
            "errors": [f"Source path does not exist: {source}"],
        }

    work_dirs: list[Path] = []
    # Track the _bookscout_work parent dirs created during extraction so
    # cleanup can remove the right directories (not the archive stem subdirs).
    extraction_roots: set[Path] = set()
    extracted = False
    errors: list[str] = []

    # ── Step 1: gather candidate roots ──────────────────────────────────────
    audio_files: list[Path] = []

    if source.is_file():
        if source.suffix.lower() in ARCHIVE_EXTENSIONS:
            work_root = source.parent / "_bookscout_work"
            extract_dir = _extract_archive(source, work_root)
            work_dirs.append(extract_dir)
            extraction_roots.add(work_root)
            extracted = True
        elif source.suffix.lower() in AUDIO_EXTENSIONS:
            # Single audio file — copy it directly; do NOT scan the parent
            # directory (which may contain unrelated torrents).
            audio_files.append(source)
        else:
            errors.append(f"Unrecognised file type: {source.suffix}")
    else:
        work_dirs.append(source)

    # ── Step 2: check for nested archives inside directories ─────────────────
    for wd in list(work_dirs):
        for archive in _collect_archives(wd):
            work_root = archive.parent / "_bookscout_work"
            extract_dir = _extract_archive(archive, work_root)
            work_dirs.append(extract_dir)
            extraction_roots.add(work_root)
            extracted = True

    # ── Step 3: collect all audio files from directories ────────────────────
    for wd in work_dirs:
        audio_files.extend(_collect_audio_files(wd))

    # Deduplicate by resolved path only — the same file can be collected twice
    # via overlapping work dirs, but same-NAMED files in different directories
    # (CD1/Track01.mp3, CD2/Track01.mp3 …) are distinct parts of the book and
    # must all be kept.
    seen: set[Path] = set()
    unique_audio: list[Path] = []
    for af in audio_files:
        resolved = af.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_audio.append(af)

    if not unique_audio:
        # Nothing to copy — could be download still in progress or wrong path
        return {
            "destination": None,
            "files_copied": [],
            "extracted": extracted,
            "skipped": [],
            "errors": [f"No audiobook files found under: {source}"],
        }

    # ── Step 4: build destination path ──────────────────────────────────────
    dest = _build_dest(library_root, author, series, title)
    dest.mkdir(parents=True, exist_ok=True)

    # ── Step 5: copy files ──────────────────────────────────────────────────
    files_copied: list[str] = []
    skipped: list[str] = []

    if rename_files:
        # Clean library names: "<Title>.ext" for a single file,
        # "<Title> - Part NN.ext" for multi-file books, ordered naturally
        # (Track 2 before Track 10, CD1 before CD2).
        ordered = sorted(unique_audio, key=_natural_key)
        safe_title = _sanitise(title)
        width = max(2, len(str(len(ordered))))
        if len(ordered) == 1:
            plan = [(ordered[0], f"{safe_title}{ordered[0].suffix.lower()}")]
        else:
            plan = [
                (af, f"{safe_title} - Part {idx:0{width}d}{af.suffix.lower()}")
                for idx, af in enumerate(ordered, 1)
            ]
    else:
        # Keep original release filenames; same-named files from different
        # source dirs (multi-disc layouts) get a parent-dir prefix.
        name_counts = Counter(af.name for af in unique_audio)
        plan = [
            (
                af,
                af.name if name_counts[af.name] == 1 else f"{af.parent.name} - {af.name}",
            )
            for af in unique_audio
        ]

    for af, target_name in plan:
        target = dest / target_name
        if target.exists():
            skipped.append(target_name)
            logger.info(
                "import: file already exists at destination — skipping",
                extra={"file": target_name, "dest": str(dest)},
            )
            continue
        try:
            shutil.copy2(str(af), str(target))
            files_copied.append(target_name)
            logger.info(
                "import: copied file",
                extra={"file": target_name, "dest": str(dest)},
            )
        except Exception as exc:
            err = f"Failed to copy {af.name}: {exc}"
            errors.append(err)
            logger.error("import: copy failed", extra={"file": af.name, "error": str(exc)})

    # ── Step 6: clean up extraction temp dirs ───────────────────────────────
    for work_root in extraction_roots:
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
            "copied": len(files_copied),
            "skipped": len(skipped),
        },
    )

    return {
        "destination": str(dest),
        "files_copied": files_copied,
        "extracted": extracted,
        "skipped": skipped,
        "errors": errors,
    }

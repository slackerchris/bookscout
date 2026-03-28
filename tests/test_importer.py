"""Tests for core/importer.py.

Tests are purely filesystem-based (no DB, no network).  Each test gets a
temporary directory via pytest's ``tmp_path`` fixture.

Covers:
  - Path sanitisation (_sanitise)
  - Destination path construction (_build_dest) — with/without series
  - Audio file collection (_collect_audio_files)
  - Archive collection (_collect_archives)
  - Zip extraction (_extract_zip)
  - Full import_download happy path (directory of audio files)
  - Full import_download with zip archive
  - Missing source path returns error result
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from core.importer import (
    _build_dest,
    _collect_archives,
    _collect_audio_files,
    _extract_zip,
    _sanitise,
    import_download,
)


# ---------------------------------------------------------------------------
# _sanitise
# ---------------------------------------------------------------------------

class TestSanitise:
    def test_removes_unsafe_chars(self):
        assert _sanitise('My:Book/Title"') == "My_Book_Title_"

    def test_trims_leading_trailing_dots(self):
        assert _sanitise("...Book...") == "Book"

    def test_max_len_truncation(self):
        long = "A" * 200
        result = _sanitise(long)
        assert len(result) == 120

    def test_empty_string_returns_placeholder(self):
        result = _sanitise("")
        assert result == "_"

    def test_normal_name_unchanged(self):
        assert _sanitise("Brandon Sanderson") == "Brandon Sanderson"


# ---------------------------------------------------------------------------
# _build_dest
# ---------------------------------------------------------------------------

class TestBuildDest:
    def test_with_series(self, tmp_path):
        dest = _build_dest(tmp_path, "J.N. Chaney", "Renegade Star", "Renegade Star #1")
        assert dest == tmp_path / "J.N. Chaney" / "Renegade Star" / "Renegade Star #1"

    def test_without_series(self, tmp_path):
        dest = _build_dest(tmp_path, "Frank Herbert", None, "Dune")
        assert dest == tmp_path / "Frank Herbert" / "Dune"

    def test_sanitises_author(self, tmp_path):
        dest = _build_dest(tmp_path, "Author: Bad/Name", None, "Title")
        assert "Author_ Bad_Name" in str(dest)


# ---------------------------------------------------------------------------
# _collect_audio_files / _collect_archives
# ---------------------------------------------------------------------------

class TestCollect:
    def test_collects_audio_extensions(self, tmp_path):
        for ext in (".m4b", ".mp3", ".flac", ".opus"):
            (tmp_path / f"file{ext}").touch()
        (tmp_path / "cover.jpg").touch()  # should be ignored
        audio = _collect_audio_files(tmp_path)
        assert len(audio) == 4
        assert all(f.suffix in {".m4b", ".mp3", ".flac", ".opus"} for f in audio)

    def test_collects_archives(self, tmp_path):
        (tmp_path / "book.zip").touch()
        (tmp_path / "book.rar").touch()
        (tmp_path / "book.m4b").touch()
        archives = _collect_archives(tmp_path)
        assert len(archives) == 2

    def test_recursive_collection(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "chapter1.mp3").touch()
        (sub / "chapter2.mp3").touch()
        assert len(_collect_audio_files(tmp_path)) == 2


# ---------------------------------------------------------------------------
# _extract_zip
# ---------------------------------------------------------------------------

class TestExtractZip:
    def test_extracts_contents(self, tmp_path):
        # Create a zip with one audio file
        zip_path = tmp_path / "book.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("chapter1.mp3", b"fake audio data")
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        success = _extract_zip(zip_path, extract_dir)
        assert success is True
        assert (extract_dir / "chapter1.mp3").exists()

    def test_returns_false_on_bad_zip(self, tmp_path):
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"not a zip file")
        extract_dir = tmp_path / "out"
        extract_dir.mkdir()
        success = _extract_zip(bad_zip, extract_dir)
        assert success is False


# ---------------------------------------------------------------------------
# import_download — full pipeline
# ---------------------------------------------------------------------------

class TestImportDownload:
    def test_directory_of_audio_files(self, tmp_path):
        src = tmp_path / "download"
        src.mkdir()
        (src / "chapter1.m4b").write_bytes(b"audio")
        (src / "chapter2.m4b").write_bytes(b"audio")
        lib = tmp_path / "library"

        result = import_download(src, lib, "J.N. Chaney", "Renegade Star #1",
                                 series="Renegade Star")
        assert result["errors"] == []
        assert len(result["files_copied"]) == 2
        assert result["extracted"] is False
        dest = Path(result["destination"])
        assert dest.exists()
        assert (dest / "chapter1.m4b").exists()

    def test_zip_archive_extracted_and_moved(self, tmp_path):
        zip_path = tmp_path / "book.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("audio.mp3", b"fake audio")
        lib = tmp_path / "library"

        result = import_download(zip_path, lib, "Frank Herbert", "Dune")
        assert result["errors"] == []
        assert result["extracted"] is True
        assert "audio.mp3" in result["files_copied"]

    def test_missing_source_returns_error(self, tmp_path):
        result = import_download(
            tmp_path / "nonexistent",
            tmp_path / "library",
            "Author",
            "Title",
        )
        assert result["files_copied"] == []
        assert any("does not exist" in e for e in result["errors"])

    def test_no_audio_files_returns_empty(self, tmp_path):
        src = tmp_path / "download"
        src.mkdir()
        (src / "readme.txt").write_text("not audio")
        lib = tmp_path / "library"

        result = import_download(src, lib, "Author", "Title")
        assert result["files_copied"] == []
        assert any("No audiobook files found" in e for e in result["errors"])

    def test_destination_without_series(self, tmp_path):
        src = tmp_path / "download"
        src.mkdir()
        (src / "book.mp3").write_bytes(b"audio")
        lib = tmp_path / "library"

        result = import_download(src, lib, "Frank Herbert", "Dune")
        dest = Path(result["destination"])
        # Series segment must be absent
        assert "Frank Herbert" in str(dest)
        assert dest.parent.name == "Frank Herbert"

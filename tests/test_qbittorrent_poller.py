"""Tests for the qBittorrent auto-import candidate selection.

Mirrors the semantics of the retired n8n poller workflow: only torrents
tagged ``bookscout-<id>`` are candidates, and the ``bs-imported`` /
``bs-failed`` marker tags make processing exactly-once.
"""
from __future__ import annotations

from core.qbittorrent import select_import_candidates


def _torrent(**overrides) -> dict:
    base = {
        "hash": "abc123",
        "name": "Some Audiobook [M4B]",
        "tags": "bookscout-42, audiobooks",
        "content_path": "/downloads/Some Audiobook [M4B]",
        "save_path": "/downloads",
    }
    base.update(overrides)
    return base


def test_selects_tagged_torrent():
    cands = select_import_candidates([_torrent()])
    assert cands == [
        {
            "hash": "abc123",
            "name": "Some Audiobook [M4B]",
            "book_id": 42,
            "content_path": "/downloads/Some Audiobook [M4B]",
        }
    ]


def test_skips_untagged_and_marker_tagged():
    torrents = [
        _torrent(tags="audiobooks"),                          # no bookscout tag
        _torrent(tags="bookscout-1, bs-imported"),            # already imported
        _torrent(tags="bookscout-2, bs-failed"),              # failed, needs manual retry
        _torrent(tags="bookscout-xyz"),                       # malformed id
    ]
    assert select_import_candidates(torrents) == []


def test_content_path_falls_back_to_save_path_plus_name():
    t = _torrent(content_path="", save_path="/downloads/", name="Book Dir")
    cands = select_import_candidates([t])
    assert cands[0]["content_path"] == "/downloads/Book Dir"


def test_skips_when_no_path_derivable():
    t = _torrent(content_path="", save_path="", name="Book Dir")
    assert select_import_candidates([t]) == []


def test_tolerates_junk_entries():
    assert select_import_candidates([None, "x", {}, _torrent()]) != []  # type: ignore[list-item]

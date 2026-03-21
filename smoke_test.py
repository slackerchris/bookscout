"""BookScout smoke test.

Runs the full metadata pipeline against one or more authors and reports
per-source and post-merge counts, confidence breakdown, and whether the
Google Books API key is active.

Usage:
    python smoke_test.py                          # default: J.N. Chaney
    python smoke_test.py "Brandon Sanderson"
    python smoke_test.py "J.N. Chaney" "Andrew Rowe"

Flags:
    --no-google     skip Google Books (useful to isolate other sources)
    --no-audible    skip Audible catalog
    --no-ol         skip Open Library
    --lang en       override language_filter (default: from config.yaml or 'en')
    --config PATH   path to config.yaml (default: ./config.yaml)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import httpx

# ── path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from confidence import score_books
from core.merge import merge_books
from core.metadata import (
    query_audnexus,
    query_google_books,
    query_openlibrary,
)


def load_cfg(config_path: str) -> Any:
    try:
        from config import load_config
        return load_config(config_path)
    except Exception:
        return None


async def run_author(
    author: str,
    *,
    language_filter: str,
    google_key: str | None,
    skip_google: bool,
    skip_audible: bool,
    skip_ol: bool,
) -> None:
    print(f"\n{'='*60}")
    print(f"  Author : {author}")
    print(f"  Lang   : {language_filter}")
    print(f"  GB key : {'YES (' + google_key[:8] + '...)' if google_key else 'NO (unauthenticated)'}")
    sources_active = []
    if not skip_ol:
        sources_active.append("OpenLibrary")
    if not skip_google:
        sources_active.append("GoogleBooks" + (" (keyed)" if google_key else ""))
    if not skip_audible:
        sources_active.append("Audible")
    print(f"  Sources: {', '.join(sources_active)}")
    print(f"{'='*60}")

    async with httpx.AsyncClient() as client:
        tasks = []
        labels = []

        if not skip_ol:
            tasks.append(query_openlibrary(client, author, language_filter))
            labels.append("OpenLibrary")

        if not skip_google:
            tasks.append(query_google_books(client, author, language_filter, google_key or None))
            labels.append("GoogleBooks")

        if not skip_audible:
            tasks.append(query_audnexus(client, author, language_filter))
            labels.append("Audible")

        results = await asyncio.gather(*tasks)

    # Per-source counts
    for label, books in zip(labels, results):
        print(f"  {label:<15} {len(books):>4} books")

    # Merge + score
    merged = merge_books(list(results))
    scored = score_books(merged, search_author=author)

    bands: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for b in scored:
        band = b.get("confidence_band", "LOW").upper()
        bands[band] = bands.get(band, 0) + 1

    print(f"  {'─'*38}")
    print(f"  {'Merged (deduped)':<15} {len(scored):>4} books")
    print(f"  Confidence: HIGH={bands['HIGH']}  MEDIUM={bands['MEDIUM']}  LOW={bands['LOW']}")

    # Sample HIGH titles
    high_books = [b for b in scored if b.get("confidence_band", "").upper() == "HIGH"][:5]
    if high_books:
        print(f"\n  Sample HIGH-confidence titles:")
        for b in high_books:
            series = ""
            if b.get("series"):
                pos = b.get("series_position", "")
                series = f"  [{b['series']} #{pos}]" if pos else f"  [{b['series']}]"
            print(f"    • {b['title']}{series}")


async def main(args: argparse.Namespace) -> None:
    cfg = load_cfg(args.config)

    # Language: CLI flag > config > default 'en'
    language_filter = args.lang
    if language_filter is None:
        if cfg:
            scan_cfg = getattr(cfg, "scan", None)
            language_filter = getattr(scan_cfg, "language_filter", "en") or "en"
        else:
            language_filter = "en"

    # Google API key: config (unless --no-google)
    google_key: str | None = None
    if not args.no_google and cfg:
        apis_cfg = getattr(cfg, "apis", None)
        google_key = getattr(apis_cfg, "google_books_key", "") or None

    for author in args.authors:
        await run_author(
            author,
            language_filter=language_filter,
            google_key=google_key,
            skip_google=args.no_google,
            skip_audible=args.no_audible,
            skip_ol=args.no_ol,
        )

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BookScout pipeline smoke test")
    parser.add_argument(
        "authors",
        nargs="*",
        default=["J.N. Chaney"],
        help="Author name(s) to test (default: J.N. Chaney)",
    )
    parser.add_argument("--no-google", action="store_true", help="Skip Google Books")
    parser.add_argument("--no-audible", action="store_true", help="Skip Audible catalog")
    parser.add_argument("--no-ol", action="store_true", help="Skip Open Library")
    parser.add_argument("--lang", default=None, help="Override language_filter")
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "config.yaml"),
        help="Path to config.yaml",
    )
    asyncio.run(main(parser.parse_args()))

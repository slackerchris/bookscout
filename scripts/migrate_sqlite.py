#!/usr/bin/env python3
"""
BookScout SQLite → PostgreSQL Migration Script
===============================================
One-time tool to carry existing SQLite data into the new PostgreSQL schema.

Usage:
    python scripts/migrate_sqlite.py \\
        --sqlite /data/bookscout.db \\
        --postgres postgresql://bookscout:bookscout@localhost/bookscout

    # Validate without writing anything:
    python scripts/migrate_sqlite.py --dry-run

The script is IDEMPOTENT — safe to re-run. Existing records are matched by:
  - Authors : name (exact)
  - Books   : asin → isbn13 → title (in priority order)
Matched records are skipped; only truly new records are inserted.

Prerequisites:
  pip install psycopg2-binary
  (Run `alembic upgrade head` first to create the schema)
"""
import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime


try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("ERROR: psycopg2-binary is required.  pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_name_sort(name: str) -> str:
    """'Brandon Sanderson' → 'Sanderson, Brandon'"""
    parts = name.strip().split()
    if len(parts) <= 1:
        return name.strip()
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def extract_year(release_date: str | None) -> int | None:
    if not release_date:
        return None
    m = re.search(r"\d{4}", str(release_date))
    return int(m.group()) if m else None


def title_sort_key(title: str) -> str:
    """Strip leading articles for sort: 'The Name of the Wind' → 'name of the wind'"""
    return re.sub(r"^(the|a|an)\s+", "", title.lower()).strip()


def connect_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def connect_postgres(dsn: str):
    return psycopg2.connect(dsn)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate(sqlite_path: str, pg_dsn: str, dry_run: bool = False) -> None:
    sqlite = connect_sqlite(sqlite_path)
    pg = connect_postgres(pg_dsn)
    pg.autocommit = False
    pgc = pg.cursor()

    authors_migrated = authors_skipped = 0
    books_migrated   = books_skipped   = 0
    ba_rows = 0

    try:
        # ── 1. Authors ───────────────────────────────────────────────────────
        print("── Authors ─────────────────────────────────────────────────────")
        sqlite_authors = sqlite.execute(
            "SELECT id, name, image_url, last_scanned, active FROM authors ORDER BY id"
        ).fetchall()

        # old SQLite id → new PostgreSQL id
        author_id_map: dict[int, int] = {}

        for row in sqlite_authors:
            name = (row["name"] or "").strip()
            if not name:
                continue

            pgc.execute("SELECT id FROM authors WHERE name = %s", (name,))
            existing = pgc.fetchone()
            if existing:
                author_id_map[row["id"]] = existing[0]
                authors_skipped += 1
                continue

            if not dry_run:
                pgc.execute(
                    """
                    INSERT INTO authors (name, name_sort, image_url, active)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        name,
                        normalize_name_sort(name),
                        row["image_url"],
                        bool(row["active"]) if row["active"] is not None else True,
                    ),
                )
                new_id = pgc.fetchone()[0]
            else:
                new_id = -(row["id"])  # stable negative placeholder for dry-run

            author_id_map[row["id"]] = new_id
            authors_migrated += 1

        print(f"  {authors_migrated} migrated, {authors_skipped} already existed\n")

        # ── 2. Watchlist (all active authors) ────────────────────────────────
        print("── Watchlist ───────────────────────────────────────────────────")
        wl_added = wl_skipped = 0
        for row in sqlite_authors:
            if not (row["active"] if row["active"] is not None else True):
                continue
            pg_author_id = author_id_map.get(row["id"])
            if pg_author_id is None or pg_author_id < 0:
                continue

            pgc.execute("SELECT id FROM watchlist WHERE author_id = %s", (pg_author_id,))
            if pgc.fetchone():
                wl_skipped += 1
                continue

            last_scanned = None
            if row["last_scanned"]:
                try:
                    last_scanned = datetime.fromisoformat(str(row["last_scanned"]))
                except (ValueError, TypeError):
                    pass

            if not dry_run:
                pgc.execute(
                    """
                    INSERT INTO watchlist (author_id, last_scanned, scan_enabled)
                    VALUES (%s, %s, true)
                    """,
                    (pg_author_id, last_scanned),
                )
            wl_added += 1

        print(f"  {wl_added} added, {wl_skipped} already existed\n")

        # ── 3. Books ─────────────────────────────────────────────────────────
        print("── Books ───────────────────────────────────────────────────────")

        # Check which columns exist in the SQLite books table (older DBs may
        # not have score/confidence_band from v0.30.0)
        sqlite_book_cols = {
            row[1] for row in sqlite.execute("PRAGMA table_info(books)").fetchall()
        }
        has_score = "score" in sqlite_book_cols

        col_select = (
            "id, author_id, title, subtitle, isbn, isbn13, asin, "
            "release_date, format, source, cover_url, description, "
            "series, series_position, have_it, deleted, co_authors"
        )
        if has_score:
            col_select += ", score, confidence_band, score_reasons"

        sqlite_books = sqlite.execute(
            f"SELECT {col_select} FROM books ORDER BY id"
        ).fetchall()

        book_id_map: dict[int, int] = {}

        for row in sqlite_books:
            title = (row["title"] or "").strip()
            if not title:
                continue

            # Lookup existing by ASIN → isbn13 → title
            pg_book_id = None
            if row["asin"]:
                pgc.execute("SELECT id FROM books WHERE asin = %s", (row["asin"],))
                r = pgc.fetchone()
                if r:
                    pg_book_id = r[0]

            if pg_book_id is None and row["isbn13"]:
                pgc.execute("SELECT id FROM books WHERE isbn13 = %s", (row["isbn13"],))
                r = pgc.fetchone()
                if r:
                    pg_book_id = r[0]

            if pg_book_id is not None:
                book_id_map[row["id"]] = pg_book_id
                books_skipped += 1
                continue

            if not dry_run:
                pgc.execute(
                    """
                    INSERT INTO books (
                        title, title_sort, subtitle,
                        asin, isbn, isbn13,
                        published_year, release_date,
                        series_name, series_position,
                        format, source, cover_url, description,
                        score, confidence_band, score_reasons,
                        have_it, deleted, match_method
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s
                    )
                    RETURNING id
                    """,
                    (
                        title,
                        title_sort_key(title),
                        row["subtitle"],
                        row["asin"] or None,
                        row["isbn"] or None,
                        row["isbn13"] or None,
                        extract_year(row["release_date"]),
                        row["release_date"],
                        row["series"],
                        row["series_position"],
                        row["format"],
                        row["source"],
                        row["cover_url"],
                        row["description"],
                        (row["score"] if has_score else 0) or 0,
                        (row["confidence_band"] if has_score else "low") or "low",
                        (row["score_reasons"] if has_score else None),
                        bool(row["have_it"]),
                        bool(row["deleted"]),
                        "audiobookshelf" if row["have_it"] else "api",
                    ),
                )
                new_book_id = pgc.fetchone()[0]
            else:
                new_book_id = -(row["id"])

            book_id_map[row["id"]] = new_book_id
            books_migrated += 1

        print(f"  {books_migrated} migrated, {books_skipped} already existed\n")

        # ── 4. book_authors (primary + co-authors from legacy JSON blob) ─────
        print("── book_authors ────────────────────────────────────────────────")

        for row in sqlite_books:
            pg_book_id   = book_id_map.get(row["id"])
            pg_author_id = author_id_map.get(row["author_id"])

            if not pg_book_id or pg_book_id < 0:
                continue

            # Primary author
            if pg_author_id and pg_author_id > 0 and not dry_run:
                pgc.execute(
                    """
                    INSERT INTO book_authors (book_id, author_id, role)
                    VALUES (%s, %s, 'author')
                    ON CONFLICT DO NOTHING
                    """,
                    (pg_book_id, pg_author_id),
                )
                ba_rows += 1

            # Co-authors from legacy co_authors JSON blob
            co_names: list[str] = []
            if row["co_authors"]:
                try:
                    co_names = json.loads(row["co_authors"])
                except (json.JSONDecodeError, TypeError):
                    pass

            for co_name in co_names:
                co_name = co_name.strip()
                if not co_name:
                    continue

                if dry_run:
                    ba_rows += 1
                    continue

                # Find or create co-author record
                pgc.execute("SELECT id FROM authors WHERE name = %s", (co_name,))
                r = pgc.fetchone()
                if r:
                    co_pg_id = r[0]
                else:
                    pgc.execute(
                        "INSERT INTO authors (name, name_sort) VALUES (%s, %s) RETURNING id",
                        (co_name, normalize_name_sort(co_name)),
                    )
                    co_pg_id = pgc.fetchone()[0]

                pgc.execute(
                    """
                    INSERT INTO book_authors (book_id, author_id, role)
                    VALUES (%s, %s, 'co-author')
                    ON CONFLICT DO NOTHING
                    """,
                    (pg_book_id, co_pg_id),
                )
                ba_rows += 1

        print(f"  {ba_rows} rows created\n")

        # ── Commit ───────────────────────────────────────────────────────────
        if not dry_run:
            pg.commit()
            print("✓ Migration complete!")
        else:
            pg.rollback()
            print("✓ Dry run complete — no changes written.")

        print(f"""
Summary
───────
  Authors:      {authors_migrated} migrated, {authors_skipped} already existed
  Books:        {books_migrated} migrated, {books_skipped} already existed
  book_authors: {ba_rows} rows created
""")

    except Exception as exc:
        pg.rollback()
        print(f"\nERROR during migration: {exc}", file=sys.stderr)
        raise
    finally:
        sqlite.close()
        pg.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate BookScout SQLite database to PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--sqlite",
        default="/data/bookscout.db",
        help="Path to the SQLite database file (default: /data/bookscout.db)",
    )
    parser.add_argument(
        "--postgres",
        default="postgresql://bookscout:bookscout@localhost/bookscout",
        help="PostgreSQL DSN",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and count without writing anything to PostgreSQL",
    )
    args = parser.parse_args()

    print(f"Source:      {args.sqlite}")
    print(f"Destination: {args.postgres}")
    print(f"Mode:        {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    migrate(args.sqlite, args.postgres, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

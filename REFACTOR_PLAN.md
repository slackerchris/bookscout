# BookScout Refactoring Plan
## v0.30.0 → v0.40.0: Flask MVP → Async Headless Service

**Target Version:** 0.40.0  
**Start Date:** November 22, 2025  
**Updated:** March 21, 2026  
**Status:** Done  

---

## Target Architecture

```
┌─────────────────────────────────────────────────┐
│                  FastAPI Service                 │
│                                                  │
│  /api/v1/authors    GET/POST/DELETE              │
│  /api/v1/books      GET/PATCH                    │
│  /api/v1/scans      POST  → enqueues job         │
│  /api/v1/webhooks   POST  → register endpoints   │
│  /api/v1/events     GET   → SSE stream           │
└──────────────────┬──────────────────────────────┘
                   │
         ┌─────────┴──────────┐
         │                    │
   ┌─────▼──────┐    ┌────────▼───────┐
   │  arq worker │    │  Event Bus     │
   │  (scan jobs)│    │  Redis pub/sub │
   │             │    └────────┬───────┘
   │  - scan     │             │
   │  - match    │    ┌────────▼───────┐
   │  - score    │    │  Webhooks      │
   └─────────────┘    │  - n8n         │
                       │  - Mafl        │
                       │  - Discord     │
                       │  - ABS notify  │
                       └────────────────┘
```

**Infrastructure:** FastAPI + asyncio + arq (Redis-backed job queue) + PostgreSQL

---

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| API framework | **FastAPI** | Async-native, auto OpenAPI docs, clean dependency injection |
| Job queue | **arq + Redis** | Async-native, lightweight, no Celery overhead; Redis already present for event bus |
| Event bus | **Redis pub/sub** | Reuses existing Redis infra; feeds the SSE stream and webhook dispatcher |
| Database | **PostgreSQL** | Proper relational schema for author identity resolution and many-to-many author/book; Alembic for migrations |
| Notifications | **Webhooks** | More flexible than Apprise; integrates with n8n, Mafl, Discord, ABS natively |
| Real-time | **SSE stream** | Lightweight; no WebSocket complexity for a primarily read-oriented event feed |
| Config | **Hybrid** | DB = source of truth for authors/books/scan history; `config.yaml` = static infra (API keys, URLs, schedule, webhook targets) |
| ORM | **SQLAlchemy async** | Pairs naturally with asyncpg + Alembic; full async query support |

---

## Staged Roadmap

| Version | Focus | Status |
|---|---|---|
| 0.30.0 | Confidence engine wired into Flask app | ✅ Done |
| 0.31.0 | PostgreSQL schema + SQLAlchemy async models + Alembic | ✅ Done |
| 0.32.0 | arq worker + Redis + FastAPI + SSE + Webhooks + config.yaml + CLI (collapsed v0.32–v0.39) | ✅ Done |
| 0.33.0 | *(collapsed into v0.32.0)* | — |
| 0.34.0 | *(collapsed into v0.32.0)* | — |
| 0.35.0 | *(collapsed into v0.32.0)* | — |
| 0.36.0 | *(collapsed into v0.32.0)* | — |
| 0.37.0 | Filesystem scanner (`core/scanner.py`) + `/api/v1/library-paths` + hybrid ABS mode | ✅ Done |
| 0.38.0 | *(collapsed into v0.32.0)* | — |
| 0.39.0 | *(collapsed into v0.32.0)* | — |
| 0.40.0 | Stable service release | ✅ Done |
| 0.41.0 | Cross-watchlist dedup + co-author discovery + structured logging | ✅ Done |
| 0.42.0 | Download integration + post-download file organisation | ✅ Done |
| 0.42.1 | Full env-var config + qBittorrent post-process hook | ✅ Done |
| 0.42.2 | Hotfix: `book.series_name` + `files_moved` type check in importer task | ✅ Done |
| 0.43.0 | Data integrity hardening + scan performance + code hygiene | ✅ Done |
| 0.44.0 | Author identity resolution (`author_aliases`) + scan metrics | ✅ Done |
| 0.45.0 | Metadata response caching with TTL | ✅ Done |
| 0.46.0 | pytest suite for `core/` + promoted smoke tests | ✅ Done |
| 0.47.0 | Webhook retry with exponential backoff + dead endpoint detection | ✅ Done |
| 0.48.0 | `GET /api/v1/authors/{id}/languages` — language catalog breakdown + `books.language` column | ✅ Done |
| 0.49.0 | `GET /api/v1/books?updated_since=` — delta-poll filter for n8n / offline recovery | ✅ Done |
| 0.50.0 | `_get_or_create_author` — normalised-name index + SQL fuzzy match | ✅ Done |

---

## v0.43.0 — Data Integrity Hardening + Scan Performance

**Status:** Done
**Goal:** Close the data integrity gaps uncovered by code review, speed up
prolific-author scans, and consolidate duplicated helper code before the test
suite lands.

### `_find_existing_book` — filter deleted books in Phase 1

**File:** `core/scan.py`  
**Problem:** The Phase 1 global identifier lookup (`isbn13 → isbn → asin`) has
no `deleted.is_(False)` guard.  A soft-deleted book can match, the scan skips
re-inserting it (the `existing and existing.deleted` guard returns `continue`),
and the title is silently absent from scan results.  
**Fix:** Add `.where(Book.deleted.is_(False))` to each Phase 1 query, or log a
`logger.warning("skipping soft-deleted book %s", found.id)` at minimum so the
gap is visible in structured logs.

```python
# Phase 1 query — add deleted filter
q = await session.execute(
    select(Book).where(field == value, Book.deleted.is_(False))
)
```

### `check_audiobookshelf` — semaphore-bounded concurrency

**File:** `core/scan.py` (scan loop)  
**Problem:** ABS ownership checks are serialised one-per-book to respect rate
limits.  For a prolific author (200+ books) this is 200+ sequential HTTP
round-trips; scan wall time is dominated by network latency.  
**Fix:** Replace the serial loop with `asyncio.gather` gated by an
`asyncio.Semaphore(4)`:

```python
sem = asyncio.Semaphore(4)

async def _check_one(book_obj):
    async with sem:
        return await check_audiobookshelf(book_obj, abs_url, abs_token, http_client)

abs_results = await asyncio.gather(*(_check_one(b) for b in books_to_check))
```

Target concurrency of 3–5 strikes a safe balance between throughput and ABS
server load.

### Extract `_sort_name` / `_sort_title` to `core/normalize.py`

**Problem:** `_sort_name` is identical in `core/scan.py`, `api/v1/authors.py`,
and `api/v1/abs.py`.  `_sort_title` is local to `core/scan.py`.  
**Fix:** Move both to `core/normalize.py`, export them, and replace all
call-sites with `from core.normalize import sort_name, sort_title`.  Eliminates
three copies of the same logic and gives the test suite a single target.

### `_get_or_create_author` — groundwork for alias resolution

**File:** `core/scan.py`  
**Note:** Currently does an exact `name ==` match, so `"Terry Maggert"` and
`"Terry H. Maggert"` produce two `Author` rows.  The `author_aliases` table
planned for v0.44.0 is the full fix; in v0.43.0 at minimum apply
`author_names_match()` (already in `core/normalize.py`) as a pre-check before
inserting to catch the most common variant collisions.

---

## v0.44.0 — Author Identity Resolution

**Status:** Done
**Goal:** Consolidate name variants behind a canonical `Author` row and surface
the alias table via the API.

### Author aliases (`author_aliases` table)

Introduce an `author_aliases` table: `(id, author_id FK, alias, source)`.
Populate it with every name variant seen during scanning.  Update
`_get_or_create_author` to query aliases before inserting a new row.  Expose
`GET /api/v1/authors/{id}/aliases` and `POST /api/v1/authors/{id}/aliases`.

### `Book.asin` uniqueness review

**Problem:** `asin` has a `UniqueConstraint` in the schema, but ASINs are not
globally canonical — Amazon reuses them across marketplaces.  Expanding beyond
English-language audiobooks risks constraint violations.  
**Options:**
- Change to a composite unique constraint `(asin, format)` or `(asin, language)`.
- Drop the unique constraint and rely on the merge / dedup logic.
- Keep unique but add a migration guard that logs instead of raising on
  conflict (use `INSERT ... ON CONFLICT DO NOTHING` via SQLAlchemy
  `insert(...).on_conflict_do_nothing(index_elements=["asin"])`).

---

## v0.46.0 — Test Suite

**Status:** Done
**Goal:** Establish a `pytest` baseline that covers the scan pipeline,
confidence engine, and importer.

### Promote `smoke_test.py` to pytest fixtures

`smoke_test.py` validates the full stack manually.  Extract its setup steps
into `conftest.py` fixtures (in-memory SQLite session, mock HTTP responders via
`respx` or `pytest-httpx`, arq worker stub) and convert each scenario into a
`pytest` test function.

### Priority test targets

| Module | Cases |
|---|---|
| `core/scan.py` | `_find_existing_book` Phase 1/2 — live row, deleted row, missing identifier |
| `confidence.py` | Score regression suite — known inputs → expected tier |
| `core/importer.py` | Archive extraction, audio collection, path sanitisation |
| `core/normalize.py` | `sort_name`, `sort_title`, `author_names_match` |
| `workers/tasks.py` | `import_download_task` — success path, missing book, unconfigured library |

---

## v0.40.0 — Stable Service Release

**Status:** Done
**Goal:** Ship a production-ready deployment that requires zero follow-up surgery. All broken pipes fixed, docs match reality, deployment is push-and-run.

### Definition of Done

- [x] README.md fully rewritten for the FastAPI headless service (port 8000, `/docs`, docker-compose)
- [x] DEPLOYMENT.md fully rewritten: `docker-compose up`, `config.yaml` layout, library-path registration via API, ABS integration
- [x] CHANGELOG entry for v0.40.0 with full summary of the v0.32.0→v0.40.0 arc
- [x] `VERSION` bumped to `0.40.0`
- [x] `main.py` `version` string updated to `0.40.0`
- [x] All routes covered by at least one `/docs` smoke-test pass (manual checklist below)
- [x] No stale references to Flask, `app.py`, port 5000, SQLite, `bookscout.db`, `./start.sh`, or `templates/` anywhere in the docs tree

### Smoke-Test Checklist (manual, docker-compose)

```
docker compose up -d
# verify all 5 containers healthy (db, redis, migrate, bookscout, worker)

POST  /api/v1/authors       { "name": "J.N. Chaney" }
GET   /api/v1/authors
POST  /api/v1/scans/author/{id}
GET   /api/v1/books?confidence=HIGH
GET   /api/v1/library-paths
POST  /api/v1/library-paths  { "path": "/mnt/audiobooks", "name": "NAS" }
GET   /events                # SSE stream — confirm heartbeat
```

### Key Improvements Since v0.32.0

| Area | Change |
|---|---|
| API source | Switched from dead Audnexus `/search` to Audible catalog API |
| Language filtering | ISO 639-1 normalisation; default `language_filter: en` |
| Filesystem scanner | `core/scanner.py` + `/api/v1/library-paths`; hybrid ABS/API mode |
| Author matching | `_expand_initials()` in `core/normalize.py`; `"J.N."` ↔ `"J. N."` ↔ `"John N."` all match |
| Documentation | README + DEPLOYMENT rewritten for FastAPI headless service |

---

## v0.41.0 — Cross-Watchlist Dedup + Co-Author Discovery

**Status:** Done  
**Goal:** Eliminate duplicate book rows when co-authors share books across watchlist scans, and expose co-author discovery so users can grow their watchlist organically.

### Problem 1 — Cross-Watchlist Duplicate Books

**Root cause:** `_find_existing_book` in `core/scan.py` joins `book_authors` and
filters on `author_id == <scanning author> AND role == "author"`.  When Author B
(e.g. Terry Maggert) is scanned and a shared book (e.g. *Backyard Starship #1*)
already exists from Author A's (J.N. Chaney's) scan, the query returns nothing —
because Maggert is stored as `role="co-author"`, not `"author"` — and a
duplicate `books` row is inserted with Maggert as the sole primary author.

**Fix — two-phase lookup in `_find_existing_book`:**

1. **Phase 1 — global identity lookup** (no author filter):  
   Search by `isbn13 > isbn > asin` across the entire `books` table.  If found,
   this is the canonical record regardless of which scan created it.
2. **Phase 2 — title fallback** (current behaviour, author-scoped):  
   Only used when no ISBN/ASIN match exists — scope to author to avoid
   collisions on common titles.

When phase 1 returns a hit, the update branch also adds a `role="author"`
`book_authors` row for the scanning author (Maggert), so the book is correctly
associated with both watchlist authors.  Any pre-existing `role="co-author"` row
for that same author is removed (avoids having both roles for the same person).

```
Before fix:
  books:        id=1  title="Backyard Starship #1"  (from Chaney scan)
  books:        id=2  title="Backyard Starship #1"  (from Maggert scan — DUPE)
  book_authors: (1, chaney,  "author")
  book_authors: (1, maggert, "co-author")
  book_authors: (2, maggert, "author")
  book_authors: (2, chaney,  "co-author")

After fix:
  books:        id=1  title="Backyard Starship #1"  (single record)
  book_authors: (1, chaney,  "author")
  book_authors: (1, maggert, "author")   ← both watchlist authors linked
```

### Problem 2 — Stale Co-Author Link Removal

Currently re-scan only *adds* missing co-author rows; it never removes ones that
have become stale (e.g. a source incorrectly credited an extra author and a
later scan corrects it).  Fix: replace the additive logic with a full
set-reconcile — delete co-author rows not present in the fresh scan, add ones
that are missing.

### Problem 3 — Co-Author Discovery

BookScout knows that Maggert co-wrote with Chaney but doesn't surface that
information to the user or act on it.  Plan:

- After each scan, collect the full set of co-author names seen across all
  scanned books
- Check which of those aren't already on the watchlist
- Emit a `coauthor.discovered` SSE/webhook event with the list:
  ```json
  {
    "event": "coauthor.discovered",
    "triggered_by_author": "J.N. Chaney",
    "coauthors": [
      {"name": "Terry Maggert", "book_count": 47},
      {"name": "Christopher Hopper", "book_count": 22}
    ]
  }
  ```
- Add `scan.auto_add_coauthors: false` config flag — when `true`, automatically
  adds discovered co-authors to the watchlist and enqueues their scans
- Add `GET /api/v1/authors/{id}/coauthors` endpoint — returns co-authors
  discovered for a watchlist author with per-co-author book counts and a flag
  indicating whether they're already on the watchlist

### Tasks

- [x] `core/scan.py`: rewrite `_find_existing_book` — phase 1 global ASIN/ISBN
  lookup, phase 2 author-scoped title fallback
- [x] `core/scan.py`: on cross-author hit, add `role="author"` link for scanning
  author, remove stale `role="co-author"` for same person
- [x] `core/scan.py`: replace additive co-author refresh with set-reconcile
  (delete stale rows + add missing rows)
- [x] `core/scan.py`: after scan, collect discovered co-authors + emit
  `coauthor.discovered` event
- [x] `config.py`: add `scan.auto_add_coauthors: false` default
- [x] `core/scan.py`: if `auto_add_coauthors` enabled, enqueue watchlist adds
  for new co-authors
- [x] `api/v1/authors.py`: add `GET /api/v1/authors/{id}/coauthors` endpoint
- [x] `CHANGELOG.md`: v0.41.0 entry
- [x] `VERSION` + `main.py`: bump to `0.41.0`

### Migration note

Existing duplicate book rows (from pre-fix scans) will need a one-off
deduplication migration.  Add an Alembic data migration that:
1. Groups `books` rows by `asin` / `isbn13` / `isbn` (where non-null)
2. For each group, keeps the earliest `created_at` as canonical
3. Repoints `book_authors` rows to the canonical id
4. Deletes the duplicate rows

## v0.48.0 — Language Catalog Visibility

**Status:** Done  
**Goal:** Give users a way to inspect what languages an author's catalog
actually contains before committing to a `language_filter`.  Without this,
choosing the right filter is guesswork for authors who publish in multiple
languages.

### `GET /api/v1/authors/{id}/languages`

**File:** `api/v1/authors.py`  
**Response:** `list[{language: str | null, count: int}]` ordered by count
descending.  `language` is `null` for book rows that pre-date this release.

```json
[
  {"language": "en", "count": 47},
  {"language": "de", "count": 6},
  {"language": null, "count": 2}
]
```

### `Book.language` column

**Migration:** `0005_book_language` — adds `language TEXT` (nullable) to
the `books` table.  
**Population:** `core/scan.py` sets `language=book.get("language")` when
creating a new `Book` row, and includes `"language"` in the COALESCE update
path for existing rows.  All four metadata providers already returned this
value in their result dicts; it was previously discarded.  Existing rows
remain `NULL` until the author is re-scanned.

### Note on `null` counts

If re-scanning is not practical for the existing catalog, a one-off
backfill query can populate reasonable defaults:
```sql
UPDATE books SET language = 'en' WHERE language IS NULL;
```
Not included as a migration step because the correct value is unknowable
for older rows — let re-scans fill in the real data.

---

## v0.49.0 — Books Delta-Poll Filter

**Status:** Done  
**Goal:** Let polling workflows recover any discovery window missed while they
were offline, without needing to diff the full catalog client-side.

### `updated_since` query parameter

**File:** `api/v1/books.py`  
**Usage:** `GET /api/v1/books?updated_since=2026-03-25T12:00:00Z`

Adds a single `WHERE books.updated_at > :updated_since` clause.  Combines
freely with all existing filters.  Strict `>` (not `>=`) avoids re-emitting
the boundary row when the caller stores the last `updated_at` it received as
its cursor.

**Typical n8n pattern:**
1. On each scheduled run, read the stored cursor (e.g. from a static data
   node or a Postgres node) — default to epoch if first run.
2. `GET /api/v1/books?updated_since=<cursor>&have_it=false`
3. Process results, then update cursor to `max(updated_at)` of the
   returned batch.

This is complementary to webhooks: webhooks cover the real-time case;
`updated_since` covers catch-up after a workflow outage or manual back-fill.

---

## v0.50.0 — Author Fuzzy-Match Scalability

**Status:** Done  
**Goal:** Eliminate the O(n) full-table scan in `_get_or_create_author` step 3
so the function scales to 500+ authors without a throughput hit.

### Problem

Step 3 loads every `Author` row and runs `author_names_match()` in Python.
At ≤200 authors this is negligible; at 500+ it becomes the throughput ceiling
for any scan that encounters a new name variant not yet in the aliases table.
The alias table (step 2) already short-circuits this for *known* variants, but
every truly new variant still pays the full scan cost once.

### Fix — normalised name column + SQL lookup

1. **Add `Author.name_normalized` column** (migration `0005`) — a pre-computed
   version of the name with punctuation stripped and lowercased (same transform
   as `_cache_author_key()`):
   ```
   "J.N. Chaney"  → "jnchaney"
   "J. N. Chaney" → "jnchaney"
   ```
2. **Populate on insert/update** — set in `_get_or_create_author` step 4 at
   creation time; backfill existing rows in the migration.
3. **Replace the Python loop in step 3** with a single SQL query:
   ```python
   key = _cache_author_key(name)
   result = await session.execute(
       select(Author).where(Author.name_normalized == key)
   )
   author = result.scalar_one_or_none()
   ```
4. Add `Index("ix_authors_name_normalized", Author.name_normalized)` — makes
   step 3 a single indexed lookup rather than a sequential scan.

### Caveats

- `_cache_author_key` strips all non-alphanumeric characters, which means
  `"O'Brian"` and `"OBrian"` would collide.  Validate against the actual author
  corpus before shipping.
- `author_names_match()` handles initial expansion (e.g. `"J.N."` ↔ `"John N."`)
  which a simple normalised-key equality check does *not*.  The normalised index
  handles the punctuation/spacing class of variants; a separate trigram index
  (pg_trgm) or application-level fallback may still be needed for initial
  expansion cases.

---

## v0.51.0 — Author Fuzzy-Match: Remaining O(n) Cases

**Status:** Planned  
**Goal:** Close the two known gaps left by the v0.50.0 normalised-key index.

### Known Issue 1 — Normalisation Collision

**Problem:** `normalize_author_key` strips *all* non-alphanumeric characters,
so `"O'Brian"` and `"OBrian"` map to the same key (`"obrian"`).  Two distinct
authors could be incorrectly merged.

**Fix options:**
- Validate against the live author corpus before the v0.51.0 release; log a
  warning for any existing author whose `name_normalized` collides with another.
- Consider a smarter normalisation that preserves apostrophes as a separator
  (e.g. `"O'Brian"` → `"o brian"` → still distinct from `"obrian"`).
- At minimum, add a uniqueness check at insert time and fall back to a new
  row rather than silently merging on collision.

### Known Issue 2 — Initial Expansion Not Covered by Normalised Key

**Problem:** The step 3b Python fuzzy-match fallback in `_get_or_create_author`
still performs an O(n) full-table scan for initial-expansion variants
(e.g. `"J.N. Chaney"` ↔ `"John N. Chaney"`).  These are not caught by the
normalised-key equality check because the keys differ (`"jnchaney"` vs
`"johnchaney"`).

**Fix:** Enable the PostgreSQL `pg_trgm` extension and add a trigram index on
`authors.name_normalized`.  Replace the step 3b loop with a single SQL
similarity query:
```sql
SELECT * FROM authors
WHERE similarity(name_normalized, :key) > 0.6
ORDER BY similarity(name_normalized, :key) DESC
LIMIT 1;
```
This makes even the expansion case an indexed sub-linear lookup.

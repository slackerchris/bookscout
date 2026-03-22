# BookScout Refactoring Plan
## v0.30.0 → v0.40.0: Flask MVP → Async Headless Service

**Target Version:** 0.40.0  
**Start Date:** November 22, 2025  
**Updated:** March 21, 2026  
**Status:** In Progress  

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
| 0.42.0 | Author identity resolution (`author_aliases`) + scan metrics (`scan_events`) | 🔜 Next |
| 0.43.0 | pytest suite for `core/` | 📋 Planned |
| 0.44.0 | Metadata response caching with TTL | 📋 Planned |
| 0.45.0 | Webhook retry with exponential backoff + dead endpoint detection | 📋 Planned |

---

## v0.40.0 — Stable Service Release

**Status:** In Progress  
**Goal:** Ship a production-ready deployment that requires zero follow-up surgery. All broken pipes fixed, docs match reality, deployment is push-and-run.

### Definition of Done

- [ ] README.md fully rewritten for the FastAPI headless service (port 8000, `/docs`, docker-compose)
- [ ] DEPLOYMENT.md fully rewritten: `docker-compose up`, `config.yaml` layout, library-path registration via API, ABS integration
- [ ] CHANGELOG entry for v0.40.0 with full summary of the v0.32.0→v0.40.0 arc
- [ ] `VERSION` bumped to `0.40.0`
- [ ] `main.py` `version` string updated to `0.40.0`
- [ ] All routes covered by at least one `/docs` smoke-test pass (manual checklist below)
- [ ] No stale references to Flask, `app.py`, port 5000, SQLite, `bookscout.db`, `./start.sh`, or `templates/` anywhere in the docs tree

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

**Status:** Not Started  
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

- [ ] `core/scan.py`: rewrite `_find_existing_book` — phase 1 global ASIN/ISBN
  lookup, phase 2 author-scoped title fallback
- [ ] `core/scan.py`: on cross-author hit, add `role="author"` link for scanning
  author, remove stale `role="co-author"` for same person
- [ ] `core/scan.py`: replace additive co-author refresh with set-reconcile
  (delete stale rows + add missing rows)
- [ ] `core/scan.py`: after scan, collect discovered co-authors + emit
  `coauthor.discovered` event
- [ ] `config.py`: add `scan.auto_add_coauthors: false` default
- [ ] `core/scan.py`: if `auto_add_coauthors` enabled, enqueue watchlist adds
  for new co-authors
- [ ] `api/v1/authors.py`: add `GET /api/v1/authors/{id}/coauthors` endpoint
- [ ] `CHANGELOG.md`: v0.41.0 entry
- [ ] `VERSION` + `main.py`: bump to `0.41.0`

### Migration note

Existing duplicate book rows (from pre-fix scans) will need a one-off
deduplication migration.  Add an Alembic data migration that:
1. Groups `books` rows by `asin` / `isbn13` / `isbn` (where non-null)
2. For each group, keeps the earliest `created_at` as canonical
3. Repoints `book_authors` rows to the canonical id
4. Deletes the duplicate rows

---

## Overview

Transition BookScout from a user-triggered Flask web app into a fully autonomous async service. The domain logic (API querying, merge/dedupe, confidence scoring) is already proven; the work is replacing UI-driven triggers with scheduled/event-driven scanning, replacing SQLite with a proper relational schema, and exposing everything via a clean REST + SSE + webhook interface.

---


---

## v0.30.0 — Confidence Engine Integration
**Status:** In Progress  
**Goal:** Wire the already-committed `confidence.py` into the Flask scan pipeline as a stepping stone before the full async rewrite.

### Tasks
- [ ] `from confidence import score_books` import in `app.py`
- [ ] Call `score_books(all_books, search_author=author_name)` in `scan_author()` after `merge_books()`, before the ABS check loop
- [ ] SQLite migration in `init_db()`: add `score INTEGER DEFAULT 0`, `confidence_band TEXT DEFAULT 'low'`, `score_reasons TEXT` to `books`
- [ ] Update `INSERT` in `scan_author()` to persist all three columns
- [ ] Show confidence badge (high/medium/low) on each book card in `author.html`
- [ ] `VERSION` → `0.30.0`

---

## v0.31.0 — PostgreSQL Schema + SQLAlchemy Async Models + Alembic
**Goal:** Replace SQLite with PostgreSQL. Define the proper relational schema that will carry the project to v0.40.

### Key Schema Changes vs. Current SQLite
The most important structural change is the **many-to-many author/book relationship**. Currently a book belongs to one author with a `co_authors` JSON blob — that needs to become a proper join table.

```sql
-- Authors: normalized identity
CREATE TABLE authors (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    name_sort   TEXT NOT NULL,   -- "Sanderson, Brandon"
    asin        TEXT UNIQUE,
    openlibrary_key TEXT UNIQUE,
    image_url   TEXT,
    bio         TEXT,
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Books
CREATE TABLE books (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    title_sort      TEXT NOT NULL,
    asin            TEXT UNIQUE,
    isbn            TEXT,
    isbn13          TEXT,
    published_year  INT,
    series_name     TEXT,
    series_position TEXT,
    audio_format    TEXT,
    duration_seconds INT,
    file_path       TEXT,
    file_size       BIGINT,
    file_last_modified TIMESTAMPTZ,
    score           INT DEFAULT 0,
    confidence_band TEXT DEFAULT 'low',
    score_reasons   TEXT,
    match_method    TEXT DEFAULT 'api',  -- 'api' | 'filesystem' | 'manual'
    match_reviewed  BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Many-to-many: authors <-> books
CREATE TABLE book_authors (
    book_id   INT REFERENCES books(id) ON DELETE CASCADE,
    author_id INT REFERENCES authors(id) ON DELETE CASCADE,
    role      TEXT DEFAULT 'author',  -- 'author' | 'narrator' | 'co-author'
    PRIMARY KEY (book_id, author_id, role)
);

-- Watched authors (the user's monitoring list)
CREATE TABLE watchlist (
    id          SERIAL PRIMARY KEY,
    author_id   INT UNIQUE REFERENCES authors(id) ON DELETE CASCADE,
    last_scanned TIMESTAMPTZ,
    next_scan   TIMESTAMPTZ,
    scan_enabled BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Library filesystem paths
CREATE TABLE library_paths (
    id          SERIAL PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    name        TEXT,
    scan_enabled BOOLEAN DEFAULT TRUE,
    last_scanned TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Registered webhook consumers
CREATE TABLE webhooks (
    id          SERIAL PRIMARY KEY,
    url         TEXT NOT NULL UNIQUE,
    description TEXT,
    events      TEXT[],  -- e.g. ['book.missing', 'scan.complete']
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Webhook delivery log
CREATE TABLE webhook_deliveries (
    id          SERIAL PRIMARY KEY,
    webhook_id  INT REFERENCES webhooks(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    payload     JSONB,
    status_code INT,
    success     BOOLEAN,
    delivered_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Module Structure
```
bookscout/
  db/
    models.py        # SQLAlchemy async ORM models
    session.py       # async engine + session factory
    migrations/      # Alembic migration scripts
      env.py
      versions/
        0001_initial_schema.py
        0002_confidence_columns.py
  scripts/
    migrate_sqlite.py  # one-time SQLite → PostgreSQL data migration
```

### New Dependencies
```
asyncpg>=0.29.0
sqlalchemy[asyncio]>=2.0
alembic>=1.13.0
psycopg2-binary>=2.9       # for Alembic env (sync)
```

### SQLite → PostgreSQL Migration Script (`scripts/migrate_sqlite.py`)
- Reads existing `bookscout.db`
- Maps old single-author rows into `authors` + `book_authors` join table
- Parses legacy `co_authors` JSON blob into additional `book_authors` rows with role `co-author`
- Idempotent (safe to re-run)
- Prints summary: authors migrated, books migrated, conflicts skipped

---

## v0.32.0 — arq Worker + Redis Job Queue
**Goal:** Move all scan work off the HTTP thread into an async arq worker. Redis is also the event bus (pub/sub) so it's the only new infrastructure dependency.

### Job Types
```python
# workers/tasks.py
async def scan_author(ctx, author_id: int, *, triggered_by: str = "manual"):
    """Query APIs, merge, score, persist, publish events."""

async def scan_all_authors(ctx):
    """Enqueue individual scan_author jobs for all active watchlist entries."""

async def scan_library_path(ctx, library_path_id: int):
    """Filesystem scan for a configured library path."""

async def match_unmatched_files(ctx):
    """Run confidence matching on files without a confirmed book match."""
```

### Job Result Events (published to Redis)
```
bookscout:events:scan.started      { author_id, job_id }
bookscout:events:scan.complete     { author_id, new_missing: int, job_id }
bookscout:events:book.discovered   { book_id, author_id, confidence_band }
bookscout:events:match.low_confidence  { book_id, score, reasons[] }
bookscout:events:scan.failed       { author_id, error, job_id }
```

### arq Worker Settings
```python
# workers/settings.py
class WorkerSettings:
    functions = [scan_author, scan_all_authors, scan_library_path, match_unmatched_files]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 10
    job_timeout = 300       # 5 min per job
    health_check_interval = 30
```

### New Dependencies
```
arq>=0.26.0
redis>=5.0.0
```

---

## v0.33.0 — FastAPI Service (Replace Flask)
**Goal:** Full Flask → FastAPI rewrite. All HTTP logic becomes async. Templates removed. arq enqueues work; endpoints return immediately.

### Project Layout
```
bookscout/
  main.py              # FastAPI app + lifespan (scheduler + arq pool startup)
  api/
    v1/
      authors.py       # /api/v1/authors
      books.py         # /api/v1/books
      scans.py         # /api/v1/scans  → enqueue jobs
      webhooks.py      # /api/v1/webhooks
      events.py        # /api/v1/events  → SSE
      health.py        # /api/v1/health
  core/
    confidence.py      # (existing)
    scanner.py         # (v0.37)
    notifications.py   # webhook dispatch
    scheduler.py       # APScheduler periodic enqueue
  db/
    models.py
    session.py
    migrations/
  workers/
    tasks.py
    settings.py
  config.py            # config.yaml loader + env var fallback
  cli.py               # typer CLI (v0.39)
```

### Key Endpoints
| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/authors` | List watched authors |
| POST | `/api/v1/authors` | Add to watchlist |
| DELETE | `/api/v1/authors/{id}` | Remove from watchlist |
| POST | `/api/v1/scans/author/{id}` | Enqueue single author scan |
| POST | `/api/v1/scans/all` | Enqueue scan for all watched authors |
| GET | `/api/v1/books` | Query books (`?author_id=&missing=&confidence=`) |
| PATCH | `/api/v1/books/{id}` | Update review/match status |
| GET | `/api/v1/jobs/{job_id}` | Job status |
| GET | `/api/v1/events` | SSE stream of Redis pub/sub |
| POST | `/api/v1/webhooks` | Register webhook endpoint |
| GET | `/api/v1/health` | Service health + last scan timestamps |

### New Dependencies
```
fastapi>=0.115.0
uvicorn[standard]>=0.29.0
httpx>=0.27.0              # replaces requests (async-native)
apscheduler>=3.10.0
```

---

## v0.34.0 — Event Bus + SSE Stream
**Goal:** `/api/v1/events` streams all Redis pub/sub events to any connected consumer (Mafl, n8n, custom dashboard, etc.).

### SSE Event Format
```
event: book.discovered
data: {"book_id": 42, "author": "Brandon Sanderson", "title": "...", "confidence_band": "high"}

event: scan.complete
data: {"author_id": 7, "author": "...", "new_missing": 3, "duration_ms": 4200}

event: match.low_confidence
data: {"book_id": 99, "title": "...", "score": 38, "reasons": ["no_isbn", "fuzzy_title_only"]}
```

### Implementation Notes
- `asyncio.Queue` per connected SSE client
- Redis subscriber task fans out to all active client queues
- Client disconnect cleans up queue (no memory leak)
- `Last-Event-ID` header support for reconnect resume

---

## v0.35.0 — Webhook Registration + Delivery
**Goal:** Replace Apprise with a first-class webhook system. Consumers register URLs and the event types they care about.

### Webhook Payload (all events)
```json
{
  "event": "book.discovered",
  "timestamp": "2026-03-21T14:32:00Z",
  "data": { ... }
}
```

### Delivery Engine
- Fan out in parallel (`asyncio.gather`) to all registered active webhooks for the event type
- Retry on failure: 3 attempts with exponential backoff (1s, 5s, 30s)
- Log result to `webhook_deliveries` table
- Dead endpoint detection: disable webhook after 10 consecutive failures

### Integration Targets
| Target | Method |
|---|---|
| **n8n** | Webhook trigger node → any automation |
| **Discord** | n8n or direct POST to Discord webhook URL |
| **Mafl** | Direct webhook for dashboard notifications |
| **ABS** | POST to ABS notification endpoint if supported |
| **ntfy/Gotify** | Direct POST |

---

## v0.36.0 — `config.yaml`
**Goal:** Single source of truth for all infra settings. No more env-var spaghetti (env vars kept as override mechanism for Docker compatibility).

### Schema
```yaml
database:
  url: postgresql+asyncpg://bookscout:password@localhost/bookscout

redis:
  url: redis://localhost:6379/0

audiobookshelf:
  url: http://localhost:13378
  token: ""

prowlarr:
  url: http://localhost:9696
  api_key: ""

apis:
  google_books_key: ""
  isbndb_key: ""

library_paths:
  - path: /audiobooks
    name: Main Library
    scan_enabled: true

schedule:
  author_rescan_hours: 24
  library_rescan_hours: 6

webhooks:
  # Pre-configured targets (also configurable via API)
  - url: https://n8n.example.com/webhook/bookscout
    events: [book.discovered, scan.complete]
  - url: https://discord.com/api/webhooks/...
    events: [book.discovered]
```

### Loading Priority
1. `config.yaml` (file path from `BOOKSCOUT_CONFIG` env var, default `/data/config.yaml`)
2. Environment variable overrides (e.g. `BOOKSCOUT_DB_URL`)
3. Hardcoded defaults

---

## v0.37.0 — Filesystem Scanner + Hybrid ABS Mode
**Goal:** Scan local audiobook directories; match discovered files against the book catalog using confidence scoring.

### `core/scanner.py`
Key functions:
- `scan_library_path(path)` — recursively find audiobook files (M4B, M4A, MP3 folders, FLAC)
- `extract_file_metadata(file_path)` — `mutagen` for ID3/M4A tags; duration, ASIN, ISBN, narrator
- `parse_filename_metadata(file_path)` — fallback: parse author/title/series from filename patterns
- `match_file_to_catalog(file_metadata, author_books)` — run `confidence.py` scoring against known books

**Metadata priority:** ID3/M4A tags → NFO sidecar → filename → directory structure

### Hybrid Mode Logic
- `match_method` field tracks source: `'audiobookshelf'` | `'filesystem'` | `'manual'`
- When same book appears in both ABS and filesystem: prefer ABS if confidence ≥ 90, else flag for review
- Existing ABS-matched records migrated to `match_method='audiobookshelf'`

### New Dependencies
```
mutagen>=1.47.0
python-Levenshtein>=0.21.0
```

---

## v0.38.0 — Drop Web UI (Headless)
**Goal:** Remove all Jinja2 templating. Service is fully driven by REST API + SSE + webhooks.

### Removals
- `templates/` directory (all `.html` files)
- Jinja2 dependency
- Any `render_template`, flash message, redirect-to-UI logic
- Static file serving (unless needed for future minimal dashboard)

### Retained
- All `/api/v1/*` endpoints
- `/api/v1/events` SSE stream
- `/docs` (FastAPI auto-generated OpenAPI)
- `/api/v1/health`

### Docker
- Entrypoint switches from `python app.py` → `uvicorn bookscout.main:app --host 0.0.0.0 --port 8000`
- `docker-compose.yml` adds Redis + PostgreSQL services, volume mounts for `/data` and library paths
- `bookscout.service` updated accordingly

---

## v0.39.0 — CLI Tooling
**Goal:** Local management without curl. Communicates with the local API.

### Commands
```bash
bookscout authors list
bookscout authors add "Brandon Sanderson"
bookscout authors remove 42

bookscout scan author "Brandon Sanderson"
bookscout scan all
bookscout scan status <job_id>

bookscout books --author "Brandon Sanderson" --missing --confidence low

bookscout webhooks list
bookscout webhooks add <url> --events book.discovered scan.complete
bookscout webhooks test <id>

bookscout status           # health + scheduler + last scan timestamps
bookscout notify test      # fire a test event to all registered webhooks
```

### New Dependency
```
typer>=0.12.0
rich>=13.0.0    # pretty tables in terminal output
```

---

## v0.40.0 — Stable Service Release
**Goal:** Clean, documented, production-ready headless service.

### Final Checklist
- [ ] `main.py` uvicorn entrypoint with lifespan (scheduler start, arq pool, Redis subscriber)
- [ ] Updated `Dockerfile` (multi-stage: deps + app; uvicorn entrypoint)
- [ ] Updated `docker-compose.yml`: bookscout + PostgreSQL + Redis + volume mounts
- [ ] Updated `bookscout.service` systemd unit
- [ ] `migrate_sqlite.py` script tested and documented
- [ ] `README.md` rewritten for service model
- [ ] `QUICKSTART.md`: config.yaml setup, first scan, webhook setup
- [ ] `DEPLOYMENT.md`: Docker volumes, systemd, PostgreSQL setup
- [ ] OpenAPI docs at `/docs`
- [ ] All tests passing (`pytest tests/`)
- [ ] `VERSION` → `0.40.0`

---

## Full Dependency Stack (v0.40)

```
# API
fastapi>=0.115.0
uvicorn[standard]>=0.29.0
httpx>=0.27.0

# Database
asyncpg>=0.29.0
sqlalchemy[asyncio]>=2.0
alembic>=1.13.0
psycopg2-binary>=2.9

# Job queue + event bus
arq>=0.26.0
redis>=5.0.0

# Scheduler
apscheduler>=3.10.0

# Audio metadata (v0.37)
mutagen>=1.47.0
python-Levenshtein>=0.21.0

# Config
PyYAML>=6.0.1

# CLI
typer>=0.12.0
rich>=13.0.0
```

---

## Migration & Rollback

### SQLite → PostgreSQL (v0.31.0)
1. Automatic backup: `/data/bookscout.db.vX.Y.Z.backup` before any migration
2. Run `python scripts/migrate_sqlite.py` — reads SQLite, writes to PostgreSQL
3. Verify counts match before proceeding
4. Keep last 3 SQLite backups

### Per-Version Rollback
1. Stop container / service
2. For DB-level rollback: `alembic downgrade -1`
3. For full rollback: restore DB backup + revert to previous image tag
4. Restart

---

*Last Updated: March 21, 2026*

---

## v0.42.0 — Author Identity Resolution + Scan Metrics

**Status:** Not Started  
**Goal:** Permanently fix multi-name author identity issues (`J.N. Chaney` / `J. N. Chaney` / `Jason N. Chaney`) via a first-class `author_aliases` table, and capture per-scan metrics in a queryable `scan_events` table that complements the structured JSON logging.

---

### Feature 1 — `author_aliases` Table

**Problem:** `_expand_initials()` in `core/normalize.py` handles the most common J.N.-style variants but is purely heuristic — it can't handle arbitrary alternate names, pen names, or transliteration variants (`Tolkien` / `JRRT` / `John Ronald Reuel Tolkien`).  Any new edge case requires a code change.

**Solution:** Introduce an `author_aliases` table that stores known alternate names for a canonical `authors` row.  `author_names_match()` checks aliases before falling back to the fuzzy heuristics.

```sql
CREATE TABLE author_aliases (
    id          SERIAL PRIMARY KEY,
    author_id   INT NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    alias       TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,   -- result of normalize_name(alias)
    source      TEXT DEFAULT 'manual', -- 'manual' | 'api' | 'inferred'
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (author_id, alias_normalized)
);

CREATE INDEX idx_author_aliases_normalized ON author_aliases(alias_normalized);
```

**Lookup flow in `author_names_match()`:**
1. Exact normalized match (current step 1) — no change
2. **NEW:** Check `author_aliases` — if either name matches any alias for the same author, return `True`
3. Initials expansion heuristic (current fallback) — kept as last resort

**API additions:**
- `GET /api/v1/authors/{id}/aliases` — list aliases
- `POST /api/v1/authors/{id}/aliases` — add alias `{ "alias": "J.N. Chaney" }`
- `DELETE /api/v1/authors/{id}/aliases/{alias_id}` — remove alias

**Auto-population:** During scan, when `author_names_match()` returns `True` via the heuristic path, optionally persist the matched variant as an `'inferred'` alias so the heuristic is never re-run for that pair.

### Tasks

- [ ] `db/migrations/` — Alembic migration `0003_author_aliases.py`
- [ ] `db/models.py` — `AuthorAlias` ORM model
- [ ] `core/normalize.py` — `author_names_match()` gains DB alias lookup (injected session)
- [ ] `core/scan.py` — on heuristic-path match, persist alias as `source='inferred'` (behind `scan.infer_aliases: true` config flag)
- [ ] `api/v1/authors.py` — 3 new alias CRUD endpoints
- [ ] `config.py` — `scan.infer_aliases: false` default

---

### Feature 2 — `scan_events` Table

**Problem:** Scan outcomes are logged as structured JSON lines (added in v0.41.0) but are not queryable — you can't ask "how many new books has Chaney yielded over the last 30 scans?" without grepping logs.

**Solution:** After each scan, write one row to a `scan_events` table capturing the key metrics that are already being logged.

```sql
CREATE TABLE scan_events (
    id             SERIAL PRIMARY KEY,
    author_id      INT REFERENCES authors(id) ON DELETE SET NULL,
    triggered_by   TEXT DEFAULT 'scheduler',  -- 'scheduler' | 'manual' | 'webhook'
    status         TEXT NOT NULL,              -- 'success' | 'error'
    books_found    INT DEFAULT 0,
    new_books      INT DEFAULT 0,
    updated_books  INT DEFAULT 0,
    error_message  TEXT,
    duration_ms    INT,
    started_at     TIMESTAMPTZ NOT NULL,
    completed_at   TIMESTAMPTZ
);

CREATE INDEX idx_scan_events_author ON scan_events(author_id, started_at DESC);
CREATE INDEX idx_scan_events_started ON scan_events(started_at DESC);
```

**API additions:**
- `GET /api/v1/scans/history?author_id=&limit=50` — paginated scan history
- `GET /api/v1/scans/history/{scan_id}` — single scan event detail
- `GET /api/v1/authors/{id}/scan-history` — convenience wrapper scoped to one author

**Pairs with structured logging:** The JSON log line and the DB row carry the same fields (`author_id`, `books_found`, `new_books`, `updated_books`) — logs for operators, DB for querying/dashboards.

### Tasks

- [ ] `db/migrations/` — Alembic migration `0004_scan_events.py`
- [ ] `db/models.py` — `ScanEvent` ORM model
- [ ] `core/scan.py` — write `ScanEvent` row at scan start (status=`'in_progress'`) and update on completion/error
- [ ] `api/v1/scans.py` — 3 new history endpoints
- [ ] `CHANGELOG.md` — v0.42.0 entry
- [ ] `VERSION` + `main.py` — bump to `0.42.0`

---

## v0.43.0 — pytest Suite for `core/`

**Status:** Not Started  
**Goal:** Establish a regression net before any schema-level refactor. Pure functions in `core/normalize.py` and `core/merge.py` are the highest-value targets — they have no I/O and can be tested without infrastructure.

### Coverage targets

| Module | Functions | Notes |
|---|---|---|
| `core/normalize.py` | `normalize_name`, `author_names_match`, `_expand_initials` | Many edge cases already documented in comments |
| `core/merge.py` | `merge_books`, `deduplicate_by_isbn` | Pure data transforms |
| `core/scan.py` | `_find_existing_book` | Needs async DB fixture; lower priority |
| `confidence.py` | `score_books`, scoring rules | Existing `test_confidence.py` to be migrated to pytest |

### Fixtures

```python
# tests/conftest.py
@pytest.fixture
async def db_session():
    """In-memory SQLite (or test PostgreSQL) session for scan.py tests."""
    ...
```

### Tasks

- [ ] `pyproject.toml` or `pytest.ini` — pytest config, `asyncio_mode = auto`
- [ ] `requirements-dev.txt` — `pytest`, `pytest-asyncio`, `pytest-cov`
- [ ] `tests/conftest.py` — shared fixtures
- [ ] `tests/test_normalize.py` — port existing edge-case examples from comments + CHANGELOG
- [ ] `tests/test_merge.py` — merge/dedup logic
- [ ] `tests/test_confidence.py` — migrate `test_confidence.py` to pytest
- [ ] GitHub Actions workflow (optional) — `pytest --cov=core` on push

---

## v0.44.0 — Metadata Response Caching with TTL

**Status:** Not Started  
**Goal:** Cache per-author API responses in the database to avoid redundant upstream calls and reduce exposure to rate limits on OpenLibrary / Google Books / Audible.

### Schema

```sql
CREATE TABLE metadata_cache (
    id           SERIAL PRIMARY KEY,
    cache_key    TEXT NOT NULL UNIQUE,  -- '{provider}:{author_normalized}'
    provider     TEXT NOT NULL,         -- 'openlibrary' | 'googlebooks' | 'audible' | 'isbndb'
    author_id    INT REFERENCES authors(id) ON DELETE CASCADE,
    payload      JSONB NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_metadata_cache_key ON metadata_cache(cache_key);
CREATE INDEX idx_metadata_cache_expires ON metadata_cache(expires_at);
```

**TTL config:**
```yaml
scan:
  cache_ttl_hours:
    openlibrary: 24
    googlebooks: 24
    audible: 12
    isbndb: 48
```

**Lookup flow in `core/metadata.py`:**
1. Check `metadata_cache` where `cache_key = '{provider}:{author_normalized}'` AND `expires_at > NOW()`
2. If hit → return cached payload (no upstream call)
3. If miss → fetch from API, write to `metadata_cache`, return result

**Cache invalidation:**
- `POST /api/v1/authors/{id}/invalidate-cache` — force-expire all cached entries for an author
- Automatic expiry via `expires_at`; a periodic cleanup task deletes rows where `expires_at < NOW() - interval '1 day'`

### Tasks

- [ ] `db/migrations/` — Alembic migration `0005_metadata_cache.py`
- [ ] `db/models.py` — `MetadataCache` ORM model
- [ ] `core/metadata.py` — wrap each `query_*()` function with cache read/write
- [ ] `config.py` — `scan.cache_ttl_hours` defaults
- [ ] `api/v1/authors.py` — `POST /api/v1/authors/{id}/invalidate-cache`
- [ ] `workers/tasks.py` — `cleanup_expired_cache_task` arq periodic job

---

## v0.45.0 — Webhook Retry + Dead Endpoint Detection

**Status:** Not Started  
**Goal:** Make webhook delivery reliable. The current implementation fires once and logs failure — no retry, no circuit-breaker.

### Retry strategy

```
Attempt 1 — immediate
Attempt 2 — 1 s delay
Attempt 3 — 5 s delay
Attempt 4 — 30 s delay
→ After 4 failures: mark delivery as permanently failed
```

Implemented as an arq task (not inline in the HTTP handler) so retries survive service restarts.

### Dead endpoint detection

- After **10 consecutive delivery failures** for a webhook, set `webhooks.active = false` and emit a `webhook.disabled` event to the SSE stream
- `GET /api/v1/webhooks` response includes `consecutive_failures` count and `disabled_at` timestamp
- `POST /api/v1/webhooks/{id}/re-enable` — reset failure counter and re-activate

### Schema additions

```sql
ALTER TABLE webhooks ADD COLUMN consecutive_failures INT DEFAULT 0;
ALTER TABLE webhooks ADD COLUMN disabled_at TIMESTAMPTZ;
ALTER TABLE webhooks ADD COLUMN last_success_at TIMESTAMPTZ;
```

### Tasks

- [ ] `db/migrations/` — Alembic migration `0006_webhook_retry_fields.py`
- [ ] `db/models.py` — add three fields to `Webhook` model
- [ ] `workers/tasks.py` — `deliver_webhook_task(webhook_id, event_type, payload)` with retry loop
- [ ] `api/v1/webhooks.py` — update fan-out to enqueue `deliver_webhook_task` instead of inline POST
- [ ] `api/v1/webhooks.py` — `POST /api/v1/webhooks/{id}/re-enable` endpoint
- [ ] `core/search.py` (or notifications module) — emit `webhook.disabled` SSE event on auto-disable
- [ ] `CHANGELOG.md` — v0.45.0 entry
- [ ] `VERSION` + `main.py` — bump to `0.45.0`

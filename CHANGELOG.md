# BookScout Changelog

## [0.41.0] - 2026-07-13

> **Cross-watchlist deduplication + co-author discovery.**  Books shared by
> multiple watched authors are now stored as a single canonical row.  Co-authors
> that appear during scanning are surfaced via a new API endpoint and a Redis
> event, with an optional flag to auto-add them to the watchlist.

### Added
- **`_find_existing_book` Phase-1 global lookup** — ISBN-13, ISBN, and ASIN
  identity checks now search *all* books regardless of which author originally
  added them.  When a cross-author match is found the scanning author is
  immediately promoted to `role="author"` on the canonical `books` row and any
  stale `role="co-author"` row for the same person is removed.  Eliminates
  duplicate book rows for co-authored titles (e.g. a Chaney/Maggert series
  no longer creates two separate `books` rows when each author is scanned).
- **Co-author set-reconcile** — the update branch now performs a full
  add/delete reconcile on `book_authors` rows instead of only appending
  missing entries.  Stale co-author links removed from a book's metadata will
  be cleaned up on the next scan.
- **Co-author discovery** — after each scan, co-author names seen in fetched
  books are checked against the watchlist.  Any that are not already watched
  generate a `coauthor.discovered` Redis event
  `{"event":"coauthor.discovered","author_id":…,"author_name":…,"coauthors":[…],"auto_added":…}`.
- **`scan.auto_add_coauthors`** — boolean config flag (default `false`).  When
  `true`, newly discovered co-authors are automatically added to the watchlist
  so they will be scanned on the next scheduled run.
- **`GET /api/v1/authors/{id}/coauthors`** — returns co-authors for a given
  primary author, ordered by shared-book count descending.  Each entry includes
  `id`, `name`, `on_watchlist`, and `book_count`.
- **Migration `0002_deduplicate_books`** — data migration that groups existing
  `books` rows by ASIN/ISBN-13/ISBN, retains the earliest `created_at` as
  canonical, re-points `book_authors` to the canonical ID, and deletes
  duplicates.

### Fixed
- **Cross-watchlist duplicate books** — root cause of the `_find_existing_book`
  author-scoped filter that prevented recognition of books already in the
  database under a different primary author.

## [0.40.0] - 2026-03-21

> **Stable service release.**  Production-ready FastAPI headless service with
> full Audible catalog coverage, per-source toggle flags, co-author re-scan fix,
> and completely rewritten documentation.

### Added
- **`smoke_test.py`** — pipeline smoke test script with `--no-google` /
  `--no-audible` / `--no-ol` / `--lang` / `--config` flags, per-source counts,
  confidence breakdown, and sample HIGH-confidence titles
- **`scan.sources` config block** — `openlibrary`, `google_books`, `audible`,
  `isbndb` boolean flags in `config.yaml` to enable/disable individual sources
  without redeploying
- **`.gitignore`** — added `.env` and `config.yaml` to prevent accidental
  credential commits

### Fixed
- **Audible pagination cap lifted** — previous hard limit of 4 pages (200
  results); now paginates up to 20 pages (1 000 results) driven by
  `total_results`.  J.N. Chaney: 200 → 298 English audiobooks (340 total,
  42 filtered as non-English translated editions — correct behaviour)
- **Co-author re-scan** — existing books now have co-author `book_authors` rows
  added/refreshed on subsequent scans; previously co-authors were only written
  on first insert
- **`_LANG_NAME_TO_ISO` expanded** — added `pl`, `nl`, `ru`, `ja`, `zh`, `ko`,
  `sv`, `da`, `no`, `fi`, `cs`, `hu`, `ro`, `tr` so full language names (e.g.
  `"polish"`) normalise to ISO 639-1 codes correctly
- **OpenLibrary error logging** — exception type now included in error message
  (was silently printing empty string for `ReadTimeout`)

### Changed
- **README.md** — complete rewrite for FastAPI headless service: port 8000,
  `/docs`, docker-compose quickstart, `config.yaml` reference, API endpoint
  table, homelab integration diagram
- **DEPLOYMENT.md** — complete rewrite: docker-compose workflow, `config.yaml`
  setup, initial API walkthrough, webhook registration, ABS integration,
  systemd bare-metal template, troubleshooting, backup/restore
- **REFACTOR_PLAN.md** — added `## v0.40.0` section with definition of done,
  smoke-test checklist, and key-improvements-since-v0.32.0 table

---

## [0.37.0] - 2026-03-21

> **Filesystem scanner + library path management.**  BookScout can now detect
> owned audiobooks directly from local library directories, without requiring
> Audiobookshelf.  ABS and filesystem ownership checks work together — whichever
> fires first marks the book as owned.

### Added
- **`core/scanner.py`** — async filesystem scanner
  - Walks configured library path directories for audio files (`.m4b`, `.mp3`,
    `.flac`, `.opus`, `.aac`, `.ogg`, `.wma`, `.m4a`)
  - Parses author + title from directory structure (supports ABS standard layout,
    single-file books, nested series folders, and `Author - Title` filename pattern)
  - Matches found files against DB books using `author_names_match` + word-overlap
    title similarity (≥ 0.75 threshold)
  - Matched books updated: `have_it=True`, `match_method='filesystem'`,
    `file_path=<directory>`
  - Deduplicates multi-part books (multiple files in same folder = one match)
  - Updates `LibraryPath.last_scanned` timestamp on completion
- **`api/v1/library_paths.py`** — library path REST API
  - `GET /api/v1/library-paths` — list all configured paths with scan status
  - `POST /api/v1/library-paths` — register a new path (validates existence)
  - `DELETE /api/v1/library-paths/{id}` — remove a path
  - `POST /api/v1/library-paths/{id}/scan` — enqueue filesystem scan for one path
  - `POST /api/v1/library-paths/scan-all` — enqueue scan for all enabled paths
- **`workers/tasks.py`** — two new arq tasks
  - `scan_library_path_task(library_path_id)` — scan a single path
  - `scan_all_library_paths_task()` — scan all enabled paths sequentially
- **`workers/settings.py`** — both new tasks registered in `WorkerSettings.functions`

### Changed
- `main.py`: mounted `/api/v1/library-paths` router; bumped `version` to `0.37.0`
- `VERSION` → `0.37.0`
- `REFACTOR_PLAN.md`: roadmap table updated — all completed versions marked ✅,
  v0.40.0 marked as next target

---

## [0.32.1] - 2026-03-21

### Fixed
- **Audnexus API broken** (`core/metadata.py`): The `/search?name=` endpoint returns
  HTTP 404.  `query_audnexus()` rewritten to use the Audible catalog API
  (`api.audible.com/1.0/catalog/products`) for audiobook discovery (paginated,
  up to 200 books per author) with Audnexus `/books/{asin}` for per-book enrichment
  (cover, ISBN, release date, series). Result: 0 → 199 audiobooks for Brandon Sanderson.
- **Language normalisation** (`core/metadata.py`): Audnexus returns full language
  names (`"english"`, `"german"`); these are now mapped to ISO 639-1 codes (`"en"`,
  `"de"`) to match the `language_filter` convention. The `language_filter` parameter
  was previously accepted but silently ignored — it now correctly filters results.
- **Default `language_filter`** (`config.yaml.example`, `core/scan.py`): Changed
  from `"all"` to `"en"` so new deployments default to English-only results.

---

## [0.32.2] - 2026-03-21

### Fixed
- **`author_names_match()` missed spaced-initial variants** (`core/normalize.py`):
  `"J.N. Chaney"` normalized to the single token `"jn"`, while `"J. N. Chaney"`
  normalized to `["j", "n"]` — the existing initials logic could never reconcile
  these. Added `_expand_initials()` which splits 2–3 character all-consonant
  non-last tokens back into individual initials before comparison. Now matches:
  `J.N. Chaney` ↔ `J. N. Chaney`, `John N. Chaney`, `Jason N. Chaney`,
  `J.R.R. Tolkien` ↔ `J. R. R. Tolkien` ↔ `John Ronald Reuel Tolkien`.
  No false positives introduced (`James Chaney`, `Jordan Chaney` still `False`).

---

## [0.32.0] - 2026-03-21

### Added
- **FastAPI service** (`main.py`): replaces Flask (`app.py` deleted)
  - `uvicorn main:app` entry-point; auto-generated `/docs` (Swagger UI) and `/redoc`
  - CORS middleware, async lifespan managing Redis + arq connections
- **REST API** (`api/v1/`):
  - `GET/POST/PATCH/DELETE /api/v1/authors` — watchlist CRUD with stats
  - `GET/PATCH/DELETE /api/v1/books` — book querying and edits
  - `POST /api/v1/scans/author/{id}` — enqueue single-author scan
  - `POST /api/v1/scans/all` — enqueue full-watchlist scan
  - `GET /api/v1/scans/job/{id}` — arq job status polling
  - `GET /api/v1/events` — SSE stream (real-time scan events from Redis pub/sub)
  - `GET/POST/DELETE /api/v1/webhooks` — webhook registration + delivery log
  - `POST /api/v1/webhooks/{id}/test` — test delivery
  - `POST /api/v1/search` — unified Prowlarr + Jackett search
  - `POST /api/v1/search/download` — route to configured download client
  - `POST /api/v1/audiobookshelf/import-authors` — bulk-import ABS library authors
  - `GET /health` — liveness + DB readiness check
- **async core modules** (`core/`):
  - `core/normalize.py` — author name normalisation and fuzzy matching
  - `core/metadata.py` — async `httpx` versions of all 4 API query functions (OpenLibrary, Google Books, Audnexus, ISBNdb) + Audible series lookup; OpenLibrary/Google Books/Audnexus queried **in parallel** per scan
  - `core/merge.py` — book deduplication and source accumulation
  - `core/audiobookshelf.py` — async ABS ownership check + bulk author fetch
  - `core/search.py` — async Prowlarr / Jackett search + SABnzbd / qBittorrent / Transmission download routing
  - `core/scan.py` — `scan_author_by_id()` async scan orchestrator writing to PostgreSQL
- **arq workers** (`workers/`):
  - `workers/tasks.py` — `scan_author_task` and `scan_all_authors_task` arq functions
  - `workers/settings.py` — `WorkerSettings` class; start with `arq workers.settings.WorkerSettings`
  - Worker context initialised with a Redis async client for event publishing
- **Config system** (`config.py`, `config.yaml.example`):
  - Reads `config.yaml` (path via `BOOKSCOUT_CONFIG` env var, default `/data/config.yaml`)
  - Deep-merges with hard-coded defaults then layers env var overrides
  - Covers: database, redis, audiobookshelf, prowlarr, jackett, APIs, download clients, scan schedule
- **CLI** (`cli.py`): typer-based command-line interface
  - `python cli.py scan --author-id <id>` — in-process single-author scan
  - `python cli.py scan --all` — in-process full-watchlist scan
  - `python cli.py migrate --sqlite <path>` — delegates to `scripts/migrate_sqlite.py`
- **Docker Compose** updated:
  - `migrate` service: runs `alembic upgrade head` once before anything starts
  - `bookscout` service: `uvicorn main:app`, port `8000`
  - `worker` service: `arq workers.settings.WorkerSettings` (separate process)
- **Dockerfile** updated: uvicorn entrypoint, copies `core/`, `api/`, `workers/`, `cli.py`

### Removed
- `app.py` — Flask monolith
- `templates/` — all Jinja2 HTML templates
- `start.sh` — Flask dev-server script
- Flask, Werkzeug, requests from `requirements.txt`

### Changed
- `requirements.txt`: Flask/Werkzeug/requests → fastapi, uvicorn, httpx, typer, rich
- `VERSION` → `0.32.0`

---

## [0.31.0] - 2026-03-21

### Added
- **PostgreSQL support**: Full async schema via SQLAlchemy 2.0 + asyncpg
  - Proper relational schema replaces SQLite flat tables
  - Many-to-many `book_authors` join table with `role` discriminator (`author` / `co-author` / `narrator`) — replaces legacy `co_authors` JSON blob
  - `watchlist` table separates "monitored authors" from raw author records
  - `library_paths`, `webhooks`, `webhook_deliveries` tables added for upcoming v0.37 and v0.35 features
  - Full index set on hot query paths (`isbn13`, `confidence_band`, `have_it`, `name_sort`, `author_id`)
- **Alembic migrations** (`alembic.ini`, `db/migrations/`): Version-controlled schema management
  - Async-compatible `env.py` using `asyncpg`
  - `DATABASE_URL` env var overrides `alembic.ini` (Docker-friendly)
  - Initial migration `0001_initial_schema.py` creates all tables with `alembic upgrade head`
- **SQLite → PostgreSQL migration script** (`scripts/migrate_sqlite.py`)
  - Idempotent: safe to re-run; skips already-migrated records
  - Migrates authors, books, watchlist; explodes legacy `co_authors` JSON → `book_authors` rows
  - `--dry-run` flag validates and counts without writing
  - Usage: `python scripts/migrate_sqlite.py --sqlite /data/bookscout.db --postgres postgresql://...`
- **Docker Compose** updated with PostgreSQL 16 + Redis 7 services
  - Health checks on both services; bookscout `depends_on` both
  - `POSTGRES_PASSWORD` env var (default: `bookscout` — change in production)
  - Named volumes: `postgres-data`, `redis-data`, `bookscout-data`
- **`db/models.py`**: SQLAlchemy async ORM models (used by Alembic and future FastAPI service)
- **`db/session.py`**: Async engine + `AsyncSessionFactory` + `get_session()` FastAPI dependency

### Note
`app.py` continues running on SQLite for this version. The PostgreSQL schema is established and data migration tooling is ready. The Flask → FastAPI cutover happens in v0.33.0.

---

## [0.30.0] - 2026-03-21

### Added
- **Confidence Scoring Engine** (`confidence.py`): Scores merged book results to surface the most reliable matches
  - Multi-signal scoring: exact/normalized title match, author match (exact + fuzzy), ISBN match, publication year, provider count, audiobook format
  - ISBN match awards +100 points; multi-provider presence adds up to +35 points
  - Penalty system: bad-keyword detection (-60) for summaries/workbooks/companions, suspicious edition mismatch (-25) for abridged/illustrated/movie tie-in editions
  - Results bucketed into `high` (≥100), `medium` (50–99), and `low` (<50) confidence bands
  - Each scored book carries `score`, `confidence_band`, and `score_reasons` fields for transparency and debugging
- **Confidence Integration** (`app.py`): `score_books()` wired into `scan_author()` pipeline
  - Called after `merge_books()`, results sorted by score descending before ABS check loop
  - DB migration: `score`, `confidence_band`, `score_reasons` columns added to `books` table (auto-migrates on startup)
  - Both INSERT and UPDATE paths persist score data
- **Confidence Badges** (`author.html`): Visual confidence indicator on every book card
  - Green = high (≥100), yellow = medium (50–99), red = low (<50)
  - Raw score shown in tooltip on hover
- **Confidence Integration Guide** (`CONFIDENCE_INTEGRATION.py`): Reference patch instructions
- **Confidence Test Suite** (`test_confidence.py`): Unit tests covering scoring rules and edge cases
- **Updated Roadmap** (`REFACTOR_PLAN.md`): Full v0.30→v0.40 staged plan — FastAPI + arq + Redis + PostgreSQL service architecture

---

## [0.29.4] - 2025-11-05

### Changed
- **Version Numbering**: Reset to 0.x.x to indicate beta/personal-use status
  - Major refactor would be needed for public release (ID-based authors, caching, etc.)
  - Current version is stable and feature-complete for personal use

### Added
- **Duplicate Author Finder**: Find and merge duplicate authors
  - Detects authors with similar names using normalization logic
  - Identifies authors sharing books (same ASINs/ISBNs)
  - UI to review and approve merges
  - Moves all books to primary author, deactivates duplicates
  - Accessible via "Duplicate Authors" in navigation

### Fixed
- **Audnexus Author Extraction**: Fetch complete author list from book details
  - Search results only showed searched author name
  - Now fetches full book details by ASIN to get all co-authors
  - Enables proper co-author display for audiobooks

---

## [2.9.3] - 2025-11-05

### Added
- **Co-Author Support**: Track and display multiple authors per book
  - New `co_authors` JSON column stores additional authors beyond primary
  - APIs automatically extract all authors from responses (OpenLibrary, Google Books)
  - Co-authors displayed on book cards as "with [Author 2], [Author 3]"
  - Manual add/edit forms include co-authors field (comma-separated input)
  - Primary author concept: book belongs to one author (first/main), others shown as collaborators
  - Similar to Readarr's author model for practical management

---

## [2.9.2] - 2025-11-05

### Added
- **Premium API Support**: Optional paid API keys in settings
  - ISBNdb API key support ($10-50/month for comprehensive ISBN metadata)
  - Google Books API key support (free, increases rate limits)
  - Premium APIs integrated into author scanning and metadata search
  - Settings UI shows links to API documentation and signup
- **Delete Book Button**: Quick delete on each book card
  - Red "Delete Book" button below Edit/Find Info buttons
  - Confirmation dialog before deletion
  - Easier to remove mismatched books without using Manage Duplicates
- **Increased API Result Limits**: Better book discovery for prolific authors
  - OpenLibrary: 100 → 200 results
  - Google Books: 40 → 120 results (pagination over 3 pages)
  - Audnexus: 40 → 100 results
  - Timeouts increased to 15 seconds

### Fixed
- **Author Name Normalization**: Handle author name variations and inconsistencies
  - Handles initials with/without periods: "J.N. Chaney" vs "j n Chaney" vs "JN Chaney"
  - Handles spacing variations: "j. n. Chaney" vs "j.n.chaney"
  - Removes suffixes: Jr, Sr, II, III, IV for better matching
  - Bidirectional initial matching: "j n chaney" matches "john nicholas chaney"
  - Applied consistently across OpenLibrary and Google Books queries
  - Reduces false positives while catching formatting variations

---

## [2.9.1] - 2025-11-05

### Fixed
- **Soft Delete System**: Prevent merged/deleted books from re-appearing during rescans
  - Books are now marked as `deleted = 1` instead of being permanently removed
  - Deleted books are filtered out from all views
  - Rescans skip books marked as deleted
  - Preserves database history while keeping interface clean
- **Preserve Manual Edits During Rescan**: Only update empty fields
  - Changed UPDATE logic to use `COALESCE(existing, new)`
  - Manual edits to ISBN, ASIN, series, descriptions are now protected
  - `have_it` status still updates to reflect current library state

---

## [2.9.0] - 2025-11-05

### Added
- **Manual Book Management**: Complete suite of manual book editing features
  - Add books manually with comprehensive form (title, subtitle, series, ISBN, ASIN, release date, format, cover URL, description)
  - Edit existing book details with pre-populated form
  - Search book metadata across Open Library, Google Books, and Audnexus APIs
  - Select and apply correct metadata from search results
  - Edit and Find Info buttons on each book card
- **Metadata Search**: Visual card-based results display showing:
  - Book cover images
  - All available identifiers (ISBN, ISBN-13, ASIN)
  - Series information
  - Release dates and formats
  - Source badges (OpenLibrary, GoogleBooks, Audnexus)
  - One-click "Use This" to apply selected metadata

### Technical
- New backend routes: `/books/<id>/edit`, `/books/<id>/search-metadata`, `/books/<id>/apply-metadata`
- Smart metadata merging only updates non-empty fields
- Search filters results by title similarity across all three APIs
- Complete JavaScript handlers for edit and metadata search workflows

### Fixed
- JSON parsing error in metadata apply button (stored objects directly on DOM elements instead of as JSON strings in data attributes)
- Proper HTML escaping to prevent XSS vulnerabilities

---

## [2.4.0] - 2025-11-03

### Fixed
- **CRITICAL:** Pagination finally works correctly!
  - Changed from `offset` parameter to `page` parameter (ABS API requirement)
  - Was processing same 100 books repeatedly (that's why only 39 authors)
  - Now actually pages through all 1747 books
  - Should find 300+ unique authors now

### Changed
- Better logging shows: "page X, items Y-Z" instead of "offset X"
- Shows final count: "Finished: processed X of Y items"

---

## [2.3.1] - 2025-11-03

### Fixed
- **CRITICAL:** Bulk import now properly splits multi-author books
  - Handles "Author A, Author B" → creates 2 authors
  - Handles "Author A & Author B" → creates 2 authors  
  - Handles "Author A and Author B" → creates 2 authors
  - Should now find 300+ authors instead of only 39
- Fixed template crash when viewing author pages (Jinja2 syntax error)

---

## [2.3.0] - 2025-11-03

### Added
- **Edit Author Names** - Click pencil icon to fix import errors or spelling
  - Available on home page (author cards)
  - Available on author detail page
  - Uses modal popup for clean UX
  - Validates for duplicates

---

## [2.2.0] - 2025-11-03

### Added
- **Statistics Dashboard** on home page showing:
  - Total authors being monitored
  - How many have been scanned
  - How many are pending scan

---

## [2.1.1] - 2025-11-03

### Fixed
- Footer now properly supports dark mode (text readable in both themes)

---

## [2.1.0] - 2025-11-03

### Fixed
- **CRITICAL:** Bulk import from Audiobookshelf now works correctly
  - Fixed API response parsing to match actual ABS structure
  - Added pagination support (fetches all books, not just first 100)
  - Handles multi-author books (splits "Author A, Author B" into separate authors)
  - Better error logging for debugging

### Technical
- Updated `get_all_authors_from_audiobookshelf()` function
- Reads from `media.metadata.authorName` structure
- Paginates through library with 100 items per request
- Prints progress to Docker logs

---

## [2.0.0] - 2025-11-03

### Added
- **Bulk Import from Audiobookshelf** - Import all authors from your library at once
- **Show Missing Only Filter** - Toggle to view only books you don't have
- **Dark Mode** - Full dark theme with persistent preference
- **Success Messages** - Flash notifications for all actions
- **Better UX** - Save button redirects to home page with confirmation

### Changed
- Settings save now redirects to home page (instead of staying on settings)
- All forms now show success/error messages via flash alerts
- Improved visual feedback throughout the app

### Fixed
- Dockerfile now handles empty static/ directory correctly

---

## [1.0.0] - 2025-11-03

### Initial Release
- Multi-source book discovery (Open Library, Google Books, Audnexus)
- Manual author management
- Audiobookshelf integration (check what you have)
- Prowlarr integration (search for missing books)
- SQLite database
- Web UI with Bootstrap 5
- Docker deployment support

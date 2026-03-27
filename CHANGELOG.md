# BookScout Changelog

## [0.60.0] - 2026-03-27

### Fixed
- **Duplicate co-author `BookAuthor` inserts crashing scans** — When the same
  co-author name appeared more than once in a book's `authors` list (e.g.,
  returned by two different metadata sources), the scan would add two
  `BookAuthor(book_id, author_id, role='co-author')` objects to the session.
  The first was flushed successfully; the second caused a
  `UniqueViolationError` on `uq_book_author_role` when a later autoflush fired,
  crashing the entire scan task.  Two fixes applied:
  - **New-book path**: added a `new_book_co_ids` set that guards
    `session.add(BookAuthor(...))` so the same author_id is never linked twice
    to the same new book.
  - **Update-book path**: moved `fresh_co_ids.add(co_author.id)` *after* the
    insert guard and extended the condition to
    `co_author.id not in existing_co_ids and co_author.id not in fresh_co_ids`,
    preventing a second insert when the same co-author resolves twice within
    the same reconciliation loop.  `fresh_co_ids` is still fully populated
    for the stale-link cleanup that follows.

---

## [0.50.16] - 2026-03-26

### Added
- **qBittorrent tag support** — new `default_tag` field in `download.torrent`
  config (env var `TORRENT_TAG`). Tags are sent as the qBittorrent `tags`
  field (comma-separated). The `bookscout-{book_id}` tag is appended when
  `book_id` is set, so both the configured tag and the per-book tracking tag
  are applied together.

---

## [0.50.15] - 2026-03-26

### Fixed
- **Prowlarr search restricted to torrent indexers** — Added `protocol=torrent`
  to Prowlarr search params so only torrent indexers are queried (matching the
  Audio/Audiobook + Torrent filter shown in the Prowlarr UI).

---

## [0.50.14] - 2026-03-26

### Fixed
- **Search returning epubs** — Reverted indexer category from `7000` (Books/
  Ebooks) back to `3030` (Audio/Audiobook). Category 7000 returns epub/pdf
  results; 3030 is the correct Newznab category for audiobook files (mp3/m4b).

---

## [0.50.13] - 2026-03-26

### Fixed
- **Non-English Audible books slipping through language filter** — Root cause
  found: the Audible catalog API only returns the `language` field when
  `media` is included in `response_groups`. Without it every product came
  back with no language, so the filter had nothing to act on and fell through
  to the Audnexus per-ASIN enrichment step which could time out leaving
  `language=null`. Fix: added `media` to `response_groups`; Polish/German/etc.
  editions are now filtered out in the catalog page loop before any Audnexus
  enrichment is attempted. The catalog language is also stored on the book
  dict as an immediate fallback so the post-enrichment filter always has a
  value to check even if Audnexus times out.

---

## [0.50.12] - 2026-03-26

### Fixed
- **Confidence scores always Low** — Author-scan mode was scoring a maximum of
  75 points (author match + 3 sources) so nothing ever reached HIGH (≥100).
  Two root causes fixed:
  - Added an identifier bonus in author-scan mode: ASIN present `+40`
    (confirmed Audible product), ISBN present `+20`.  A single-source Audnexus
    book now scores author(40) + ASIN(40) + audiobook(20) = 100 → HIGH.
  - `score_books()` now always passes `want_audiobook=True`; BookScout is an
    audiobook tracker so the +20 audiobook format bonus should always apply.

---

## [0.50.11] - 2026-03-26

### Fixed
- **Non-English books persisting after rescan** — Scan now soft-deletes any
  unowned books for the scanned author whose stored `language` is known and
  doesn't match the configured `language_filter`. Previous scans left behind
  Polish/etc. editions with `language='pl'` that the filter was correctly
  excluding at query time but never removing from the DB.
- **Google Books language default** — Removed the implicit `"en"` default for
  the Google Books `language` field. Books with no language tag in the API
  response now store `null` and are excluded by the language filter instead
  of being assumed English.

---

## [0.50.10] - 2026-03-26

### Changed
- **Search category** — Indexer queries now use Newznab category `7000` (Books)
  instead of `3030` (Audio/Audiobook).

---

## [0.50.9] - 2026-03-26

### Fixed
- **`english_only` filter — Latin-script non-English books** — `BookOut` now
  exposes the stored `language` code so the UI can filter on `language == 'en'`
  directly. Previously the filter only detected non-Latin scripts (Cyrillic,
  CJK, etc.) and let through translations in Latin-script languages like Polish.
  Rows with no `language` value still fall back to the non-Latin script regex.

---

## [0.50.8] - 2026-03-26

### Fixed
- **Search — audiobook category filter** — Prowlarr queries now use
  `categories=[3030]` (Newznab audiobook) instead of the generic `type=book`
  endpoint; Jackett queries pass `Category[]=3030` so results are scoped to
  audiobook indexers only, eliminating unrelated results.

---

## [0.50.7] - 2026-03-26

### Fixed
- **Author table bloat** — `_get_or_create_author` was creating DB rows for
  every name in the `authors` field returned by OpenLibrary and Google Books,
  which includes translators, editors, illustrators, foreword writers and
  narrators. This caused the author table to grow to 3500+ rows when only ~370
  were real tracked authors.
- **Contributor-role filter** (`_is_contributor_only`) — new filter in
  `core/scan.py` rejects names encoding a non-author role before they reach
  the DB. Catches: dash/paren role suffixes (`"Frog Jones - editor"`,
  `"Alan Tepper - Übersetzer"`), plain-space suffixes (`"Grover Gardner
  narrator"`), `"Read by …"` / `"Narrated by …"` prefixes, comma-separated
  narrator group credits (`"Scott Aiello, Marc Vietor, Tavia Gilbert"`), and
  known noise strings (`"et al"`, `"A Full Cast"`, `"Various Authors"`, etc.).
  Covers English, German (`Übersetzer`), French (`traducteur`), Italian
  (`traduttore`), Spanish (`traductor`) and Portuguese (`tradutor`) role words.
- **`auto_add_coauthors=false` now actually prevents Author row creation** —
  previously the flag only controlled Watchlist entries; co-author names still
  silently created Author rows via `_get_or_create_author`. A new
  `_find_author()` function (lookup-only, never inserts) is used instead when
  the flag is off.
- **Narrators separated from authors** — narrator names are now stored in a
  dedicated `books.narrator` TEXT column (comma-joined string) and never
  written to the authors table. Extracted from the Audible/Audnexus API
  `narrators` array; merged and deduplicated across sources. Exposed in
  `BookOut` API response.
- **`import-authors` and `sync-books` ABS endpoints** now apply the same
  `_is_contributor_only` filter so a clean re-import from Audiobookshelf does
  not recreate junk rows. `import-authors` also now sets `name_normalized` on
  newly created Author rows (was previously missing).

### Added
- **Migration `0007_book_narrator`** — `ALTER TABLE books ADD COLUMN narrator TEXT`.

## [0.50.6] - 2026-03-26

### Fixed
- ABS sync now splits comma/ampersand-separated `authorName` strings and takes
  only the primary author per book, eliminating ~600 phantom "authors" that
  were being created from multi-author ABS metadata fields.
- ABS sync strips role annotations (`- introduction`, `(narrator)` etc.) from
  author names before matching/creating Author rows.
- ABS sync strips `(Unabridged)` / `(Abridged)` from titles at import.

## [0.50.5] - 2026-03-26

### Fixed
- **Scan dedup phase 2** now uses `normalize_title_key()` instead of exact string
  match, so "(Unabridged)" / verbose subtitle variants imported by ABS sync no
  longer create duplicate rows when the metadata scan runs.

## [0.50.4] - 2026-03-26

### Added
- **`POST /api/v1/audiobookshelf/sync-books`** — walks every ABS library and
  imports all owned books directly into the DB (`have_it=True`,
  `match_method="audiobookshelf"`).  Captures title, author, series name,
  series position, ASIN, and ISBN straight from ABS metadata so books like
  "Alpha" that the metadata APIs miss are still tracked.  Creates author +
  watchlist entries for any unknown authors.  After import, enqueues a
  metadata scan for every affected author to backfill descriptions, cover
  URLs, and confidence scores via the normal scan pipeline.
- **`get_all_books_from_audiobookshelf()`** (`core/audiobookshelf.py`) — paginates
  all ABS library items and returns structured book dicts with series/identifier
  fields.

## [0.50.3] - 2026-03-26

### Fixed
- **Confidence scoring inflated in author scans** — `score_books()` was passing
  each book's own title as `search_title`, giving every book +50 for "exact title
  match" against itself, and the ISBN bonus (+100) fired for any book that had an
  identifier. Author scans now pass an empty `search_title` so only author-match
  and multi-source signals contribute — wrong-author books from noisy API results
  (e.g. a Terry Brooks "Landover" book returned when scanning Aleron Kong) now
  score low instead of high.
- **ISBN bonus gated on title search** — the +100 ISBN/ASIN presence bonus in
  `score_book()` now only applies when a `search_title` is provided, since the
  signal is only meaningful when looking for a specific book.

## [0.50.2] - 2026-03-26

### Fixed
- **Duplicate editions not collapsed** — `merge_books()` now runs a second
  title-dedup pass after identifier dedup.  Different API editions of the same
  book (each with a unique ISBN/ASIN but an equivalent normalised title, e.g.
  `"God's Eye : Awakening"` / `"God's Eye: Awakening: A Labyrinth World Novel"`)
  are collapsed into a single record.  The shortest/cleanest title is kept and
  all fields are coalesced across editions.

## [0.50.1] - 2026-03-26

### Fixed
- **ABS ownership check — all books showing Missing** — `check_audiobookshelf()`
  now strips parenthetical content and verbose subtitles from the title before
  querying ABS (e.g. `"The Land: Founding: A LitRPG Saga (Chaos Seeds) (Volume 1)"`
  → `"The Land: Founding"`).  The word-overlap ratio now divides by
  `min(title_words, abs_words)` instead of `len(title_words)`, so a short ABS
  title correctly matches a long metadata API title.
- **Duplicate books in scan results** — the title-based merge dedup key now uses
  `normalize_title_key()` which strips leading articles, parentheticals, and
  text after a second colon.  Variants like `"The Land: Founding"`, `"Land:
  Founding (Chaos Seeds) (Volume 1)"`, and `"The Land: Founding: A LitRPG Saga
  (Chaos Seeds) (Volume 1)"` now merge into a single record.
- **`coroutine was never awaited` warnings** — unawaited metadata coroutines are
  now explicitly closed when a Redis cache hit is returned, eliminating
  `RuntimeWarning` spam in the worker logs.

### Added
- **`normalize_title_key()`** (`core/normalize.py`) — normalised dedup key for
  book titles; strips articles, parentheticals, and verbose subtitles.
- **`abs_search_title()`** (`core/normalize.py`) — simplified title for ABS
  search queries; keeps only the main title and first subtitle segment.

## [0.50.0] - 2026-03-26

### Added
- **`Author.name_normalized` column + index** — new `TEXT` column on the
  `authors` table storing a punctuation/case-stripped key (e.g. `"J.N. Chaney"`
  → `"jnchaney"`).  Indexed via `ix_authors_name_normalized`.  Populated at
  author creation time and backfilled for existing rows via migration `0006`.
- **`normalize_author_key()` helper** (`core/normalize.py`) — single source of
  truth for the normalisation transform used by both the SQL index and
  `_cache_author_key()` in `core/scan.py`.

### Changed
- **`_get_or_create_author` step 3** — replaced the O(n) full-table Python scan
  with a single indexed SQL lookup on `name_normalized`.  Covers
  punctuation/spacing variants (e.g. `"J.N. Chaney"` ↔ `"J. N. Chaney"`)
  without loading every author row.  A Python fuzzy-match fallback (step 3b)
  is retained for initial-expansion variants not handled by the key equality
  check (e.g. `"J.N."` ↔ `"John N."`); see v0.51.0 for the pg_trgm fix.
- **Language filter — OpenLibrary** — OpenLibrary returns ISO 639-2 three-letter
  codes (`"eng"`, `"kor"`).  The filter was comparing against two-letter codes
  (`"en"`), so it never matched and returned zero books.  A new
  `_LANG_639_2_TO_1` mapping normalises all codes to ISO 639-1 before
  filtering.  When a book has multiple editions in different languages, the
  matching language is stored as the primary rather than whichever OL listed
  first.
- **Language filter — Audnexus/ISBNdb** — books whose language cannot be
  determined (Audnexus enrichment failed or book not in Audnexus, ISBNdb
  record has no language field) now default to `None` instead of `"en"`.
  When a language filter is active, `None`-language books are excluded rather
  than assumed to be English, preventing non-English books from slipping
  through when the enrichment call fails.

### Migration
- `0006_author_name_normalized` — adds `authors.name_normalized` (nullable
  TEXT), backfills it from existing `name` values using
  `regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')`, and creates
  `ix_authors_name_normalized`.

## [0.49.3] - 2026-03-25

### Fixed
- **`scan_all_authors_task` crash (follow-up)** — `ctx["redis"]` provided by arq
  is a plain `Redis` client, not an `ArqRedis` instance. Fixed by creating a
  dedicated `ArqRedis` pool via `create_pool(_redis_settings())` instead of
  reusing the context client.

## [0.49.2] - 2026-03-25

### Fixed
- **`scan_all_authors_task` crash** — `ArqRedis(redis_client)` was wrapping an
  already-constructed `ArqRedis` instance (injected by arq into `ctx["redis"]`)
  in a second `ArqRedis()` call, causing `AttributeError: 'Redis' object has no
  attribute 'connection_kwargs'` on every scan-all invocation. The fix uses
  `ctx["redis"]` directly as the arq connection.
- **Run-together initials normalisation** — `"D.E. Sherman"` and
  `"D. E. Sherman"` now normalise to the same string. A pre-processing step in
  `normalize_author_name()` inserts a space between adjacent letter-period-letter
  sequences before stripping all periods.

## [0.49.1] - 2026-03-25

### Fixed
- **ABS author import strips role annotations** — author name parts from ABS metadata
  are now cleaned before import. Suffixes like `- editor`, `(narrator)`,
  `- Author & Narrator`, `- Translator & Editor`, `(foreword)`, `(afterword)`,
  `(introduction)`, and `(contributor)` are stripped via a regex applied in
  `get_all_authors_from_audiobookshelf()` before the name hits the deduplication
  and noise-filter logic. This ensures "Christopher Tolkien - editor" is stored
  as "Christopher Tolkien" and correctly deduplicates against any existing
  "Christopher Tolkien" entry.

## [0.49.0] - 2026-03-25

### Added
- **`GET /api/v1/books?updated_since=<ISO 8601>`** — new query parameter that
  filters results to books whose `updated_at` is strictly after the given
  timestamp.  Combines freely with all existing filters (`author_id`,
  `confidence_band`, `have_it`, `missing_only`).  Designed for polling
  workflows (e.g. n8n) that process only new discoveries since their last run,
  recovering any window missed while the workflow was offline without needing
  to diff the full catalog.

## [0.48.0] - 2026-03-25

### Added
- **`GET /api/v1/authors/{id}/languages`** — returns a per-language count
  breakdown (ISO 639-1 codes, ordered by count descending) for a given author's
  catalog.  Useful for choosing an appropriate `language_filter` before
  triggering a scan, particularly for authors who publish in multiple languages.
  The `language` field is `null` for book rows that pre-date this release.
- **`Book.language` column** — ISO 639-1 language code (e.g. `"en"`, `"de"`)
  stored at scan time from the metadata source.  All four metadata providers
  (OpenLibrary, Google Books, Audnexus, ISBNdb) already returned a `language`
  key in their result dicts; this change persists it to the database.  Existing
  rows are `NULL` until re-scanned.
- **Migration `0005_book_language`** — adds `language TEXT` (nullable) to
  the `books` table.

## [0.47.0] - 2026-03-25

### Added
- **`api/v1/webhooks._deliver()` — exponential backoff retry** — delivery now
  retries up to 3 attempts (delays: 0 s, 2 s, 8 s) before recording a failure.
  The single-attempt `test` endpoint keeps its original one-shot behaviour.
- **Dead endpoint detection** — `deliver_event()` now tracks consecutive
  delivery failures per webhook.  Once `failure_count` reaches 5 the webhook is
  automatically deactivated (`active=False`, `disabled_at` timestamp set) and a
  `WARNING` log entry is emitted.  A successful delivery resets `failure_count`
  to 0.
- **`POST /api/v1/webhooks/{id}/reactivate`** — re-enables a webhook that was
  auto-disabled by dead endpoint detection (or manually via DELETE), resetting
  `failure_count` and clearing `disabled_at`.
- **`Webhook.failure_count` / `Webhook.disabled_at`** — new schema columns
  exposed on `WebhookOut` responses so callers can see the health of each
  registered endpoint.
- **Migration `0004_webhook_retry`** — adds `failure_count INTEGER NOT NULL
  DEFAULT 0` and `disabled_at TIMESTAMPTZ` to the `webhooks` table.

## [0.46.0] - 2026-03-25

### Added
- **`tests/` — pytest suite** — 84 tests covering `core/normalize.py`,
  `confidence.py`, `core/importer.py`, `core/scan._find_existing_book()`, and
  `core/scan._cached_query()`.  Tests use `pytest-asyncio` (auto mode) and an
  in-memory SQLite database via `aiosqlite` so no external services are needed.
- **`tests/conftest.py`** — shared fixtures: session-scoped async engine with
  FK enforcement, per-test rolled-back `AsyncSession`, and a book-dict factory.
- **`pytest.ini`** — project pytest configuration (`asyncio_mode = auto`,
  `testpaths = tests`).
- Added `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`, `aiosqlite>=0.20.0` to
  `requirements.txt`.

### Fixed
- **`core/scan._find_existing_book()` Phase 2** — title fallback query now
  excludes soft-deleted books (matches Phase 1 behaviour).  Previously a
  deleted book could be returned via Phase 2 even though Phase 1 correctly
  filtered it out by identifier.

## [0.45.0] - 2026-03-25

### Added
- **`core/scan.py` — `_cached_query()`** — Redis-backed cache wrapper for
  raw metadata API responses.  Each source's result list is serialised to
  JSON and stored under a key of the form
  `bookscout:meta:{source}:{author}:{lang}`.  On the next scan for the same
  author and language, the cached payload is returned immediately without
  hitting the external API.  Cache misses are transparent; Redis errors
  (read or write) are caught, logged at `WARNING`, and fall through to a
  live query so a Redis hiccup never breaks a scan.
- **`core/scan.py` — `_cache_author_key()`** — normalises an author name to
  a compact alphanumeric key segment (e.g. `"J.N. Chaney"` → `"jnchaney"`).
- **`config.py` / `config.yaml.example`** — new `scan.cache_ttl_hours`
  setting (default: `24`).  Set to `0` to disable caching entirely.  Can
  also be controlled via the `SCAN_CACHE_TTL_HOURS` environment variable.

### Changed
- **`core/scan.py` — source query tasks** — all four metadata source calls
  (`query_openlibrary`, `query_google_books`, `query_audnexus`,
  `query_isbndb`) are now wrapped with `_cached_query()` so repeat scans of
  the same author within the TTL window are served from Redis rather than
  making fresh outbound HTTP requests.  The `metadata.py` functions
  themselves remain stateless and unchanged.

---

## [0.44.0] - 2026-03-25

### Added
- **`db/models.py` — `AuthorAlias` model** — new `author_aliases` table:
  `(id, author_id FK, alias, source, created_at)`.  The `(author_id, alias)`
  pair is unique.  `source` records where the variant was seen (`'scan'`,
  `'abs'`, `'manual'`).  Cascades on author delete.
- **`db/migrations/versions/0003_author_aliases.py`** — Alembic migration
  that creates `author_aliases`, adds indexes on `alias` and `author_id`,
  and **drops** the `uq_books_asin` unique constraint on `books.asin`.
- **`GET /api/v1/authors/{id}/aliases`** — returns all known name variants
  for an author, ordered by insertion time.
- **`POST /api/v1/authors/{id}/aliases`** — manually register a new alias
  (`alias`, `source` defaulting to `"manual"`); 409 if it already exists.
- **`DELETE /api/v1/authors/{id}/aliases/{alias_id}`** — remove a specific
  alias by id.

### Changed
- **`core/scan.py` — `_get_or_create_author()`** — full alias resolution
  pipeline: (1) exact `Author.name` match, (2) `author_aliases` table
  lookup for a previously seen variant, (3) fuzzy `author_names_match()`
  scan as last resort.  Every variant that passes through the function is
  recorded in `author_aliases` via the new `_record_alias()` helper, so
  future lookups hit step 2 (alias table) instead of the linear scan.
- **`core/scan.py` — new `_record_alias()` helper** — inserts an
  `AuthorAlias` row if `(author_id, alias)` is not already present.
- **`books.asin` unique constraint dropped** — Amazon ASINs are not globally
  canonical (reused across marketplaces).  Duplicate prevention is now
  handled entirely by the `_find_existing_book` Phase-1 lookup.

---

## [0.43.0] - 2026-03-25

### Added
- **`core/scan.py` — `_get_or_create_author()`** — added fuzzy name-match
  pre-check using `author_names_match()` before inserting a new `Author` row.
  Exact match is tried first (indexed, fast); if that misses, all existing
  authors are checked with `author_names_match()` to catch common variants
  such as `"Terry Maggert"` vs `"Terry H. Maggert"`.  Prevents duplicate
  author rows for the most common collision patterns ahead of the full alias
  resolution landing in v0.44.0.
- **`core/scan.py` — ABS concurrency** — replaced the serial
  per-book `check_audiobookshelf` loop with `asyncio.gather` gated by
  `asyncio.Semaphore(4)`.  Up to 4 ABS ownership checks now run in parallel,
  significantly reducing scan wall time for prolific authors (200+ books was
  previously 200+ sequential round-trips).

### Fixed
- **`core/scan.py` — `_sort_name` stale reference** — co-author discovery
  block was calling the removed `_sort_name()` private function (deleted in
  v0.42.3) when `auto_add_coauthors` is enabled.  Replaced with the canonical
  `sort_name()` import from `core.normalize`.

---

## [0.42.3] - 2026-03-23

### Changed
- **`core/normalize.py`** — added `sort_name()` and `sort_title()` as public
  helpers.  All three previous private copies (`_sort_name` in
  `core/scan.py`, `api/v1/authors.py`, `api/v1/abs.py`; `_sort_title` in
  `core/scan.py`) are removed and replaced with imports from this single
  source of truth.

### Fixed
- **`core/scan.py` — `_find_existing_book()`** — Phase 1 identifier queries
  now include `Book.deleted.is_(False)`.  Previously a soft-deleted row
  could be found by isbn/asin, the scan would skip re-inserting it (the
  `existing.deleted` guard), and the book would silently vanish from scan
  results.  Deleted books are now invisible to Phase 1 so a fresh row is
  created as expected.

---

## [0.42.2] - 2026-03-23

### Fixed
- **`workers/tasks.py` — `import_download_task()`** — `book.series` attribute
  reference corrected to `book.series_name` (the actual model field).  The
  previous code would raise `AttributeError` at runtime any time an import was
  triggered for a book with series metadata.
- **`workers/tasks.py` — `import_download_task()`** — `files_moved` success
  check changed from `result.get("files_moved", 0) > 0` to
  `result.get("files_moved")`.  `files_moved` is a `list[str]`, not an int;
  comparing a list with `> 0` raises `TypeError` in Python — the truthiness
  check is both correct and simpler.

---

## [0.42.1] - 2026-03-23

### Added
- **Full env-var config** — all download and post-process settings can now
  be set via environment variables / `.env` file without touching
  `config.yaml`: `DOWNLOAD_PREFERRED`, `SABNZBD_URL`, `SABNZBD_API_KEY`,
  `SABNZBD_CATEGORY`, `TORRENT_URL`, `TORRENT_USERNAME`, `TORRENT_PASSWORD`,
  `TORRENT_CATEGORY`, `TORRENT_SAVE_PATH`, `POSTPROCESS_MODE`,
  `POSTPROCESS_LIBRARY_ROOT`.
- **`scripts/qbittorrent-postprocess.sh`** — drop-in post-download hook for
  qBittorrent.  Reads the `bookscout-{id}` tag stamped by BookScout at grab
  time, calls `POST /api/v1/books/{id}/import`, and logs the result.
  Configure via `BOOKSCOUT_URL`, `TRIGGER_CATEGORY`, and `LOG_FILE` env vars.
- **`book_id` in `DownloadRequest`** — when set, the torrent is submitted to
  qBittorrent with a `bookscout-{id}` tag enabling automatic post-process
  correlation.

### Fixed
- **`docker-compose.yml`** — removed duplicate `volumes:` key under the
  `bookscout` service.

---

## [0.42.0] - 2026-03-23

> **Download integration + post-download file organisation.**  Full pipeline
> from indexer search through download client submission to automatic
> extraction and library organisation.

### Added
- **`POST /api/v1/books/{id}/search`** — auto-constructs an indexer search
  query from the book's title and primary author name, queries all configured
  Prowlarr / Jackett indexers, and returns annotated results with
  `size_human` field.
- **`GET /api/v1/search/status`** — pings all configured indexers and the
  active download client in parallel; returns connectivity and version info
  for each service.
- **`GET /api/v1/search/download/queue`** — proxies the live download queue
  from the configured download client (SABnzbd, qBittorrent, or Transmission)
  with per-item progress, ETA, and save path.
- **`POST /api/v1/books/{id}/import`** — enqueues an `import_download_task`
  that extracts archives and moves audio files into
  `<library_root>/<Author>/<Series>/<Title>/`.  Requires
  `postprocess.mode: bookscout` and a configured `postprocess.library_root`.
- **`core/importer.py`** — post-download file organiser: handles zip / rar /
  7z extraction (optional `rarfile` + `py7zr` deps), collects audio files
  (`.m4b .mp3 .flac .opus .aac .ogg .wma .m4a`), sanitises path components,
  builds the `author/series/title` destination tree, moves files, and cleans
  up temporary work directories.
- **`import_download_task`** (`workers/tasks.py`) — arq background task that
  reads book metadata from the DB, runs the importer in a thread pool, and
  marks `book.have_it = True` / `book.match_method = "imported"` on success.
  Registered in `WorkerSettings.functions`.
- **`postprocess` config section** — new top-level key with two fields:
  `mode` (`"client"` default | `"bookscout"`) and `library_root` (absolute
  path to the root audiobook library).

### Changed
- **`send_to_sabnzbd()` / `send_to_torrent_client()`** — return a rich `dict`
  (`success`, `nzo_id` / `hash`, `detail` on failure) instead of a bare
  `bool`; callers now surface the client reference to the API response.
- **`POST /api/v1/search/download`** — response now includes `nzo_id` (NZB)
  or `hash` (torrent) from the download client.
- **Download clients** — `category` and `save_path` parameters added to all
  three send functions (SABnzbd `cat=`, qBittorrent `category`/`savepath`,
  Transmission `download-dir`).  Config gains `default_category` (SABnzbd +
  torrent) and `save_path` (torrent) with per-request override support.

### Fixed
- **`GET /api/v1/events` SSE heartbeat** — was firing every ~1 s due to a
  missing time-gate; now fires exactly every 30 s using a timestamp
  comparison.

---

## [0.41.4] - 2026-03-22

### Fixed
- **`workers/tasks.py` — `scan_all_authors_task()`** — was running all author
  scans inline in a single arq job, hitting the 300 s job timeout after just a
  handful of authors.  Now enqueues one `scan_author_task` job per author via
  `ArqRedis.enqueue_job()` so each author scan runs independently within its
  own timeout budget.  Falls back to inline execution when no Redis context is
  available (e.g. CLI usage).
- **`workers/settings.py`** — raised `job_timeout` from 300 s to 600 s so a
  single author scan (which hits Audible + OpenLibrary + Google Books in
  parallel) has enough headroom even for prolific authors.

---

## [0.41.3] - 2026-03-21

> **Smarter ABS import deduplication.**  Author name variants like `"J.N. Chaney"`,
> `"JN Chaney"`, and `"j.n. chaney"` now collapse into a single watchlist entry.
> Noise strings (`"others"`, `"various"`, etc.) are filtered out.

### Fixed
- **`core/audiobookshelf.py` — `get_all_authors_from_audiobookshelf()`** — replaced
  the raw `set[str]` with a `dict` keyed by `normalize_author_name()`.  When two
  strings normalise to the same key (e.g. `"J.N. Chaney"` and `"JN Chaney"`), the
  longer/more-detailed display form is kept.  Added `_NOISE_AUTHORS` denylist
  that discards `"others"`, `"various"`, `"various authors"`, `"unknown"`,
  `"unknown author"`, `"multiple authors"`, `"multiple narrators"`, `"narrators"`.
- **`api/v1/abs.py` — `import_authors()`** — replaced the exact-string
  `Author.name == name` duplicate check with `author_names_match()` fuzzy
  comparison against all existing author names loaded in a single query.  Same
  guard is applied within a single import batch (prevents two name-variants
  arriving in the same API call from both being inserted).

---

## [0.41.2] - 2026-03-21

### Changed
- **Default port changed from 8000 to 8765** — avoids conflict with Portainer
  (port 8000) and stays clear of all common `*arr` suite ports (Sonarr 8989,
  Radarr 7878, Lidarr 8686, Readarr 8787, Prowlarr 9696, Jackett 9117).
  Updated in `Dockerfile`, `docker-compose.yml`, `bookscout.service`,
  `config.yaml.example`, `.env.example`, and all documentation.

---

## [0.41.1] - 2026-03-21

> **Structured JSON logging.**  All `print()` calls replaced with a proper
> `logging` setup that emits newline-delimited JSON — ready for Loki, Grafana,
> or any log aggregator.

### Added
- **`core/logging_config.py`** — `setup_logging()` configures the root logger to
  emit newline-delimited JSON via `python-json-logger`.  Reads `LOG_LEVEL` env
  var (default `INFO`); gracefully falls back to plain text if the package is
  absent.  Suppresses noisy third-party loggers (`httpx`, `httpcore`,
  `uvicorn.access`, `sqlalchemy.engine`).
- **`LOG_LEVEL` env var** — exposed in `docker-compose.yml` for both `bookscout`
  and `worker` services (`${LOG_LEVEL:-INFO}`); documented in `.env.example`.
- **`python-json-logger>=2.0.7`** added to `requirements.txt`.

### Changed
- **All 18 `print()` calls replaced** with structured `logger.*` calls across
  `main.py`, `workers/settings.py`, `core/metadata.py`, `core/scan.py`,
  `core/audiobookshelf.py`, `core/search.py`, and `api/v1/webhooks.py`.
- Key structured log events include `author_id`, `books_found`, `new_books`,
  `updated_books`, `error`, and `exc_type` fields for machine-readable filtering.

---

## [0.41.0] - 2026-03-21

> **Cross-watchlist deduplication + co-author discovery + scheduled scanning.**
> Books shared by multiple watched authors are now stored as a single canonical
> row.  Co-authors are surfaced via a new API endpoint and a Redis event.
> `schedule_cron` in config now actually fires — the arq worker runs a full
> watchlist scan on the configured schedule.

### Added
- **Scheduled scanning** — `WorkerSettings.cron_jobs` is now built from
  `scan.schedule_cron` in `config.yaml` (default `"0 * * * *"` = top of every
  hour).  The arq worker parses the 5-field crontab string at startup and
  registers `scan_all_authors_task` as a recurring cron job — no external
  scheduler or `POST /scans/all` is needed.  Supports all standard crontab
  syntax: `*`, `*/n`, `n`, `n-m`, `n,m`.  Parse errors disable the schedule
  and log a warning rather than crashing the worker.
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

## [0.40.0] - 2026-03-17

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

## [0.37.0] - 2026-03-14

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

## [0.32.1] - 2026-03-12

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

## [0.32.2] - 2026-03-10

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

## [0.32.0] - 2026-03-08

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

## [0.31.0] - 2026-02-24

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

## [0.30.0] - 2026-02-21

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

## [2.9.3] - 2025-11-01

### Added
- **Co-Author Support**: Track and display multiple authors per book
  - New `co_authors` JSON column stores additional authors beyond primary
  - APIs automatically extract all authors from responses (OpenLibrary, Google Books)
  - Co-authors displayed on book cards as "with [Author 2], [Author 3]"
  - Manual add/edit forms include co-authors field (comma-separated input)
  - Primary author concept: book belongs to one author (first/main), others shown as collaborators
  - Similar to Readarr's author model for practical management

---

## [2.9.2] - 2025-10-31

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

## [2.9.1] - 2025-10-29

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

## [2.9.0] - 2025-10-22

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

## [2.4.0] - 2025-10-22

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

## [2.3.1] - 2025-10-20

### Fixed
- **CRITICAL:** Bulk import now properly splits multi-author books
  - Handles "Author A, Author B" → creates 2 authors
  - Handles "Author A & Author B" → creates 2 authors  
  - Handles "Author A and Author B" → creates 2 authors
  - Should now find 300+ authors instead of only 39
- Fixed template crash when viewing author pages (Jinja2 syntax error)

---

## [2.3.0] - 2025-10-20

### Added
- **Edit Author Names** - Click pencil icon to fix import errors or spelling
  - Available on home page (author cards)
  - Available on author detail page
  - Uses modal popup for clean UX
  - Validates for duplicates

---

## [2.2.0] - 2025-10-19

### Added
- **Statistics Dashboard** on home page showing:
  - Total authors being monitored
  - How many have been scanned
  - How many are pending scan

---

## [2.1.1] - 2025-10-19

### Fixed
- Footer now properly supports dark mode (text readable in both themes)

---

## [2.1.0] - 2025-10-18

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

## [2.0.0] - 2025-10-16

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

## [1.0.0] - 2025-10-14

### Initial Release
- Multi-source book discovery (Open Library, Google Books, Audnexus)
- Manual author management
- Audiobookshelf integration (check what you have)
- Prowlarr integration (search for missing books)
- SQLite database
- Web UI with Bootstrap 5
- Docker deployment support

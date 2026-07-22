# BookScout Architecture

## System Overview

BookScout is an **async headless REST API service** built on FastAPI.
All interaction is via HTTP endpoints documented at `/docs`; the optional
[bookscout-ui](https://github.com/slackerchris/bookscout-ui) web panel is a
separate SPA that consumes this API.

```
┌──────────────────────────────────────────────────────────────────┐
│                        BookScout                                  │
│                    (FastAPI / uvicorn)                            │
│                       port 8765                                   │
└──────────────────┬───────────────────────────────────────────────┘
                   │  REST API calls
         ┌─────────┴──────────┐
         │                    │
         ▼                    ▼
  ┌─────────────┐     ┌──────────────┐
  │ PostgreSQL  │     │    Redis     │
  │ (data store)│     │ (job queue + │
  └─────────────┘     │  event bus)  │
                      └──────┬───────┘
                             │  arq jobs
                             ▼
                      ┌──────────────┐
                      │  arq Worker  │
                      │  (scan jobs) │
                      └──────┬───────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │ Open Library │  │ Google Books │  │   Audnexus   │
  └──────────────┘  └──────────────┘  └──────────────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                             │ merge + score
                             ▼
                      ┌──────────────┐
                      │Audiobookshelf│  ownership check
                      └──────┬───────┘
                             │
                             ▼
                      ┌──────────────┐
                      │  PostgreSQL  │  persist books/authors
                      └──────────────┘
                             │
                             ▼
                      ┌──────────────┐
                      │    Redis     │  publish scan.complete
                      │  (pub/sub)   │  coauthor.discovered
                      └──────────────┘
```

---

## Data Flow: Scanning an Author

```
POST /api/v1/scans/{author_id}
         │
         ▼ (enqueue via arq)
┌────────────────────────────────────────────┐
│  arq Worker — scan_author(author_id)        │
└────────────────────────────────────────────┘
         │
         ├─► OpenLibrary API    ─┐
         ├─► Google Books API   ─┤  parallel gather
         ├─► Audnexus API       ─┤
         └─► ISBNdb API         ─┘ (if key configured)
                                │
                         merge_books()
                         score_books()
                                │
                         ┌──────▼───────┐
                         │ ABS check    │  have_it = true/false
                         └──────┬───────┘
                                │
                   for each book in all_books:
                         │
                   _find_existing_book()
                   ├── Phase 1: global ISBN-13/ISBN/ASIN lookup
                   └── Phase 2: author-scoped title fallback
                         │
              ┌──────────┴──────────┐
              │                     │
           new book             existing book
         INSERT + link         UPDATE coalesce
         BookAuthor            co-author set-reconcile
                         │
                    co-author discovery
                    publish coauthor.discovered
                         │
                    publish scan.complete
```

---

## Database Schema

```sql
-- Watched authors
authors (
    id, name, name_sort, name_normalized, asin, openlibrary_key,
    image_url, bio, active, created_at, updated_at
)

-- Known name aliases for authors (e.g. "J.N. Chaney" → "JN Chaney")
author_aliases (
    id, author_id, alias, source, created_at
)

-- Watchlist entries (one per watched author)
watchlist (
    id, author_id, scan_enabled, last_scanned, favorite, created_at
)

-- Books discovered from all sources
books (
    id, title, title_sort, subtitle, isbn, isbn13, asin,
    release_date, published_year, format, source,
    cover_url, description, narrator, language,
    series_name, series_position,
    have_it, score, confidence_band, score_reasons,
    match_method, deleted, created_at, updated_at
)

-- Many-to-many: books ↔ authors, with role
book_authors (
    book_id, author_id,
    role  -- "author" | "co-author"
)

-- Webhook endpoints registered by the user
webhooks (
    id, url, description, events, active,
    failure_count, disabled_at, created_at
)

-- Delivery log for each webhook event
webhook_deliveries (
    id, webhook_id, event_type, payload,
    status_code, success, delivered_at
)

-- Filesystem paths to scan for owned audiobooks
library_paths (
    id, path, name, scan_enabled, last_scanned, created_at
)

-- User-configurable settings (JSON blobs keyed by name)
app_settings (
    key, value, updated_at
)

-- Record of every send-to-download-client action
download_attempts (
    id, book_id, book_title, query, release_title,
    indexer, source, type, size_bytes, seeders,
    download_url, status, error_detail, created_at
)
```

---

## Integration Points

### Audiobookshelf
- **Purpose**: Determine `have_it` for each discovered book
- **Method**: REST API (title + author fuzzy match)
- **Required**: `audiobookshelf.url` + `audiobookshelf.token` in config.yaml
- **Called**: Once per book, per scan — serialised to avoid ABS rate limits

### Prowlarr / Jackett
- **Purpose**: Search download indexers for a missing book
- **Required**: `prowlarr.url` + `prowlarr.api_key` in config.yaml
- **Called**: On demand from the UI/API, and by **auto-download** (v0.69.0)
  after each scan for authors with `watchlist.auto_download` enabled

### Auto-download (v0.69.0)
- After a scan, `core/auto_download.py` finds the author's HIGH-confidence,
  released, missing books, searches the indexers, and picks the best result
  under the download preferences (min seeders / max size hard filters,
  preferred format soft filter)
- `auto_download_mode` (download preferences): `"approval"` records the
  match as a *pending* `download_attempt` for one-click approval;
  `"auto"` sends it straight to the download client
- Guardrails: unreleased/undated books never grabbed; queued/pending
  attempts never repeated; 24 h cooldown after failures; co-authored books
  grab under their primary author only

### qBittorrent auto-import poller (v0.69.0)
- `poll_completed_downloads_task` runs on a cron
  (`postprocess.auto_import_interval_minutes`, default 2 min) when
  `postprocess.mode: bookscout` and a qBittorrent client are configured
- Every torrent BookScout sends is tagged `bookscout-<book_id>`; the poller
  lists completed torrents in the configured category, imports each tagged
  one through the normal import pipeline, then tags it `bs-imported`
  (or `bs-failed` — remove that tag in qBittorrent to retry)
- Auth: username/password from config, **or** qBittorrent's
  "bypass authentication for whitelisted IP subnets" (the 204 bypass
  handshake is recognised)

### Primary-author resolution (v0.69.0)
- Co-authored books carry `author_order` (billing position from the source
  metadata) on each `book_authors` row; after every insert/update the
  lowest-order linked author becomes `primary_author_id`
  ("billing order wins"), with deterministic tie-breaks
- A manual choice via `PATCH /books/{id}` sets `primary_author_manual` and
  is never overridden by scans
- Duplicate prevention is backed by partial unique indexes on live rows
  (`asin`, `isbn13`) — a concurrent-scan insert race is recovered by
  re-finding the winner's row and linking to it

### Redis pub/sub events

| Event | Payload |
|-------|---------|
| `scan.complete` | `author_id`, `author_name`, `new_books`, `updated_books`, `discovered[]` |
| `coauthor.discovered` | `author_id`, `author_name`, `coauthors[]`, `auto_added` |
| `import.complete` | `book_id`, `book_title`, `author_name`, `destination`, `files_copied[]` |
| `autodownload.pending` | `book_id`, `book_title`, `author_id`, `author_name`, `release_title` |
| `autodownload.sent` | same as above — best match sent to the download client |
| `autodownload.failed` | same as above — send failed (see download history for detail) |

Events are consumed by webhook subscribers
(`POST /api/v1/webhooks/`) and the `/api/v1/events` SSE stream.

---

## Technology Stack

```
API layer:
├── Python 3.11
├── FastAPI (async REST framework)
└── uvicorn (ASGI server)

Background jobs:
└── arq (Redis-backed async job queue)

Data:
├── PostgreSQL (primary store)
├── SQLAlchemy 2.0 async (ORM)
└── Alembic (schema migrations)

Messaging:
└── Redis (pub/sub event bus + arq queue)

HTTP client:
└── httpx (async)

Deployment:
├── Docker + docker-compose
└── systemd (bare-metal alternative)
```

---

## Network Requirements

**Outbound (BookScout → Internet):**
- openlibrary.org (port 443)
- googleapis.com (port 443) — optional, requires API key
- api.audnex.us (port 443)
- api2.isbndb.com (port 443) — optional, requires API key

**Inbound (client → BookScout):**
- Port 8765 (REST API)

**Local network (BookScout → your services):**
- Audiobookshelf (typically port 13378)
- Prowlarr (typically port 9696)
- PostgreSQL (port 5432)
- Redis (port 6379)

---

## Security Considerations

- Headless core — attack surface limited to the REST API (the UI is a
  separate static SPA)
- Optional bearer-token auth (`server.secret_key`); note bookscout-ui does
  not send bearer tokens yet, so restrict access at the network/proxy layer
  when using the UI
- Download-client credentials live in config.yaml / env — never in
  exported workflows; qBittorrent can run credential-free for BookScout via
  its IP-whitelist auth bypass
- No telemetry or phone-home
- HTTPS strongly recommended via reverse proxy (Nginx Proxy Manager, Caddy)
- Runs on local network — not intended to be internet-exposed

---

## Performance

**Scan time (per author):**
- 3–4 API sources gathered concurrently
- ABS ownership check serialised (rate-limit friendly)
- Typical: 10–30 seconds per author

**Database:**
- ~2–5 KB per book row
- 100 authors × 50 books = ~25 MB
- PostgreSQL handles concurrent scans without locking issues

**Memory:**
- FastAPI + workers: ~100–150 MB
- PostgreSQL: ~50–100 MB
- Redis: ~10–20 MB

---

## Adding a New Metadata Source

1. Add an async function to `core/metadata.py`:
```python
async def query_my_source(
    client: httpx.AsyncClient, author_name: str, ...
) -> list[dict[str, Any]]:
    ...
    return books_list  # each dict: title, isbn13, asin, authors, ...
```

2. Add a source flag to `config.py` `_DEFAULT["scan"]["sources"]`

3. In `core/scan.py` `scan_author()`, read the flag and add the task:
```python
if src_my_source:
    source_tasks.append(query_my_source(client, author_name, ...))
```

`merge_books()` handles deduplication automatically.


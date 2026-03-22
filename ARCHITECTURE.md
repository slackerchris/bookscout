# BookScout Architecture

## System Overview

BookScout is an **async headless REST API service** built on FastAPI.  There
is no web UI — all interaction is via HTTP endpoints documented at `/docs`.

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
    id, name, name_sort, asin, openlibrary_key,
    image_url, bio, active, created_at, updated_at
)

-- Watchlist entries (one per watched author)
watchlist (
    id, author_id, scan_enabled, last_scanned, created_at
)

-- Books discovered from all sources
books (
    id, title, title_sort, subtitle, isbn, isbn13, asin,
    release_date, published_year, format, source,
    cover_url, description, series_name, series_position,
    have_it, score, confidence_band, score_reasons,
    match_method, deleted, created_at, updated_at
)

-- Many-to-many: books ↔ authors, with role
book_authors (
    book_id, author_id,
    role  -- "author" | "co-author"
)
```

---

## Integration Points

### Audiobookshelf
- **Purpose**: Determine `have_it` for each discovered book
- **Method**: REST API (title + author fuzzy match)
- **Required**: `audiobookshelf.url` + `audiobookshelf.token` in config.yaml
- **Called**: Once per book, per scan — serialised to avoid ABS rate limits

### Prowlarr
- **Purpose**: Search download indexers for a missing book
- **Method**: REST API redirect (client-side trigger)
- **Required**: `prowlarr.url` + `prowlarr.api_key` in config.yaml
- **Called**: On demand — BookScout never auto-downloads

### Redis pub/sub events

| Event | Payload |
|-------|---------|
| `scan.complete` | `author_id`, `author_name`, `new_books`, `updated_books`, `discovered[]` |
| `coauthor.discovered` | `author_id`, `author_name`, `coauthors[]`, `auto_added` |

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

- No web UI — attack surface limited to the REST API
- API tokens stored in PostgreSQL (not config files)
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


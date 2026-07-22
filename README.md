# BookScout 📚

Headless audiobook-tracking service. Queries Open Library, Google Books, and the Audible catalog to build complete author bibliographies, scores each book by confidence, checks your Audiobookshelf library for ownership, and delivers notifications via webhooks and SSE.

> **Web UI available** — [bookscout-ui](https://github.com/slackerchris/bookscout-ui) is a React control panel that wraps this API. You can still use the REST API directly; interactive docs live at `/docs` (Swagger UI) and `/redoc`.

---

## Features

- **Multi-source discovery** — Open Library, Google Books, Audnexus, and ISBNdb queried in parallel
- **Confidence scoring** — every book gets a `HIGH / MEDIUM / LOW` band with per-reason breakdown
- **Smart deduplication** — fuzzy author matching handles initials, name variants, and inverted surnames; DB-level uniqueness backstop prevents duplicate books even under concurrent scans
- **Auto-download** (v0.69.0) — per-author opt-in: after each scan, the best indexer match for new HIGH-confidence released books is sent to your download client automatically, or queued for one-click approval
- **Auto-import** (v0.69.0) — built-in qBittorrent poller detects completed BookScout downloads, extracts/organises them into your library, and marks them owned. No external automation needed
- **Series view** (v0.69.0) — `GET /series` groups the catalog by series with per-position ownership and gap detection ("own 1, 2, 4 — position 3 missing from catalog")
- **Primary-author intelligence** (v0.69.0) — co-authored books file under their top-billed author (billing order from the source metadata); manual picks are pinned and never overridden by scans
- **Filesystem scanner** — watches local library paths and cross-references files against catalog results
- **Audiobookshelf integration** — marks books owned vs. missing against your ABS library; one-click bulk sync
- **Prowlarr / Jackett search** — unified indexer search with download routing to qBittorrent, Transmission, or SABnzbd
- **Download history** — every send-to-client action is recorded with release title, type, size, and outcome; pending auto-downloads can be approved or dismissed
- **Metadata editing** — `PATCH /books/{id}` accepts full manual overrides (title, series, narrator, release date, language, identifiers); explicit `null` clears a field
- **Duplicate detection** — `GET /books/duplicates` surfaces books that share a normalised title so cleanup is a one-step operation
- **Export** — `GET /books/export` downloads the full catalog as a JSON file
- **Webhooks + SSE** — push `scan.complete`, `coauthor.discovered`, `import.complete`, and `autodownload.*` events to Discord, n8n, Mafl, etc.
- **Scheduled scans** — cron-driven background worker (arq + Redis) rescans watchlist authors automatically

## Why BookScout?

Readarr and LazyLibrarian have incomplete metadata databases. BookScout solves this by querying multiple sources in parallel, merging and deduplicating the results, and confidence-scoring each book. Example: an author with 30+ audiobooks typically returns 25–30 HIGH-confidence results where most tools show 10–15.

---

## Quick Start

### Docker Compose (recommended)

```bash
git clone https://github.com/slackerchris/bookscout.git
cd bookscout

# Copy the example env file (only POSTGRES_PASSWORD is required)
cp .env.example .env
$EDITOR .env

# Pull images and start all services
docker compose up -d

# Tail logs to confirm everything is healthy
docker compose logs -f bookscout
```

API: **http://localhost:8765**  
Docs: **http://localhost:8765/docs**

### Services started by docker-compose

| Container | Role |
|---|---|
| `bookscout-postgres` | PostgreSQL 16 — primary datastore |
| `bookscout-redis` | Redis 7 — job queue + event bus |
| `bookscout-migrate` | Runs `alembic upgrade head` once, then exits |
| `bookscout` | FastAPI service on port 8765 |
| `bookscout-worker` | arq background worker (scans, webhooks) |

---

## Configuration

Configuration is read from `/data/config.yaml` inside the container (mount a host file there), with environment variable overrides layered on top.

### Minimal config.yaml

```yaml
audiobookshelf:
  url: http://abs:13378
  token: your_abs_api_token

prowlarr:
  url: http://prowlarr:9696
  api_key: your_prowlarr_api_key

scan:
  schedule_cron: "0 * * * *"   # rescan watchlist every hour
  language_filter: en           # ISO 639-1; set to "all" to disable

server:
  secret_key: change-me-in-production   # enables Bearer token auth on all endpoints
```

### Full config.yaml reference

```yaml
database:
  url: postgresql+asyncpg://bookscout:bookscout@postgres/bookscout

redis:
  url: redis://redis:6379

audiobookshelf:
  url: ""
  token: ""

prowlarr:
  url: ""
  api_key: ""

jackett:
  url: ""
  api_key: ""

n8n:
  url: ""        # optional — enables health check in /api/v1/search/status
  api_key: ""    # optional — enables n8n execution history endpoint

apis:
  google_books_key: ""    # optional — raises quota from 100 to 1000 req/day
  isbndb_key: ""          # optional — enables ISBNdb source

download:
  preferred: ""           # "sabnzbd" | "torrent" — used when both are configured
  sabnzbd:
    url: ""
    api_key: ""
    default_category: ""
  torrent:
    type: qbittorrent     # "qbittorrent" | "transmission"
    url: ""
    username: ""
    password: ""
    default_category: ""
    default_tag: ""
    save_path: ""

postprocess:
  mode: client            # "client" = download client handles it
                          # "bookscout" = BookScout extracts and organises files
  library_root: ""        # required when mode = "bookscout"
  # Poll qBittorrent for completed BookScout downloads (tagged bookscout-<id>)
  # and import them automatically. Active when mode = "bookscout" and a
  # qBittorrent client is configured. Successful imports are tagged
  # "bs-imported"; failures "bs-failed" (remove that tag to retry).
  auto_import: true
  auto_import_interval_minutes: 2

scan:
  schedule_cron: "0 * * * *"
  max_concurrent_scans: 5
  language_filter: en     # ISO 639-1 code, or "all" to accept all languages
  cache_ttl_hours: 24
  auto_add_coauthors: false
  sources:
    openlibrary: true
    google_books: true
    audible: true
    isbndb: true          # only active when apis.isbndb_key is set

server:
  host: 0.0.0.0
  port: 8765
  secret_key: change-me-in-production
  cors_origins: ["*"]
```

### Environment variable overrides

All YAML keys can be overridden with environment variables:

| Variable | YAML equivalent | Notes |
|---|---|---|
| `DATABASE_URL` | `database.url` | PostgreSQL async DSN |
| `REDIS_URL` | `redis.url` | Redis DSN |
| `AUDIOBOOKSHELF_URL` | `audiobookshelf.url` | |
| `AUDIOBOOKSHELF_TOKEN` | `audiobookshelf.token` | |
| `PROWLARR_URL` | `prowlarr.url` | |
| `PROWLARR_API_KEY` | `prowlarr.api_key` | |
| `JACKETT_URL` | `jackett.url` | |
| `JACKETT_API_KEY` | `jackett.api_key` | |
| `N8N_URL` | `n8n.url` | |
| `N8N_API_KEY` | `n8n.api_key` | Required for execution history endpoint |
| `GOOGLE_BOOKS_API_KEY` | `apis.google_books_key` | |
| `ISBNDB_API_KEY` | `apis.isbndb_key` | |
| `SECRET_KEY` | `server.secret_key` | Bearer token for API auth |
| `DOWNLOAD_PREFERRED` | `download.preferred` | `sabnzbd` or `torrent` |
| `SABNZBD_URL` | `download.sabnzbd.url` | |
| `SABNZBD_API_KEY` | `download.sabnzbd.api_key` | |
| `SABNZBD_CATEGORY` | `download.sabnzbd.default_category` | |
| `TORRENT_URL` | `download.torrent.url` | |
| `TORRENT_USERNAME` | `download.torrent.username` | |
| `TORRENT_PASSWORD` | `download.torrent.password` | |
| `TORRENT_CATEGORY` | `download.torrent.default_category` | |
| `TORRENT_TAG` | `download.torrent.default_tag` | |
| `TORRENT_SAVE_PATH` | `download.torrent.save_path` | |
| `POSTPROCESS_MODE` | `postprocess.mode` | `bookscout` or `client` |
| `POSTPROCESS_LIBRARY_ROOT` | `postprocess.library_root` | Required when mode = `bookscout` |
| `SCAN_LANGUAGE_FILTER` | `scan.language_filter` | `en` or `all` |
| `SCAN_CACHE_TTL_HOURS` | `scan.cache_ttl_hours` | |

---

## Authentication

When `server.secret_key` is set to anything other than the default placeholder, all endpoints (except `/health`, `/docs`, `/redoc`, `/openapi.json`) require:

```
Authorization: Bearer <your-secret-key>
```

If the key is left at the default, a warning is printed at startup and all endpoints are accessible without credentials.

> **Note:** [bookscout-ui](https://github.com/slackerchris/bookscout-ui) does not send bearer tokens yet. If you use the web UI, leave `secret_key` unset and restrict access at the network layer instead (LAN-only reverse proxy with an access list, VPN, etc.).

---

## API Overview

### Books

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/books` | List books (filterable by author, confidence, owned, `updated_since`) |
| `GET` | `/api/v1/books/count` | Count books — cheap stat-card query |
| `GET` | `/api/v1/books/summary` | Aggregate summary (total, missing, upcoming, etc.) |
| `GET` | `/api/v1/books/recently-imported` | Books imported from the filesystem |
| `GET` | `/api/v1/books/recently-discovered` | Books added by recent scans |
| `GET` | `/api/v1/books/upcoming` | Books with a future release date |
| `GET` | `/api/v1/books/export` | Download full catalog as `bookscout-export.json` |
| `GET` | `/api/v1/books/duplicates` | Find books sharing a normalised title + author |
| `GET` | `/api/v1/books/co-author-conflicts` | Books where 2+ watched authors are credited — pick the true primary |
| `GET` | `/api/v1/books/{id}` | Get single book |
| `PATCH` | `/api/v1/books/{id}` | Update book metadata; setting `primary_author_id` pins it against scan reassignment (`primary_author_manual: false` unpins) |
| `DELETE` | `/api/v1/books/{id}` | Soft-delete (dismiss) a book |
| `POST` | `/api/v1/books/{id}/search` | Search indexers for this book |
| `POST` | `/api/v1/books/{id}/rescan` | Re-queue a metadata scan for the book's author |
| `POST` | `/api/v1/books/{id}/import` | Import a downloaded file into the library (`postprocess.mode: bookscout` only) |

### Authors

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/authors` | List watchlist authors |
| `POST` | `/api/v1/authors` | Add author to watchlist |
| `GET` | `/api/v1/authors/count` | Count authors |
| `GET` | `/api/v1/authors/favorites` | List favourite author IDs |
| `GET` | `/api/v1/authors/unwatched` | Authors imported from ABS but not on the watchlist |
| `GET` | `/api/v1/authors/{id}` | Get author + stats |
| `PATCH` | `/api/v1/authors/{id}` | Update author name / active status / `auto_download` opt-in |
| `DELETE` | `/api/v1/authors/{id}` | Remove from watchlist |
| `POST` | `/api/v1/authors/{id}/watch` | Add existing author to watchlist |
| `POST` | `/api/v1/authors/{id}/favorite` | Mark author as favourite |
| `DELETE` | `/api/v1/authors/{id}/favorite` | Unmark favourite |
| `PATCH` | `/api/v1/authors/{id}/watchlist` | Toggle `scan_enabled` |
| `GET` | `/api/v1/authors/{id}/coauthors` | Co-authors discovered for this author |
| `GET` | `/api/v1/authors/{id}/languages` | Per-language book count breakdown |
| `GET` | `/api/v1/authors/{id}/preferences` | Get notes and ignore rules |
| `PATCH` | `/api/v1/authors/{id}/preferences` | Update notes and ignore rules |
| `GET` | `/api/v1/authors/{id}/aliases` | List all known name aliases |
| `POST` | `/api/v1/authors/{id}/aliases` | Add a name alias |
| `DELETE` | `/api/v1/authors/{id}/aliases/{alias_id}` | Delete an alias |

### Scans

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/scans/author/{id}` | Enqueue scan for one author |
| `POST` | `/api/v1/scans/all` | Enqueue scan for all watchlist authors |
| `GET` | `/api/v1/scans/stats` | Scan statistics (last scan time, new books today, totals) |
| `GET` | `/api/v1/scans/job/{job_id}` | Poll arq job status |

### Search & Downloads

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/search` | Search Prowlarr + Jackett indexers |
| `POST` | `/api/v1/search/download` | Send a result to the configured download client; records a `download_attempt` row |
| `GET` | `/api/v1/search/status` | Check indexer and download client connectivity |
| `GET` | `/api/v1/search/download/queue` | Fetch the active download client queue |
| `GET` | `/api/v1/download-history` | List recent download attempts (`?status=pending` for auto-download approvals) |
| `POST` | `/api/v1/download-history/{id}/approve` | Approve a pending auto-download — sends it to the client |
| `POST` | `/api/v1/download-history/{id}/dismiss` | Dismiss a pending auto-download |
| `DELETE` | `/api/v1/download-history` | Clear all download history |

### Series

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/series` | Series with per-position ownership + gap detection (`?missing_only=true`, `?author_id=`) |

### Settings

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/settings/download-preferences` | Get quality/format preferences (also drive auto-download match selection) |
| `PATCH` | `/api/v1/settings/download-preferences` | Update preferences, incl. `auto_download_mode`: `"approval"` (default) or `"auto"` |

### Webhooks & Events

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/webhooks` | List registered webhook endpoints |
| `POST` | `/api/v1/webhooks` | Register a webhook |
| `DELETE` | `/api/v1/webhooks/{id}` | Deactivate a webhook |
| `POST` | `/api/v1/webhooks/{id}/reactivate` | Re-enable a disabled webhook |
| `POST` | `/api/v1/webhooks/{id}/test` | Send a test ping |
| `GET` | `/api/v1/webhooks/{id}/deliveries` | Delivery log for a webhook |
| `GET` | `/api/v1/events` | SSE stream of live events |

### Audiobookshelf

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/audiobookshelf/import-authors` | Bulk-import all ABS authors into the watchlist |
| `POST` | `/api/v1/audiobookshelf/sync-books` | Import all ABS books + enqueue metadata scans |
| `GET` | `/api/v1/abs/status` | Audiobookshelf connection status |

### Other

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/api/v1/library-paths` | List filesystem library paths |
| `POST` | `/api/v1/library-paths` | Register a library path |
| `POST` | `/api/v1/library-paths/{id}/scan` | Trigger a filesystem scan |
| `GET` | `/api/v1/n8n/executions` | Proxy n8n execution history for a workflow |

Full interactive docs with request/response schemas: **http://localhost:8765/docs**

---

## Homelab Integration

```
Audiobookshelf ──────► BookScout API ◄──── BookScout UI (bookscout-ui)
                              │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
          Prowlarr        Webhooks         SSE
        (search missing)  (Discord / n8n)  (Mafl / dashboard)
               │
          qBittorrent / Transmission / SABnzbd
        (send downloads)
               │
        auto-import poller (worker)
        (completed torrent → extract → library → owned)
```

- Webhooks fire on `scan.complete`, `coauthor.discovered`, `import.complete`, and `autodownload.pending|sent|failed`
- SSE feed (`/api/v1/events`) streams real-time scan and import progress to any connected client
- The full hands-free loop: scan discovers a new HIGH-confidence book → auto-download grabs the best match → qBittorrent finishes → the poller imports it into your library and marks it owned. No external automation (n8n, scripts) required.

---

## Updating

```bash
docker compose pull
docker compose up -d
```

Migrations run automatically on startup via the `migrate` service.

---

## Data Sources

| Source | Notes |
|---|---|
| Open Library | Internet Archive — comprehensive, no key required |
| Google Books | Good series metadata; optional key raises daily quota |
| Audnexus | Audible catalog metadata — best audiobook coverage |
| ISBNdb | ISBN-based lookup; requires a paid API key |

---

## Privacy

BookScout runs entirely on your infrastructure. It makes outbound calls only to the above public book catalog APIs. No telemetry, no tracking, no external data persistence.

---

## License

Use freely. No warranty — use at your own risk.

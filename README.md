# BookScout 📚

Headless audiobook-tracking service. Queries Open Library, Google Books, and the Audible catalog to build complete author bibliographies, scores each book by confidence, checks your Audiobookshelf library for ownership, and delivers notifications via webhooks and SSE.

~~There is **no web UI** — interaction is entirely through the REST API. Interactive docs are served at `/docs` (Swagger UI) and `/redoc`.~~
**Announcing Bookscout UI** 
https://github.com/slackerchris/bookscout-ui

You can still use the REST API. Interactive docs are served at `/docs` (Swagger UI) and `/redoc`  if do not wish to install/use *Bookscout UI*

## Features

- **Multi-source discovery** — Open Library, Google Books, and the Audible catalog API queried in parallel
- **Confidence scoring** — every book gets a `HIGH / MEDIUM / LOW` band with per-reason breakdown
- **Smart deduplication** — fuzzy author matching handles initials, name variants, and inverted surnames
- **Filesystem scanner** — watches local library paths and cross-references files against catalog results
- **Audiobookshelf integration** — marks books owned vs. missing against your ABS library
- **Prowlarr / Jackett search** — one-call API to open a search for any missing book
- **Webhooks + SSE** — push `scan.complete`, `coauthor.discovered`, and custom events to n8n, Discord, Mafl, etc.
- **Scheduled scans** — cron-driven background worker (arq + Redis) rescans watchlist authors automatically

## Why BookScout?

Readarr and LazyLibrarian have incomplete metadata databases. BookScout solves this by querying multiple sources in parallel, merging and deduplicating the results, and confidence-scoring each book. Example: an author with 30+ audiobooks typically returns 25–30 HIGH-confidence results where most tools show 10–15.

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

API is available at **http://localhost:8765**
Interactive docs at **http://localhost:8765/docs**

### Services started by docker-compose

| Container | Role |
|---|---|
| `bookscout-postgres` | PostgreSQL 16 — primary datastore |
| `bookscout-redis` | Redis 7 — job queue + event bus |
| `bookscout-migrate` | Runs `alembic upgrade head` once, then exits |
| `bookscout` | FastAPI service on port 8765 |
| `bookscout-worker` | arq background worker (scans, webhooks) |

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
  url: ""    # optional — enables health check in /api/v1/search/status

apis:
  google_books_key: ""    # optional — raises quota from 100 to 1000 req/day
  isbndb_key: ""          # optional — enables ISBNdb source

scan:
  schedule_cron: "0 * * * *"
  max_concurrent_scans: 5
  language_filter: en     # ISO 639-1 code, or "all" to accept all languages

server:
  host: 0.0.0.0
  port: 8765
  secret_key: change-me-in-production
```

All YAML keys can be overridden with environment variables: `DATABASE_URL`, `REDIS_URL`, `AUDIOBOOKSHELF_URL`, `AUDIOBOOKSHELF_TOKEN`, `PROWLARR_URL`, `PROWLARR_API_KEY`, `JACKETT_URL`, `JACKETT_API_KEY`, `N8N_URL`, `GOOGLE_BOOKS_API_KEY`, `ISBNDB_API_KEY`, `SECRET_KEY`.

If Prowlarr has both Usenet and torrent indexers configured, BookScout search
can return a mixed audiobook result set. `POST /api/v1/search/download` routes
NZB results to SABnzbd and torrent results to the configured torrent client.
This means a hybrid setup does not need a single "all traffic goes here"
download preference; routing is determined by the selected result type.
`GET /api/v1/search/download/queue` also returns a combined queue when both
SABnzbd and a torrent client are configured.

## API Overview

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/api/v1/authors` | List watchlist authors |
| `POST` | `/api/v1/authors` | Add author to watchlist |
| `DELETE` | `/api/v1/authors/{id}` | Remove author |
| `GET` | `/api/v1/authors/{id}/coauthors` | Co-authors discovered for an author |
| `GET` | `/api/v1/authors/{id}/languages` | Per-language book count breakdown |
| `GET` | `/api/v1/books` | List books (filterable by author, confidence, owned, `updated_since`) |
| `GET` | `/api/v1/books/count` | Count books matching filter criteria (no payload — cheap stat-card query) |
| `PATCH` | `/api/v1/books/{id}` | Update book metadata |
| `POST` | `/api/v1/books/{id}/search` | Search indexers for a specific book |
| `POST` | `/api/v1/scans/author/{id}` | Enqueue scan for one author |
| `POST` | `/api/v1/scans/all` | Enqueue scan for all watchlist authors |
| `GET` | `/api/v1/scans/stats` | Scan statistics (last scan time, new books today, totals) |
| `POST` | `/api/v1/search` | Search Prowlarr + Jackett indexers |
| `POST` | `/api/v1/search/download` | Send an indexer result to your download client |
| `GET` | `/api/v1/search/status` | Check indexer, download client, and n8n connectivity |
| `GET` | `/api/v1/library-paths` | List filesystem library paths |
| `POST` | `/api/v1/library-paths` | Register a new library path |
| `POST` | `/api/v1/library-paths/{id}/scan` | Trigger filesystem scan |
| `GET` | `/api/v1/events` | SSE stream of live events |
| `GET` | `/api/v1/webhooks` | List registered webhook consumers |
| `POST` | `/api/v1/webhooks` | Register a webhook endpoint |
| `GET` | `/api/v1/abs/status` | Audiobookshelf connection status |

Full interactive docs with request/response schemas: **http://localhost:8765/docs**

## Homelab Integration

```
Audiobookshelf ──────► BookScout API
                              │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
          Prowlarr        Webhooks         SSE
        (search missing)  (n8n / Discord)  (Mafl dashboard)
```

- Register library paths so BookScout can cross-reference your actual files
- Webhooks fire on `scan.complete`, `coauthor.discovered`
- SSE feed (`/api/v1/events`) streams real-time scan progress

## Updating

```bash
docker compose pull
docker compose up -d
```

Migrations run automatically on startup via the `migrate` service.

## Data Sources

| Source | Notes |
|---|---|
| Open Library | Internet Archive — comprehensive, no key required |
| Google Books | Good series metadata; optional key raises daily quota |
| Audible catalog | Production Audible API — best audiobook coverage |

## Privacy

BookScout runs entirely on your infrastructure. It makes outbound calls only to the above public book catalog APIs. No telemetry, no tracking, no external data persistence.

## License

Use freely. No warranty — use at your own risk.

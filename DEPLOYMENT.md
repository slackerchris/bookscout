# BookScout — Deployment Guide

BookScout is a **headless FastAPI service**; interactive API documentation is available at `/docs`. The optional [bookscout-ui](https://github.com/slackerchris/bookscout-ui) web control panel ships as the `bookscout-ui` service in the compose file (port 8282).

---

## Prerequisites

- Docker 24+ and Docker Compose v2+
- A persistent data directory for `config.yaml` and PostgreSQL/Redis volumes
- Network access to your Audiobookshelf and Prowlarr instances from the BookScout container

---

## 1. Create config.yaml

Create a file at a path you control (e.g. `/opt/bookscout/config.yaml`):

```yaml
audiobookshelf:
  url: http://abs:13378        # URL reachable from inside the container
  token: your_abs_api_token    # ABS: Settings → Users → [user] → API Token

prowlarr:
  url: http://prowlarr:9696
  api_key: your_prowlarr_api_key

scan:
  schedule_cron: "0 * * * *"  # rescan watchlist authors hourly
  language_filter: en          # ISO 639-1; set to "all" to disable

# Optional API keys
apis:
  google_books_key: ""         # raises daily quota from 100 to 1000 req/day
  isbndb_key: ""

server:
  secret_key: change-me-in-production
```

---

## 2. Docker Compose Deployment

Copy the `docker-compose.yml` from the repository and set one environment variable:

```bash
# .env  (next to docker-compose.yml) — see .env.example for the full set
POSTGRES_PASSWORD=a-strong-password   # letters+digits only: it is spliced into a URL
```

> ⚠ `.env` gotchas: a `#` in an unquoted value silently truncates it; wrap
> values containing `#`, spaces, or `$` in single quotes. Portainer stacks
> do **not** read `.env` — use the stack's Environment variables section.

Mount your `config.yaml` into the container:

```yaml
# Relevant section of docker-compose.yml — add in the bookscout and worker services:
volumes:
  - /opt/bookscout/config.yaml:/data/config.yaml:ro
  - bookscout-data:/data
```

Start all services:

```bash
docker compose up -d
docker compose logs -f bookscout
```

Expected startup output:

```
bookscout-migrate exited with code 0
bookscout  | [bookscout] started — API docs at /docs
```

**Service map:**

| Container | Image | Role |
|---|---|---|
| `bookscout-postgres` | `postgres:16-alpine` | Primary datastore |
| `bookscout-redis` | `redis:7-alpine` | Job queue + event bus |
| `bookscout-migrate` | `ghcr.io/slackerchris/bookscout` | One-shot Alembic migration |
| `bookscout` | `ghcr.io/slackerchris/bookscout` | FastAPI on port 8765 |
| `bookscout-worker` | `ghcr.io/slackerchris/bookscout` | arq worker — scans, auto-download, qBittorrent auto-import |
| `bookscout-ui` | `ghcr.io/slackerchris/bookscout-ui` | Web control panel on port 8282 |

---

## 3. Verify the Deployment

```bash
# Health probe
curl http://localhost:8765/health
# {"status":"ok","version":"0.41.4"}

# Open interactive docs
open http://localhost:8765/docs
```

---

## 4. Initial Setup via API

### Add your first author

```bash
curl -s -X POST http://localhost:8765/api/v1/authors \
  -H "Content-Type: application/json" \
  -d '{"name": "J.N. Chaney"}' | jq
```

### Trigger a scan

```bash
# Replace 1 with the id returned above
curl -s -X POST http://localhost:8765/api/v1/scans/author/1 | jq
```

### Check scan results

```bash
curl -s "http://localhost:8765/api/v1/books?author_id=1&confidence=HIGH" | jq
```

### Register a filesystem library path

```bash
curl -s -X POST http://localhost:8765/api/v1/library-paths \
  -H "Content-Type: application/json" \
  -d '{"path": "/mnt/audiobooks", "name": "NAS audiobooks"}' | jq

# Trigger a filesystem scan
curl -s -X POST http://localhost:8765/api/v1/library-paths/1/scan | jq
```

---

## 5. Webhook Setup

Register a consumer to receive `book.missing` and `scan.complete` events:

```bash
curl -s -X POST http://localhost:8765/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://n8n:5678/webhook/bookscout",
    "description": "n8n automation",
    "events": ["book.missing", "scan.complete"]
  }' | jq
```

Webhook payloads are delivered as `POST` with a JSON body. Failed deliveries are logged in the `webhook_deliveries` table.

### SSE stream

Connect any client to the live event stream:

```bash
curl -N http://localhost:8765/api/v1/events
```

---

## 6. Audiobookshelf Integration

BookScout calls the ABS API to check which books are already in your library. The `token` in `config.yaml` must be an ABS user token with library read access.

To verify the connection:

```bash
curl -s http://localhost:8765/api/v1/abs/status | jq
```

---

## 7. Updating

```bash
docker compose pull
docker compose up -d
```

The `migrate` service runs `alembic upgrade head` automatically on every `up`, so schema migrations are applied without manual intervention.

---

## 8. Reverse Proxy (Nginx Proxy Manager / Traefik)

BookScout exposes only port 8765. Add an NPM / Traefik proxy pass to put it behind TLS:

```
https://bookscout.home.yourdomain.com → http://bookscout:8765
```

No additional configuration is required inside BookScout — it trusts the `X-Forwarded-*` headers by default.

---

## 9. systemd (bare-metal alternative)

If you run BookScout outside Docker, **two units are required** — the API alone never executes scans or imports:

- `bookscout.service` — the API (runs `alembic upgrade head` on start)
- `bookscout-worker.service` — the arq worker (scans, auto-download, auto-import)

Both templates are in the repository; update the paths/user in each. Database and Redis must be provided externally; set `DATABASE_URL` and `REDIS_URL` in the environment file.

---

## 10. Troubleshooting

### Containers won't start

```bash
docker compose ps          # check which services are unhealthy
docker compose logs migrate # check if Alembic migration failed
docker compose logs postgres
```

### `bookscout-migrate` exits non-zero

Check the actual traceback — the last lines name the failing statement:

```bash
docker compose logs migrate | tail -20
```

Common causes, by error message:
- `Name or service not known` — the hostname in `DATABASE_URL` doesn't match the postgres **service key** in the compose file (the YAML key *is* the DNS name), or Docker's embedded DNS is broken on the host (common with snap-installed Docker or LXCs without `nesting=1`; static container IPs work around it)
- `password authentication failed` — a restored `postgres-data` volume keeps its **original** password; the env var only applies on first initialisation. Fix with `ALTER USER bookscout WITH PASSWORD '...'` inside the postgres container
- a migration traceback — file an issue with the log tail

### Books not matching Audiobookshelf

- Confirm `audiobookshelf.url` is reachable from inside the container (`docker exec bookscout curl $ABS_URL`)
- Regen the ABS API token and update `config.yaml`
- Run a re-scan: `POST /api/v1/scans/author/{id}`

### Scan returns 0 books

- Check `language_filter` in `config.yaml` — if set to `en` but the author's catalog is catalogued in another language, change to `all`
- Google Books returns 429 intermittently — this is normal; Open Library and Audible catalog results are still used

### Worker not processing jobs

```bash
docker compose logs worker
# look for "arq: starting worker" and task execution lines
```

If Redis is unavailable the worker will exit — check `bookscout-redis` health.

### qBittorrent shows "Authentication failed" / IP banned

- Wrong password → qBittorrent bans the source IP after ~5 failed logins; restart qBittorrent to clear the ban **after** fixing the credential
- Recommended for a dedicated BookScout host: qBittorrent → Options → Web UI → *Bypass authentication for clients in whitelisted IP subnets* → `your-bookscout-ip/32`. BookScout understands the bypass handshake (v0.69.0+) and the password becomes unnecessary on that path
- The auto-import poller only processes completed torrents in `TORRENT_CATEGORY` that carry a `bookscout-<id>` tag (stamped automatically when BookScout sends the torrent)

---

## 11. Backup and Restore

### Backup

```bash
# PostgreSQL dump
docker exec bookscout-postgres pg_dump -U bookscout bookscout > bookscout-$(date +%F).sql

# config.yaml
cp /opt/bookscout/config.yaml /opt/bookscout/backups/config-$(date +%F).yaml
```

### Restore

```bash
docker exec -i bookscout-postgres psql -U bookscout bookscout < bookscout-YYYY-MM-DD.sql
```

---

## File Structure

```
bookscout/
├── main.py                     ← FastAPI entry point (uvicorn main:app)
├── config.py                   ← config.yaml loader with env-var overrides
├── confidence.py               ← Confidence scoring engine
├── VERSION                     ← Current release version
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── bookscout.service           ← systemd template (API)
├── bookscout-worker.service    ← systemd template (worker — required)
├── api/v1/                     ← Route handlers
│   ├── authors.py
│   ├── books.py                ← includes /export, /duplicates, /{id}/rescan
│   ├── scans.py
│   ├── events.py               ← SSE stream
│   ├── webhooks.py
│   ├── search.py               ← Prowlarr + Jackett; records download_attempts
│   ├── download_history.py     ← GET/POST/DELETE /download-history/
│   ├── settings.py             ← GET/PATCH /settings/download-preferences
│   ├── abs.py
│   ├── library_paths.py
│   ├── series.py               ← GET /series — ownership + gap view
│   ├── n8n.py
│   └── health.py
├── core/
│   ├── metadata.py             ← Multi-source catalog queries
│   ├── merge.py                ← Deduplication logic
│   ├── normalize.py            ← Author name normalisation + matching
│   ├── scan.py                 ← Scan pipeline orchestrator
│   ├── scanner.py              ← Filesystem scanner
│   ├── audiobookshelf.py       ← ABS API client
│   ├── importer.py             ← Post-download file organiser
│   ├── qbittorrent.py          ← Completed-download poller (auto-import)
│   ├── auto_download.py        ← Auto-download rules + best-match selection
│   └── search.py               ← Prowlarr / Jackett / download client helpers
├── db/
│   ├── models.py               ← SQLAlchemy async models
│   ├── session.py              ← Async session factory
│   └── migrations/             ← Alembic migration scripts
│       └── versions/
│           ├── 0001_initial_schema.py
│           ├── 0002_deduplicate_books.py
│           ├── 0003_author_aliases.py
│           ├── 0004_webhook_retry.py
│           ├── 0005_book_language.py
│           ├── 0006_author_name_normalized.py
│           ├── 0007_book_narrator.py
│           ├── 0008_author_favorites.py
│           ├── 0009_app_settings.py
│           ├── 0010_download_history.py
│           └── ... (0011–0015: primary author, identifier uniqueness, auto-download)
└── workers/
    ├── settings.py             ← arq WorkerSettings
    └── tasks.py                ← scan, import, and poll_completed_downloads tasks
```

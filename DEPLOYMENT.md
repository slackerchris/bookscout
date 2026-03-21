# BookScout — Deployment Guide

BookScout is a **headless FastAPI service** with no web UI. Interaction is via the REST API; interactive documentation is available at `/docs`.

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
# .env  (next to docker-compose.yml)
POSTGRES_PASSWORD=a-strong-password
```

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
| `bookscout` | `ghcr.io/slackerchris/bookscout` | FastAPI on port 8000 |
| `bookscout-worker` | `ghcr.io/slackerchris/bookscout` | arq background scanner |

---

## 3. Verify the Deployment

```bash
# Health probe
curl http://localhost:8000/health
# {"status":"ok","version":"0.40.0"}

# Open interactive docs
open http://localhost:8000/docs
```

---

## 4. Initial Setup via API

### Add your first author

```bash
curl -s -X POST http://localhost:8000/api/v1/authors \
  -H "Content-Type: application/json" \
  -d '{"name": "J.N. Chaney"}' | jq
```

### Trigger a scan

```bash
# Replace 1 with the id returned above
curl -s -X POST http://localhost:8000/api/v1/scans/author/1 | jq
```

### Check scan results

```bash
curl -s "http://localhost:8000/api/v1/books?author_id=1&confidence=HIGH" | jq
```

### Register a filesystem library path

```bash
curl -s -X POST http://localhost:8000/api/v1/library-paths \
  -H "Content-Type: application/json" \
  -d '{"path": "/mnt/audiobooks", "name": "NAS audiobooks"}' | jq

# Trigger a filesystem scan
curl -s -X POST http://localhost:8000/api/v1/library-paths/1/scan | jq
```

---

## 5. Webhook Setup

Register a consumer to receive `book.missing` and `scan.complete` events:

```bash
curl -s -X POST http://localhost:8000/api/v1/webhooks \
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
curl -N http://localhost:8000/api/v1/events
```

---

## 6. Audiobookshelf Integration

BookScout calls the ABS API to check which books are already in your library. The `token` in `config.yaml` must be an ABS user token with library read access.

To verify the connection:

```bash
curl -s http://localhost:8000/api/v1/abs/status | jq
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

BookScout exposes only port 8000. Add an NPM / Traefik proxy pass to put it behind TLS:

```
https://bookscout.home.yourdomain.com → http://bookscout:8000
```

No additional configuration is required inside BookScout — it trusts the `X-Forwarded-*` headers by default.

---

## 9. systemd (bare-metal alternative)

If you run BookScout outside Docker, use the included `bookscout.service` as a template. Update the `ExecStart` and `EnvironmentFile` lines:

```ini
[Service]
ExecStart=/usr/local/bin/uvicorn main:app --host 0.0.0.0 --port 8000
WorkingDirectory=/opt/bookscout
EnvironmentFile=/opt/bookscout/.env
```

Database and Redis must be provided externally; set `DATABASE_URL` and `REDIS_URL` in the environment file.

---

## 10. Troubleshooting

### Containers won't start

```bash
docker compose ps          # check which services are unhealthy
docker compose logs migrate # check if Alembic migration failed
docker compose logs postgres
```

### `bookscout-migrate` exits non-zero

Usually a `DATABASE_URL` mismatch or the postgres service not healthy yet. Check:

```bash
docker compose logs migrate | tail -20
```

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
├── main.py                  ← FastAPI entry point (uvicorn main:app)
├── config.py                ← config.yaml loader with env-var overrides
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── bookscout.service        ← systemd template
├── api/v1/                  ← Route handlers
│   ├── authors.py
│   ├── books.py
│   ├── scans.py
│   ├── events.py            ← SSE stream
│   ├── webhooks.py
│   ├── search.py
│   ├── abs.py
│   ├── library_paths.py
│   └── health.py
├── core/
│   ├── metadata.py          ← Multi-source catalog queries
│   ├── merge.py             ← Deduplication logic
│   ├── normalize.py         ← Author name normalisation + matching
│   ├── confidence.py        ← Confidence scoring engine
│   └── scanner.py           ← Filesystem scanner
├── db/
│   ├── models.py            ← SQLAlchemy async models
│   ├── session.py           ← Async session factory
│   └── alembic/             ← Migration scripts
└── workers/
    ├── settings.py          ← arq WorkerSettings
    └── tasks.py             ← scan_author_task, scan_all_authors_task, …
```

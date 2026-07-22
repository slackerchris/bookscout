# BookScout — Quick Start

BookScout is a **headless REST API service**.  Browse every endpoint
interactively at **http://localhost:8765/docs** once the service is running.

> Prefer a web UI?  [bookscout-ui](https://github.com/slackerchris/bookscout-ui)
> is a React control panel for this API (included as the `bookscout-ui`
> service in the compose file, port 8282).

---

## Requirements

- Docker + Docker Compose *(recommended)*, **or** Python 3.11+ with a running
  PostgreSQL instance and Redis

---

## Method 1: Docker Compose (Recommended)

```bash
# 1. Create your env file (credentials, integration URLs)
cp .env.example .env                 # read the formatting warnings at the top!

# 2. Start everything
docker compose up -d
```

Containers started:
- **bookscout** — FastAPI service on port **8765**
- **bookscout-ui** — web control panel on port **8282**
- **postgres** — PostgreSQL database
- **redis** — Redis instance (job queue + event bus)
- **bookscout-migrate** — runs `alembic upgrade head` once, then exits
- **bookscout-worker** — arq background worker (scans, auto-download, auto-import)

API: **http://localhost:8765**  
Interactive docs: **http://localhost:8765/docs**

### Minimal config.yaml

```yaml
audiobookshelf:
  url: "http://your-abs-server:13378"
  token: "your-abs-api-token"
```

That is all you need. PostgreSQL, Redis, port, and source settings all have
working defaults.

---

## Method 2: Bare-metal (Python)

```bash
pip install -r requirements.txt
# PostgreSQL and Redis must already be running — see DEPLOYMENT.md
uvicorn main:app --host 0.0.0.0 --port 8765
```

Set `DATABASE_URL` and `REDIS_URL` environment variables, or configure them
in `config.yaml`.

---

## First Steps (5 minutes)

### 1. Confirm the service is healthy

```bash
curl http://localhost:8765/health
# {"status":"ok","version":"0.41.0",
#  "components":{"database":"ok","redis":"ok"}}
```

If `status` is `"degraded"` check `docker compose logs` for the failing component.

### 2. Add an author to your watchlist

```bash
curl -X POST http://localhost:8765/api/v1/authors/ \
     -H "Content-Type: application/json" \
     -d '{"name": "J.N. Chaney"}'
# {"id": 1, "name": "J.N. Chaney", "active": true, ...}
```

### 3. Trigger a scan

```bash
curl -X POST http://localhost:8765/api/v1/scans/author/1
# {"job_id": "abc123", "status": "queued"}
```

The scan runs in the background via the arq worker.  Wait a few seconds, then:

```bash
curl "http://localhost:8765/api/v1/books/?author_id=1"
```

Each book has:
- `have_it` — `true` when found in your Audiobookshelf library
- `confidence_band` — `high` / `medium` / `low`
- `score` — numeric relevance score
- `series_name` / `series_position` — series info when available

### 4. Scan all watched authors at once

```bash
curl -X POST http://localhost:8765/api/v1/scans/all
```

### 5. Optional: turn on the hands-free pipeline

```bash
# Auto-download: after each scan, grab the best indexer match for this
# author's new HIGH-confidence released books (requires Prowlarr/Jackett
# and a download client in your .env)
curl -X PATCH http://localhost:8765/api/v1/authors/1 \
     -H "Content-Type: application/json" \
     -d '{"auto_download": true}'

# Default mode queues finds for approval — list and approve them:
curl "http://localhost:8765/api/v1/download-history/?status=pending"
curl -X POST http://localhost:8765/api/v1/download-history/1/approve
```

With `POSTPROCESS_MODE=bookscout`, completed qBittorrent downloads are then
imported into your library automatically — see the auto-import section in
the [README](README.md).

### 6. See your series gaps

```bash
curl "http://localhost:8765/api/v1/series/?missing_only=true"
```

---

## Key API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health (DB + Redis) |
| `GET` | `/docs` | Interactive Swagger UI |
| `GET` | `/api/v1/authors/` | List watched authors |
| `POST` | `/api/v1/authors/` | Add author to watchlist |
| `DELETE` | `/api/v1/authors/{id}` | Remove author (soft-delete) |
| `GET` | `/api/v1/authors/{id}/coauthors` | Co-authors + watchlist status |
| `POST` | `/api/v1/scans/{id}` | Scan one author |
| `POST` | `/api/v1/scans/all` | Scan all active authors |
| `GET` | `/api/v1/books/` | List books (`?author_id=`, `?have_it=false`) |
| `GET` | `/api/v1/events` | SSE stream of real-time scan events |

---

## Docker Commands

```bash
# View logs
docker compose logs -f bookscout
docker compose logs -f worker

# Stop everything
docker compose down

# Restart after config.yaml change
docker compose restart bookscout

# Rebuild after a code update
docker compose down
docker compose build
docker compose up -d
```

---

## Troubleshooting

**`"status": "degraded"` in /health**  
→ Check `docker compose logs postgres` and `docker compose logs redis`.

**Scan queued but books never appear**  
→ The arq worker may be down.  Check `docker compose logs worker`.

**`have_it` is always false**  
→ Verify `audiobookshelf.url` and `audiobookshelf.token` in `config.yaml`.

**Too few books returned**  
→ Add a Google Books API key under `apis.google_books_key` in `config.yaml`
for better coverage.  Enable/disable individual sources under `scan.sources`.

---

For full setup details, reverse-proxy configuration, and webhooks see
[DEPLOYMENT.md](DEPLOYMENT.md).

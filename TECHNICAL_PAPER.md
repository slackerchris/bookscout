# BookScout: Historical Architecture (v0.29.4)

> **Archival appendix.**  This document preserves a snapshot of the
> v0.29.4 design (November 2025) for historical reference.  It describes
> the now-retired Flask / SQLite architecture.
>
> **For the current design see [ARCHITECTURE.md](ARCHITECTURE.md).**

---

## Original stack

| Layer | Technology |
|-------|-----------|
| Web framework | Flask 3.0 (Python 3.11) |
| Database | SQLite 3 |
| Deployment | Docker, single container, port 5001 |
| UI | Server-rendered templates |

## Metadata sources (unchanged)

| Source | Notes |
|--------|-------|
| OpenLibrary | Primary ISBN source, up to 200 results/author |
| Google Books | Paginated (120 results max), descriptions, page counts |
| Audnexus | Audiobook ASINs, narrators, co-author arrays |
| ISBNdb | Optional paid API for ISBN enrichment |

## v0.29.4 database schema

```sql
authors (id, name, openlibrary_id, audible_id, last_scanned, active)
books   (id, author_id, title, isbn, isbn13, asin, co_authors JSON,
         series_name, series_position, year, source, scanned_at, owned, deleted)
```

## Key limitations that drove the v0.40+ rewrite

1. **Single-threaded Flask** — scanning was synchronous; long API sweeps blocked the server.
2. **SQLite** — no concurrent writes; unsuitable for background workers.
3. **Flat co-author storage** — JSON blob instead of a many-to-many relation caused duplicate authors.
4. **No background job queue** — scans ran request-inline; timeouts and retries were fragile.
5. **Monolithic `app.py`** — routing, scanning, merging, and API calls all in one file.

These issues were resolved in v0.40.0 by migrating to FastAPI + async
SQLAlchemy (PostgreSQL), Redis pub/sub, and arq for background task processing.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the full current design.

---

*Last revision of the original paper: November 22, 2025.*

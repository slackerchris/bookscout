"""BookScout FastAPI application entry-point.

Start with:
    uvicorn main:app --host 0.0.0.0 --port 8000

Interactive API docs are available at  /docs  (Swagger UI)
                                and at  /redoc (ReDoc)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup / shutdown."""
    from config import load_config

    config = load_config()
    application.state.config = config

    # ── Redis (pub/sub + event bus) ──────────────────────────────────────────
    import redis.asyncio as aioredis

    redis_url = getattr(getattr(config, "redis", None), "url", "redis://redis:6379")
    redis_client = aioredis.from_url(redis_url, decode_responses=False)
    application.state.redis = redis_client

    # ── arq job pool ─────────────────────────────────────────────────────────
    from arq import create_pool
    from arq.connections import RedisSettings

    arq_pool = await create_pool(RedisSettings.from_dsn(redis_url))
    application.state.arq_pool = arq_pool

    logger.info("BookScout started", extra={"docs": "/docs"})

    yield

    # Cleanup
    await arq_pool.aclose()
    await redis_client.aclose()
    logger.info("BookScout shutdown complete")


app = FastAPI(
    title="BookScout",
    description=(
        "Headless audiobook-tracking service.  "
        "Scans OpenLibrary, Google Books, Audnexus, and ISBNdb for new releases; "
        "checks your Audiobookshelf library for ownership; "
        "delivers notifications via SSE and webhooks."
    ),
    version="0.41.1",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount routers ────────────────────────────────────────────────────────────
from api.v1.authors import router as authors_router
from api.v1.books import router as books_router
from api.v1.scans import router as scans_router
from api.v1.events import router as events_router
from api.v1.webhooks import router as webhooks_router
from api.v1.health import router as health_router
from api.v1.search import router as search_router
from api.v1.abs import router as abs_router
from api.v1.library_paths import router as library_paths_router

PREFIX = "/api/v1"

app.include_router(health_router)               # /health  (no prefix — easy for probes)
app.include_router(authors_router, prefix=PREFIX)
app.include_router(books_router, prefix=PREFIX)
app.include_router(scans_router, prefix=PREFIX)
app.include_router(events_router, prefix=PREFIX)
app.include_router(webhooks_router, prefix=PREFIX)
app.include_router(search_router, prefix=PREFIX)
app.include_router(abs_router, prefix=PREFIX)
app.include_router(library_paths_router, prefix=PREFIX)

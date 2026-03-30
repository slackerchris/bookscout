"""BookScout FastAPI application entry-point.

Start with:
    uvicorn main:app --host 0.0.0.0 --port 8765

Interactive API docs are available at  /docs  (Swagger UI)
                                and at  /redoc (ReDoc)
"""
from __future__ import annotations

import logging
import pathlib
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

_VERSION = (pathlib.Path(__file__).parent / "VERSION").read_text().strip()
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_config
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
    version=_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

_cors_origins: list[str] = getattr(
    getattr(get_config(), "server", None), "cors_origins", None
) or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Optional bearer-token authentication ─────────────────────────────────────
# When server.secret_key is set to something other than the default placeholder,
# all endpoints except /health and /docs|/redoc|/openapi.json require a valid
# Authorization: Bearer <token> header.

_DEFAULT_SECRET = "bookscout-secret-key-change-in-production"
_PUBLIC_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        config = getattr(getattr(request.app, "state", None), "config", None)
        secret = getattr(getattr(config, "server", None), "secret_key", _DEFAULT_SECRET)
        # Skip auth when secret is unset or still the default placeholder
        if not secret or secret == _DEFAULT_SECRET:
            return await call_next(request)
        # Always allow public endpoints
        if any(request.url.path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth == f"Bearer {secret}":
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid bearer token"})


app.add_middleware(BearerTokenMiddleware)

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
from api.v1.n8n import router as n8n_router

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
app.include_router(n8n_router, prefix=PREFIX)

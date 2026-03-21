"""Service health check."""
from __future__ import annotations

import pathlib

from fastapi import APIRouter, Request
from sqlalchemy import text

from db.session import AsyncSessionFactory

router = APIRouter(tags=["health"])

_VERSION = (
    pathlib.Path(__file__).parent.parent.parent / "VERSION"
).read_text().strip()


@router.get("/health", summary="Liveness + readiness check")
async def health(request: Request) -> dict:
    db_ok = False
    redis_ok = False

    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    try:
        redis = getattr(request.app.state, "redis", None)
        if redis:
            await redis.ping()
            redis_ok = True
    except Exception:
        pass

    status = "ok" if (db_ok and redis_ok) else "degraded"
    return {
        "status": status,
        "version": _VERSION,
        "components": {
            "database": "ok" if db_ok else "error",
            "redis": "ok" if redis_ok else "error",
        },
    }

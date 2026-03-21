"""Service health check."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from db.session import AsyncSessionFactory

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness + readiness check")
async def health() -> dict:
    db_ok = False
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if db_ok else "degraded",
        "components": {
            "database": "ok" if db_ok else "error",
        },
    }

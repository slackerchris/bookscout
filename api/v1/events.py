"""Server-Sent Events stream.

Clients subscribe with a plain ``GET /api/v1/events`` request.  Each
``scan.complete`` (or other) event published to the ``bookscout:events``
Redis channel is forwarded as an SSE ``data:`` line.

A heartbeat comment is sent every ~30 seconds to keep the connection alive
through proxies.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", summary="SSE real-time event stream")
async def event_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        _generate(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _generate(request: Request) -> AsyncGenerator[str, None]:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        yield "data: {\"error\": \"Redis unavailable\"}\n\n"
        return

    pubsub = redis.pubsub()
    await pubsub.subscribe("bookscout:events")

    last_heartbeat = asyncio.get_event_loop().time()

    try:
        while True:
            # Client disconnect detection
            if await request.is_disconnected():
                break

            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.5
            )
            if msg and msg["type"] == "message":
                raw = msg["data"]
                text = raw.decode() if isinstance(raw, bytes) else raw
                yield f"data: {text}\n\n"
            else:
                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= 30:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now

            await asyncio.sleep(1)
    finally:
        await pubsub.unsubscribe("bookscout:events")

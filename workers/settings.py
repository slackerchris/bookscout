"""arq WorkerSettings — start with ``arq workers.settings.WorkerSettings``."""
from __future__ import annotations

import os

from arq.connections import RedisSettings

from workers.tasks import (
    scan_all_authors_task,
    scan_all_library_paths_task,
    scan_author_task,
    scan_library_path_task,
)


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379"))


async def on_startup(ctx: dict) -> None:
    """Initialise shared resources available to all tasks via ``ctx``."""
    import redis.asyncio as aioredis
    from config import get_config

    config = get_config()
    ctx["redis"] = aioredis.from_url(
        getattr(getattr(config, "redis", None), "url", "redis://redis:6379"),
        decode_responses=False,
    )
    ctx["config"] = config


async def on_shutdown(ctx: dict) -> None:
    if "redis" in ctx:
        await ctx["redis"].aclose()


class WorkerSettings:
    functions = [scan_author_task, scan_all_authors_task, scan_library_path_task, scan_all_library_paths_task]
    redis_settings = _redis_settings()
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)
    max_jobs = 10
    job_timeout = 300  # seconds per job
    keep_result = 3600  # keep result for 1 hour

"""arq WorkerSettings — start with ``arq workers.settings.WorkerSettings``."""
from __future__ import annotations

import os

from arq import cron as arq_cron
from arq.connections import RedisSettings

from workers.tasks import (
    scan_all_authors_task,
    scan_all_library_paths_task,
    scan_author_task,
    scan_library_path_task,
)


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379"))


# ---------------------------------------------------------------------------
# Cron schedule helpers
# ---------------------------------------------------------------------------

def _parse_cron_field(field: str, min_val: int, max_val: int) -> set[int] | None:
    """Parse one crontab field into a set of matching integers, or None (=every)."""
    if field == "*":
        return None
    if field.startswith("*/"):
        step = int(field[2:])
        return set(range(min_val, max_val + 1, step))
    if "," in field:
        return {int(v) for v in field.split(",")}
    if "-" in field:
        lo, hi = field.split("-", 1)
        return set(range(int(lo), int(hi) + 1))
    return {int(field)}


def _cron_kwargs(cron_str: str) -> dict:
    """Parse a 5-field crontab string (minute hour day month weekday) into
    keyword arguments for arq.cron().  None means 'any'."""
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5 cron fields, got {len(parts)}: {cron_str!r}")
    minute_f, hour_f, day_f, month_f, weekday_f = parts
    return {
        "minute":  _parse_cron_field(minute_f,  0,  59),
        "hour":    _parse_cron_field(hour_f,     0,  23),
        "day":     _parse_cron_field(day_f,      1,  31),
        "month":   _parse_cron_field(month_f,    1,  12),
        "weekday": _parse_cron_field(weekday_f,  0,   6),
    }


def _build_cron_jobs() -> list:
    """Read schedule_cron from config and return the arq CronJob list."""
    try:
        from config import get_config
        config = get_config()
        scan_cfg = getattr(config, "scan", None)
        cron_str = getattr(scan_cfg, "schedule_cron", "0 * * * *") or "0 * * * *"
        kwargs = _cron_kwargs(cron_str)
        print(f"[scheduler] schedule: '{cron_str}' → {kwargs}")
        return [arq_cron(scan_all_authors_task, **kwargs)]
    except Exception as exc:
        print(f"[scheduler] failed to parse schedule_cron — scheduled scanning disabled: {exc}")
        return []


# ---------------------------------------------------------------------------
# Startup / shutdown hooks
# ---------------------------------------------------------------------------

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
    cron_jobs = _build_cron_jobs()
    redis_settings = _redis_settings()
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)
    max_jobs = 10
    job_timeout = 300  # seconds per job
    keep_result = 3600  # keep result for 1 hour


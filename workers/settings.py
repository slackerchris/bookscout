"""arq WorkerSettings — start with ``arq workers.settings.WorkerSettings``."""
from __future__ import annotations

import logging
import os

from core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

from arq import cron as arq_cron
from arq.connections import RedisSettings

from workers.tasks import (
    import_download_task,
    poll_completed_downloads_task,
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

_WEEKDAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _cron_atom(value: str, names: dict[str, int] | None) -> int:
    if names is not None:
        named = names.get(value.strip().lower()[:3])
        if named is not None:
            return named
    return int(value)


def _parse_cron_field(
    field: str,
    min_val: int,
    max_val: int,
    names: dict[str, int] | None = None,
) -> set[int] | None:
    """Parse one crontab field into a set of matching integers, or None (=every).

    Handles lists, ranges, steps, and their combinations ("1-5,7", "0-30/10",
    "mon-fri") — each of which is legal crontab syntax.
    """
    if field == "*":
        return None
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)
        if part == "*":
            lo, hi = min_val, max_val
        elif "-" in part:
            lo_str, hi_str = part.split("-", 1)
            lo, hi = _cron_atom(lo_str, names), _cron_atom(hi_str, names)
        else:
            lo = _cron_atom(part, names)
            # "N/step" (GNU extension) means N through max; a bare "N" is just N.
            hi = max_val if step > 1 else lo
        if not (min_val <= lo <= hi <= max_val):
            raise ValueError(f"Cron field out of range: {field!r}")
        values.update(range(lo, hi + 1, step))
    return values


def _cron_kwargs(cron_str: str) -> dict:
    """Parse a 5-field crontab string (minute hour day month weekday) into
    keyword arguments for arq.cron().  None means 'any'."""
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5 cron fields, got {len(parts)}: {cron_str!r}")
    minute_f, hour_f, day_f, month_f, weekday_f = parts
    # Crontab weekdays are Sunday=0 (with 7 also meaning Sunday); arq matches
    # against datetime.weekday(), which is Monday=0..Sunday=6 — convert.
    weekdays = _parse_cron_field(weekday_f, 0, 7, names=_WEEKDAY_NAMES)
    if weekdays is not None:
        weekdays = {(d - 1) % 7 for d in weekdays}
    return {
        "minute":  _parse_cron_field(minute_f,  0,  59),
        "hour":    _parse_cron_field(hour_f,     0,  23),
        "day":     _parse_cron_field(day_f,      1,  31),
        "month":   _parse_cron_field(month_f,    1,  12, names=_MONTH_NAMES),
        "weekday": weekdays,
    }


def _build_cron_jobs() -> list:
    """Read schedule_cron from config and return the arq CronJob list."""
    jobs: list = []
    try:
        from config import get_config
        config = get_config()
        scan_cfg = getattr(config, "scan", None)
        cron_str = getattr(scan_cfg, "schedule_cron", "0 * * * *") or "0 * * * *"
        kwargs = _cron_kwargs(cron_str)
        logger.info("Scan schedule configured", extra={"cron": cron_str, "kwargs": str(kwargs)})
        jobs.append(arq_cron(scan_all_authors_task, **kwargs))
    except Exception as exc:
        logger.warning("Failed to parse schedule_cron — scheduled scanning disabled", extra={"error": str(exc)})

    # Auto-import poller (qBittorrent completed downloads).  Registered
    # unconditionally — the task itself is a cheap no-op unless
    # postprocess.mode = "bookscout" and a qBittorrent client is configured,
    # so config changes take effect without editing the schedule.
    try:
        from config import get_config
        pp = getattr(get_config(), "postprocess", None)
        interval = int(getattr(pp, "auto_import_interval_minutes", 2) or 2)
        interval = min(max(interval, 1), 30)
        jobs.append(arq_cron(poll_completed_downloads_task, minute=set(range(0, 60, interval))))
        logger.info("Auto-import poll schedule configured", extra={"interval_minutes": interval})
    except Exception as exc:
        logger.warning("Failed to configure auto-import poller", extra={"error": str(exc)})

    return jobs


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
    functions = [
        scan_author_task,
        scan_all_authors_task,
        scan_library_path_task,
        scan_all_library_paths_task,
        import_download_task,
        poll_completed_downloads_task,
    ]
    cron_jobs = _build_cron_jobs()
    redis_settings = _redis_settings()
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)
    max_jobs = 10
    job_timeout = 600  # seconds per individual author scan (10 min)
    keep_result = 3600  # keep result for 1 hour


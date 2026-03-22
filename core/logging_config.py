"""Structured JSON logging setup for BookScout.

Call ``setup_logging()`` once at process startup (main.py lifespan,
worker startup hook).  All modules then use:

    import logging
    logger = logging.getLogger(__name__)
    logger.info("message", extra={"key": "value"})

Output is newline-delimited JSON, ready for Loki / Grafana / any log
aggregator.  Each line contains at minimum:
    timestamp, level, logger (module name), message
plus any extra fields passed by the caller.
"""
from __future__ import annotations

import logging
import logging.config
import os


def setup_logging(level: str | None = None) -> None:
    """Configure root logger to emit JSON to stdout."""
    log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()

    try:
        from pythonjsonlogger.json import JsonFormatter  # type: ignore[import]
    except ImportError:
        # Graceful fallback if python-json-logger not installed yet
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        return

    fmt = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )

    handler = logging.StreamHandler()
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quieten noisy third-party loggers
    for noisy in ("httpx", "httpcore", "uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

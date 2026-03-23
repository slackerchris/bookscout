"""
BookScout configuration loader.

Reads config.yaml (default: /data/config.yaml, override with BOOKSCOUT_CONFIG env var)
and merges with environment variable overrides.

All YAML values can be overridden individually:
  DATABASE_URL, REDIS_URL, AUDIOBOOKSHELF_URL, AUDIOBOOKSHELF_TOKEN,
  PROWLARR_URL, PROWLARR_API_KEY, GOOGLE_BOOKS_API_KEY, ISBNDB_API_KEY,
  SECRET_KEY, PORT
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

_DEFAULT: dict = {
    "database": {"url": "postgresql+asyncpg://bookscout:bookscout@localhost/bookscout"},
    "redis": {"url": "redis://localhost:6379"},
    "audiobookshelf": {"url": "", "token": ""},
    "prowlarr": {"url": "", "api_key": ""},
    "jackett": {"url": "", "api_key": ""},
    "apis": {"google_books_key": "", "isbndb_key": ""},
    "download": {
        "preferred": "",
        "sabnzbd": {"url": "", "api_key": "", "default_category": ""},
        "torrent": {
            "type": "qbittorrent",
            "url": "",
            "username": "",
            "password": "",
            "default_category": "",
            "save_path": "",
        },
    },
    "scan": {
        "schedule_cron": "0 * * * *",
        "max_concurrent_scans": 5,
        "language_filter": "all",
        "auto_add_coauthors": False,
        "sources": {
            "openlibrary": True,
            "google_books": True,
            "audible": True,
            "isbndb": True,   # only active when apis.isbndb_key is set
        },
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "secret_key": "bookscout-secret-key-change-in-production",
    },
    "postprocess": {
        # "bookscout" = BookScout extracts and moves files into author/series/book layout
        # "client"    = download client is responsible (e.g. via its own post-processing scripts)
        "mode": "client",
        "library_root": "",  # required when mode = "bookscout"
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _to_ns(obj: object) -> object:
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in obj.items()})
    return obj


def _apply_env_overrides(data: dict) -> dict:
    """Layer environment variables on top of YAML values (non-destructive copy)."""
    import copy
    data = copy.deepcopy(data)
    env = os.environ

    if v := env.get("DATABASE_URL"):
        data["database"]["url"] = v
    if v := env.get("REDIS_URL"):
        data["redis"]["url"] = v
    if v := env.get("AUDIOBOOKSHELF_URL"):
        data["audiobookshelf"]["url"] = v
    if v := env.get("AUDIOBOOKSHELF_TOKEN"):
        data["audiobookshelf"]["token"] = v
    if v := env.get("PROWLARR_URL"):
        data["prowlarr"]["url"] = v
    if v := env.get("PROWLARR_API_KEY"):
        data["prowlarr"]["api_key"] = v
    if v := env.get("GOOGLE_BOOKS_API_KEY"):
        data["apis"]["google_books_key"] = v
    if v := env.get("ISBNDB_API_KEY"):
        data["apis"]["isbndb_key"] = v
    if v := env.get("SECRET_KEY"):
        data["server"]["secret_key"] = v
    if v := env.get("PORT"):
        data["server"]["port"] = int(v)

    return data


def load_config(path: Optional[str] = None) -> SimpleNamespace:
    """Load config from YAML file + env var overrides.  Idempotent — call as many times as needed."""
    config_path = path or os.getenv("BOOKSCOUT_CONFIG", "/data/config.yaml")
    raw: dict = {}
    if _yaml and Path(config_path).exists():
        with open(config_path) as fh:
            raw = _yaml.safe_load(fh) or {}

    merged = _deep_merge(_DEFAULT, raw)
    merged = _apply_env_overrides(merged)
    return _to_ns(merged)  # type: ignore[return-value]


_config: Optional[SimpleNamespace] = None


def get_config() -> SimpleNamespace:
    """Return the process-level singleton config (lazy-loaded on first call)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config

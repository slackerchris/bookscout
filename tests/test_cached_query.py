"""Tests for core/scan.py — _cached_query() and _cache_author_key().

Uses a simple in-memory mock Redis client (no real Redis needed) that
implements only get/set — the same interface used by arq's ArqRedis.
"""
from __future__ import annotations

import json
import pytest
import pytest_asyncio

from core.scan import _cache_author_key, _cached_query


# ---------------------------------------------------------------------------
# Mock Redis client
# ---------------------------------------------------------------------------

class MockRedis:
    """Minimal async Redis mock: get / set(ex=) only."""

    def __init__(self, initial: dict | None = None, fail_reads: bool = False, fail_writes: bool = False):
        self._store: dict[str, bytes] = {}
        if initial:
            for k, v in initial.items():
                self._store[k] = json.dumps(v).encode()
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self.get_calls: list[str] = []
        self.set_calls: list[str] = []

    async def get(self, key: str):
        self.get_calls.append(key)
        if self.fail_reads:
            raise ConnectionError("mock redis read failure")
        return self._store.get(key)

    async def set(self, key: str, value, ex: int = 0):
        self.set_calls.append(key)
        if self.fail_writes:
            raise ConnectionError("mock redis write failure")
        self._store[key] = value if isinstance(value, bytes) else value.encode()


async def _live_query(result: list) -> list:
    """Simulates a metadata API coroutine returning *result*."""
    return result


# ---------------------------------------------------------------------------
# _cache_author_key
# ---------------------------------------------------------------------------

class TestCacheAuthorKey:
    def test_normalises_punctuation(self):
        assert _cache_author_key("J.N. Chaney") == "jnchaney"

    def test_normalises_spaces(self):
        assert _cache_author_key("Brandon Sanderson") == "brandonsanderson"

    def test_lowercase(self):
        assert _cache_author_key("UPPER CASE") == "uppercase"

    def test_retains_numbers(self):
        assert _cache_author_key("Author 2nd") == "author2nd"


# ---------------------------------------------------------------------------
# _cached_query — cache miss
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_miss_calls_coroutine():
    redis = MockRedis()
    data = [{"title": "Dune"}]
    result = await _cached_query(redis, 3600, "bookscout:meta:test", _live_query(data))
    assert result == data
    assert "bookscout:meta:test" in redis.set_calls  # value was stored


@pytest.mark.asyncio
async def test_cache_miss_stores_result():
    redis = MockRedis()
    data = [{"title": "Dune"}, {"title": "Dune Messiah"}]
    await _cached_query(redis, 3600, "my:key", _live_query(data))
    stored = json.loads(redis._store["my:key"])
    assert stored == data


# ---------------------------------------------------------------------------
# _cached_query — cache hit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_hit_returns_stored_value():
    cached_data = [{"title": "Foundation"}]
    redis = MockRedis(initial={"my:key": cached_data})

    call_count = 0

    async def expensive_query():
        nonlocal call_count
        call_count += 1
        return [{"title": "Something else"}]

    coro = expensive_query()
    result = await _cached_query(redis, 3600, "my:key", coro)
    assert result == cached_data
    assert call_count == 0  # coroutine was never invoked on cache hit
    coro.close()  # suppress "never awaited" GC warning


# ---------------------------------------------------------------------------
# _cached_query — no Redis client (graceful bypass)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_redis_client_bypasses_cache():
    data = [{"title": "Words of Radiance"}]
    result = await _cached_query(None, 3600, "key", _live_query(data))
    assert result == data


@pytest.mark.asyncio
async def test_zero_ttl_bypasses_cache():
    redis = MockRedis()
    data = [{"title": "Words of Radiance"}]
    result = await _cached_query(redis, 0, "key", _live_query(data))
    assert result == data
    assert redis.set_calls == []  # nothing written


# ---------------------------------------------------------------------------
# _cached_query — Redis errors are non-fatal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_read_failure_falls_through_to_live():
    redis = MockRedis(fail_reads=True)
    data = [{"title": "Live result"}]
    result = await _cached_query(redis, 3600, "key", _live_query(data))
    assert result == data  # got live data despite read error


@pytest.mark.asyncio
async def test_redis_write_failure_returns_live_result():
    redis = MockRedis(fail_writes=True)
    data = [{"title": "Live result"}]
    result = await _cached_query(redis, 3600, "key", _live_query(data))
    assert result == data  # returned correctly even though write failed

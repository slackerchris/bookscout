"""Tests for webhook delivery retry logic and dead-endpoint detection.

The webhooks/webhook_deliveries tables use Postgres-only ARRAY/JSONB types
that SQLite cannot render, so the in-memory DB fixture cannot cover this code.
These tests patch both the SQLAlchemy session and httpx, so no real DB or
network connection is needed.

Coverage targets
----------------
_deliver()        — retry loop, backoff, HTTP failures, network errors
deliver_event()   — failure_count increments/resets, dead-endpoint auto-disable,
                    event subscription filtering
"""
from __future__ import annotations

import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# db.session creates a real asyncpg engine at import time; stub it out so
# importing api.v1.webhooks doesn't require a live database connection.
_db_session_stub = types.ModuleType("db.session")
_db_session_stub.get_session = None  # type: ignore[attr-defined]
sys.modules.setdefault("db.session", _db_session_stub)

from api.v1.webhooks import _deliver, deliver_event, _DEAD_THRESHOLD  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_webhook(
    id: int = 1,
    url: str = "http://example.com/hook",
    events: list[str] | None = None,
    failure_count: int = 0,
    active: bool = True,
) -> MagicMock:
    wh = MagicMock()
    wh.id = id
    wh.url = url
    wh.events = events  # None / [] = subscribe to all events
    wh.failure_count = failure_count
    wh.active = active
    wh.disabled_at = None
    return wh


def _make_session(webhooks: list) -> AsyncMock:
    """Return an AsyncMock session whose execute() yields *webhooks*."""
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value = iter(webhooks)
    session.execute.return_value = execute_result
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# _deliver() — retry loop
# ---------------------------------------------------------------------------

class TestDeliver:
    async def test_success_first_attempt(self):
        mock_resp = MagicMock(status_code=200)
        with patch("api.v1.webhooks.httpx.AsyncClient") as mock_cls:
            inst = AsyncMock()
            inst.post.return_value = mock_resp
            mock_cls.return_value.__aenter__.return_value = inst

            success, code = await _deliver("http://example.com", {"event": "test"})

        assert success is True
        assert code == 200
        assert inst.post.call_count == 1

    async def test_http_error_retries_and_succeeds(self):
        responses = [MagicMock(status_code=503), MagicMock(status_code=200)]
        idx = 0

        async def _post(*a, **kw):
            nonlocal idx
            r = responses[idx]; idx += 1; return r

        with patch("api.v1.webhooks.httpx.AsyncClient") as mock_cls, \
             patch("api.v1.webhooks.asyncio.sleep", new_callable=AsyncMock):
            inst = AsyncMock()
            inst.post.side_effect = _post
            mock_cls.return_value.__aenter__.return_value = inst

            success, code = await _deliver("http://x.com", {}, max_attempts=2)

        assert success is True
        assert code == 200
        assert inst.post.call_count == 2

    async def test_all_attempts_fail_returns_false_and_last_code(self):
        mock_resp = MagicMock(status_code=500)
        with patch("api.v1.webhooks.httpx.AsyncClient") as mock_cls, \
             patch("api.v1.webhooks.asyncio.sleep", new_callable=AsyncMock):
            inst = AsyncMock()
            inst.post.return_value = mock_resp
            mock_cls.return_value.__aenter__.return_value = inst

            success, code = await _deliver("http://x.com", {}, max_attempts=3)

        assert success is False
        assert code == 500
        assert inst.post.call_count == 3

    async def test_network_error_returns_none_code(self):
        with patch("api.v1.webhooks.httpx.AsyncClient") as mock_cls:
            inst = AsyncMock()
            inst.post.side_effect = OSError("connection refused")
            mock_cls.return_value.__aenter__.return_value = inst

            success, code = await _deliver("http://x.com", {}, max_attempts=1)

        assert success is False
        assert code is None

    async def test_network_error_then_success(self):
        idx = 0

        async def _post(*a, **kw):
            nonlocal idx
            idx += 1
            if idx == 1:
                raise OSError("timeout")
            return MagicMock(status_code=204)

        with patch("api.v1.webhooks.httpx.AsyncClient") as mock_cls, \
             patch("api.v1.webhooks.asyncio.sleep", new_callable=AsyncMock):
            inst = AsyncMock()
            inst.post.side_effect = _post
            mock_cls.return_value.__aenter__.return_value = inst

            success, code = await _deliver("http://x.com", {}, max_attempts=2)

        assert success is True
        assert code == 204

    async def test_client_reused_across_retries(self):
        """A single AsyncClient instance must span all retry attempts."""
        mock_resp = MagicMock(status_code=500)
        with patch("api.v1.webhooks.httpx.AsyncClient") as mock_cls, \
             patch("api.v1.webhooks.asyncio.sleep", new_callable=AsyncMock):
            inst = AsyncMock()
            inst.post.return_value = mock_resp
            mock_cls.return_value.__aenter__.return_value = inst

            await _deliver("http://x.com", {}, max_attempts=3)

        # AsyncClient() was instantiated exactly once (not once-per-attempt)
        assert mock_cls.call_count == 1


# ---------------------------------------------------------------------------
# deliver_event() — failure tracking and dead-endpoint detection
# ---------------------------------------------------------------------------

class TestDeliverEvent:
    async def test_success_resets_failure_count(self):
        webhook = _make_webhook(failure_count=3)
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = (True, 200)
            await deliver_event("book.discovered", {"book_id": 1}, session)

        assert webhook.failure_count == 0

    async def test_failure_increments_failure_count(self):
        webhook = _make_webhook(failure_count=1)
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = (False, 503)
            await deliver_event("book.discovered", {}, session)

        assert webhook.failure_count == 2

    async def test_failure_count_increments_by_one_per_event_not_per_attempt(self):
        """failure_count += 1 per failed event regardless of _MAX_ATTEMPTS."""
        webhook = _make_webhook(failure_count=0)
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            # _deliver returns failure — all internal attempts already exhausted
            mock_deliver.return_value = (False, None)
            await deliver_event("scan.complete", {}, session)

        # Only +1, not +_MAX_ATTEMPTS
        assert webhook.failure_count == 1

    async def test_dead_threshold_disables_webhook(self):
        webhook = _make_webhook(failure_count=_DEAD_THRESHOLD - 1)
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = (False, 500)
            await deliver_event("scan.complete", {}, session)

        assert webhook.active is False
        assert webhook.disabled_at is not None

    async def test_below_dead_threshold_stays_active(self):
        webhook = _make_webhook(failure_count=_DEAD_THRESHOLD - 2)
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = (False, 503)
            await deliver_event("scan.complete", {}, session)

        assert webhook.active is True
        assert webhook.disabled_at is None

    async def test_unsubscribed_event_is_skipped(self):
        """Webhook subscribed only to 'scan.complete' must not receive other events."""
        webhook = _make_webhook(events=["scan.complete"])
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            await deliver_event("book.discovered", {}, session)

        mock_deliver.assert_not_called()

    async def test_subscribed_event_is_delivered(self):
        webhook = _make_webhook(events=["book.discovered"])
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = (True, 200)
            await deliver_event("book.discovered", {"book_id": 42}, session)

        mock_deliver.assert_called_once()

    async def test_empty_events_list_delivers_all(self):
        """events=[] means subscribe to everything."""
        webhook = _make_webhook(events=[])
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = (True, 200)
            await deliver_event("anything.at.all", {}, session)

        mock_deliver.assert_called_once()

    async def test_delivery_log_row_added_on_success(self):
        webhook = _make_webhook()
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = (True, 200)
            await deliver_event("scan.complete", {}, session)

        session.add.assert_called_once()

    async def test_delivery_log_row_added_on_failure(self):
        webhook = _make_webhook()
        session = _make_session([webhook])

        with patch("api.v1.webhooks._deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = (False, 503)
            await deliver_event("scan.complete", {}, session)

        session.add.assert_called_once()

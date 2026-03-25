"""Webhook registration and delivery log."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Webhook, WebhookDelivery
from db.session import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Delivery retry settings
_MAX_ATTEMPTS = 3          # attempts per event delivery
_BACKOFF_DELAYS = [0, 2, 8]  # seconds before attempt 1, 2, 3
_DEAD_THRESHOLD = 5        # consecutive failures before auto-disabling


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class WebhookCreate(BaseModel):
    url: str
    description: str | None = None
    events: list[str] = []  # empty = all events


class WebhookOut(BaseModel):
    id: int
    url: str
    description: str | None = None
    events: list[str] | None = None
    active: bool
    failure_count: int = 0
    disabled_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class DeliveryOut(BaseModel):
    id: int
    event_type: str
    status_code: int | None = None
    success: bool | None = None
    delivered_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[WebhookOut])
async def list_webhooks(session: AsyncSession = Depends(get_session)) -> list[Webhook]:
    result = await session.execute(select(Webhook).where(Webhook.active.is_(True)))
    return list(result.scalars().all())


@router.post("/", response_model=WebhookOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreate,
    session: AsyncSession = Depends(get_session),
) -> Webhook:
    existing = await session.execute(select(Webhook).where(Webhook.url == body.url))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Webhook URL already registered")

    webhook = Webhook(url=body.url, description=body.description, events=body.events or [])
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)
    return webhook


@router.get("/{webhook_id}", response_model=WebhookOut)
async def get_webhook(
    webhook_id: int,
    session: AsyncSession = Depends(get_session),
) -> Webhook:
    return await _get_or_404(session, webhook_id)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    webhook = await _get_or_404(session, webhook_id)
    webhook.active = False
    await session.commit()


@router.post("/{webhook_id}/reactivate", response_model=WebhookOut, summary="Re-enable an auto-disabled webhook")
async def reactivate_webhook(
    webhook_id: int,
    session: AsyncSession = Depends(get_session),
) -> Webhook:
    """Reset ``failure_count`` and re-enable a webhook that was auto-disabled
    by dead endpoint detection.  Also works for webhooks deactivated manually
    via DELETE if you want to restore them without recreating."""
    webhook = await _get_or_404(session, webhook_id)
    webhook.active = True
    webhook.failure_count = 0
    webhook.disabled_at = None
    await session.commit()
    await session.refresh(webhook)
    return webhook


@router.post("/{webhook_id}/test", summary="Send a test event to this webhook")
async def test_webhook(
    webhook_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    webhook = await _get_or_404(session, webhook_id)
    payload = {"event": "webhook.test", "message": "BookScout webhook test ping"}
    success, code = await _deliver(webhook.url, payload)
    session.add(
        WebhookDelivery(
            webhook_id=webhook_id,
            event_type="webhook.test",
            payload=payload,
            status_code=code,
            success=success,
        )
    )
    await session.commit()
    return {"success": success, "status_code": code}


@router.get("/{webhook_id}/deliveries", response_model=list[DeliveryOut])
async def list_deliveries(
    webhook_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[WebhookDelivery]:
    await _get_or_404(session, webhook_id)
    result = await session.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.delivered_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Internal delivery helper (also used by workers)
# ---------------------------------------------------------------------------

async def deliver_event(
    event_type: str,
    payload: dict,
    session: AsyncSession,
) -> None:
    """Fan-out *event_type* to all active subscribed webhooks.

    Each endpoint is tried up to ``_MAX_ATTEMPTS`` times with exponential
    backoff.  Consecutive failures increment ``Webhook.failure_count``; once
    that reaches ``_DEAD_THRESHOLD`` the webhook is automatically deactivated
    and ``disabled_at`` is recorded.  A successful delivery resets the counter.
    """
    result = await session.execute(select(Webhook).where(Webhook.active.is_(True)))
    for webhook in result.scalars():
        subscribed: list[str] = webhook.events or []
        if subscribed and event_type not in subscribed:
            continue
        success, code = await _deliver(
            webhook.url,
            {"event": event_type, **payload},
            max_attempts=_MAX_ATTEMPTS,
        )
        if success:
            webhook.failure_count = 0
        else:
            webhook.failure_count = (webhook.failure_count or 0) + 1
            if webhook.failure_count >= _DEAD_THRESHOLD:
                webhook.active = False
                webhook.disabled_at = datetime.now(timezone.utc)
                logger.warning(
                    "Auto-disabled dead webhook %d after %d consecutive failures",
                    webhook.id, webhook.failure_count,
                    extra={"url": webhook.url},
                )
        session.add(
            WebhookDelivery(
                webhook_id=webhook.id,
                event_type=event_type,
                payload={"event": event_type, **payload},
                status_code=code,
                success=success,
            )
        )
    await session.commit()


async def _deliver(url: str, payload: dict, max_attempts: int = 1) -> tuple[bool, int | None]:
    """POST *payload* to *url*, retrying up to *max_attempts* with exponential backoff.

    Delays between attempts are taken from ``_BACKOFF_DELAYS``; the last entry
    is repeated when *max_attempts* exceeds the table length.
    Returns ``(success, last_http_status_code)``.
    """
    last_code: int | None = None
    for i in range(max_attempts):
        delay = _BACKOFF_DELAYS[i] if i < len(_BACKOFF_DELAYS) else _BACKOFF_DELAYS[-1]
        if delay:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(url, json=payload, timeout=10)
                if r.status_code < 400:
                    return True, r.status_code
                last_code = r.status_code
                logger.warning(
                    "Webhook delivery attempt %d/%d got HTTP %d",
                    i + 1, max_attempts, r.status_code,
                    extra={"url": url},
                )
        except Exception as exc:
            logger.warning(
                "Webhook delivery attempt %d/%d network error",
                i + 1, max_attempts,
                extra={"url": url, "error": str(exc)},
            )
    return False, last_code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(session: AsyncSession, webhook_id: int) -> Webhook:
    result = await session.execute(select(Webhook).where(Webhook.id == webhook_id))
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return webhook

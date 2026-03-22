"""Webhook registration and delivery log."""
from __future__ import annotations

import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Webhook, WebhookDelivery
from db.session import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


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
    """Fan-out *event_type* to all active subscribed webhooks."""
    result = await session.execute(select(Webhook).where(Webhook.active.is_(True)))
    for webhook in result.scalars():
        subscribed: list[str] = webhook.events or []
        if subscribed and event_type not in subscribed:
            continue
        success, code = await _deliver(webhook.url, {"event": event_type, **payload})
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


async def _deliver(url: str, payload: dict) -> tuple[bool, int | None]:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=10)
            return r.status_code < 400, r.status_code
    except Exception as exc:
        logger.error("Webhook delivery failed", extra={"url": url, "error": str(exc)})
        return False, None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_404(session: AsyncSession, webhook_id: int) -> Webhook:
    result = await session.execute(select(Webhook).where(Webhook.id == webhook_id))
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return webhook

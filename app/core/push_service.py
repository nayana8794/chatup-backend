# chatup/backend/app/core/push_service.py
import json
import logging
import asyncio
from typing import List, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pywebpush import webpush, WebPushException

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.push_subscription import PushSubscription

logger = logging.getLogger("uvicorn.error")


def _send_single_notification(subscription_info: dict, payload_data: str) -> Optional[str]:
    """Synchronous pywebpush sender run in a thread."""
    if not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        return None

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload_data,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_CLAIM_EMAIL},
        )
        return None
    except WebPushException as ex:
        # HTTP 404 or 410 indicates the client unsubscribed or token expired
        if ex.response is not None and ex.response.status_code in (404, 410):
            return subscription_info.get("endpoint")
        logger.warning("WebPush delivery failure: %s", str(ex))
        return None
    except Exception as e:
        logger.warning("Unexpected error during webpush dispatch: %s", str(e))
        return None


async def dispatch_push_to_users(
    user_ids: List[uuid.UUID],
    title: str,
    body: str,
    url: str = "/",
    tag: Optional[str] = None,
    data: Optional[dict] = None,
):
    """
    Asynchronously queries all device subscriptions for the given users
    and dispatches Web Push notifications without blocking API execution.
    """
    if not user_ids or not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag or "chatup-notification",
        "icon": "/pwa-192.png",
        "badge": "/pwa-192.png",
        "custom_data": data or {},
    })

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PushSubscription).where(PushSubscription.user_id.in_(user_ids))
        )
        subs = result.scalars().all()

        if not subs:
            return

        dead_endpoints: List[str] = []

        for sub in subs:
            sub_info = {
                "endpoint": sub.endpoint,
                "keys": {
                    "p256dh": sub.p256dh,
                    "auth": sub.auth,
                },
            }
            # Execute in default thread pool to avoid blocking asyncio loop
            dead_ep = await asyncio.to_thread(_send_single_notification, sub_info, payload)
            if dead_ep:
                dead_endpoints.append(dead_ep)

        # Clean up stale / expired subscriptions
        if dead_endpoints:
            await db.execute(
                delete(PushSubscription).where(PushSubscription.endpoint.in_(dead_endpoints))
            )
            await db.commit()
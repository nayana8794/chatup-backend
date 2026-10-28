# chatup/backend/app/core/push_service.py
import json
import logging
import asyncio
from typing import List, Optional
import uuid
import base64
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    WebPushException = Exception

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.push_subscription import PushSubscription

logger = logging.getLogger("uvicorn.error")


def _get_formatted_private_key(raw_key: str) -> str:
    clean_key = raw_key.strip().replace("\\n", "\n")
    if "BEGIN EC PRIVATE KEY" in clean_key or "BEGIN PRIVATE KEY" in clean_key:
        return clean_key

    try:
        padding = "=" * ((4 - len(clean_key) % 4) % 4)
        b64 = clean_key + padding
        key_bytes = base64.urlsafe_b64decode(b64)
        if len(key_bytes) == 32:
            priv_num = int.from_bytes(key_bytes, "big")
            d_key = ec.derive_private_key(priv_num, ec.SECP256R1())
            pem = d_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
            return pem.decode("utf-8")
    except Exception as e:
        logger.debug("Key conversion fallback: %s", str(e))

    return clean_key


def _send_single_notification(subscription_info: dict, payload_data: str) -> Optional[str]:
    if not webpush:
        logger.warning("pywebpush library is not installed.")
        return None

    if not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        logger.warning("VAPID keys are missing in backend configuration.")
        return None

    pem_private_key = _get_formatted_private_key(settings.VAPID_PRIVATE_KEY)

    # Note: Only 'Urgency' in headers, NO duplicate 'TTL' in headers because pywebpush has ttl parameter
    push_headers = {"Urgency": "high"}

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload_data,
            vapid_private_key=pem_private_key,
            vapid_claims={"sub": settings.VAPID_CLAIM_EMAIL},
            headers=push_headers,
            ttl=86400,
        )
        return None
    except WebPushException as ex:
        if hasattr(ex, "response") and ex.response is not None and ex.response.status_code in (404, 410):
            return subscription_info.get("endpoint")
        logger.error("WebPush delivery failure: %s", repr(ex))
        return None
    except Exception as e1:
        # Fallback using raw private key string directly
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload_data,
                vapid_private_key=settings.VAPID_PRIVATE_KEY.strip(),
                vapid_claims={"sub": settings.VAPID_CLAIM_EMAIL},
                headers=push_headers,
                ttl=86400,
            )
            return None
        except Exception as e2:
            logger.error("WebPush dispatch exception: %s (fallback failed: %s)", str(e1), str(e2))
            return None


async def dispatch_push_to_users(
    user_ids: List[uuid.UUID],
    title: str,
    body: str,
    url: str = "/",
    tag: Optional[str] = None,
    badge_count: int = 1,
    data: Optional[dict] = None,
):
    if not user_ids or not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag or f"chatup-{uuid.uuid4()}",
        "icon": "/pwa-192.png",
        "badge": "/pwa-192.png",
        "badge_count": badge_count,
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
            dead_ep = await asyncio.to_thread(_send_single_notification, sub_info, payload)
            if dead_ep:
                dead_endpoints.append(dead_ep)

        if dead_endpoints:
            await db.execute(
                delete(PushSubscription).where(PushSubscription.endpoint.in_(dead_endpoints))
            )
            await db.commit()
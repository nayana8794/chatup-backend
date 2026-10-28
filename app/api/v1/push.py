# chatup/backend/app/api/v1/push.py
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.config import settings
from app.core.push_service import dispatch_push_to_users
from app.models.user import User
from app.models.push_subscription import PushSubscription

router = APIRouter(prefix="/push", tags=["Web Push Notifications"])


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: Optional[str] = None


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


@router.get("/vapid-public-key")
async def get_vapid_public_key():
    if not settings.VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="VAPID keys not configured on server")
    return {"public_key": settings.VAPID_PUBLIC_KEY}


@router.post("/subscribe")
async def subscribe_push_notifications(
    req: PushSubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            select(PushSubscription).where(PushSubscription.endpoint == req.endpoint)
        )
    ).scalar_one_or_none()

    now = datetime.utcnow().replace(tzinfo=None)

    if existing:
        existing.user_id = current_user.id
        existing.p256dh = req.keys.p256dh
        existing.auth = req.keys.auth
        existing.user_agent = req.user_agent
        existing.created_at = now
    else:
        new_sub = PushSubscription(
            user_id=current_user.id,
            endpoint=req.endpoint,
            p256dh=req.keys.p256dh,
            auth=req.keys.auth,
            user_agent=req.user_agent,
            created_at=now,
        )
        db.add(new_sub)

    await db.commit()
    return {"status": "subscribed"}


@router.post("/unsubscribe")
async def unsubscribe_push_notifications(
    req: PushUnsubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sub = (
        await db.execute(
            select(PushSubscription).where(
                PushSubscription.endpoint == req.endpoint,
                PushSubscription.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()

    if sub:
        await db.delete(sub)
        await db.commit()

    return {"status": "unsubscribed"}


@router.post("/test")
async def send_test_push_notification(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    background_tasks.add_task(
        dispatch_push_to_users,
        user_ids=[current_user.id],
        title="ChatUp Club Notification 🦁",
        body="Congratulations! Mobile Web Push is active on your device.",
        url="/",
    )
    return {"status": "queued"}
# chatup/backend/app/api/v1/meeting_minutes.py
import uuid
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.meeting_minute import MeetingMinute, MinuteStatus
from app.models.user import User, UserRole
from app.schemas.meeting_minute import (
    MinuteCreateRequest,
    MinuteUpdateRequest,
    MinuteResponse,
)
from app.core.ws_manager import manager

router = APIRouter(prefix="/mom", tags=["Meeting Minutes"])

MOM_MANAGERS = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
    UserRole.SECRETARY,
    UserRole.VICE_SECRETARY,
]

class LockResponse(BaseModel):
    is_locked_by_other: bool
    lock_holder: Optional[dict] = None

@router.get("", response_model=List[MinuteResponse])
async def list_meeting_minutes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(MeetingMinute, User.full_name.label("author_name")).outerjoin(
        User, MeetingMinute.author_id == User.id
    )

    if current_user.role != UserRole.SUPER_ADMIN:
        query = query.where(MeetingMinute.tenant_id == current_user.tenant_id)

    if current_user.role not in MOM_MANAGERS:
        query = query.where(MeetingMinute.status == MinuteStatus.PUBLISHED)

    query = query.order_by(MeetingMinute.meeting_date.desc())
    result = await db.execute(query)

    response_list = []
    for minute, author_name in result.all():
        data = MinuteResponse.model_validate(minute)
        data.author_name = author_name
        response_list.append(data)

    return response_list

@router.get("/{minute_id}", response_model=MinuteResponse)
async def get_minute_by_id(
    minute_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(MeetingMinute, User.full_name.label("author_name"))
        .outerjoin(User, MeetingMinute.author_id == User.id)
        .where(MeetingMinute.id == minute_id)
    )
    result = await db.execute(query)
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Meeting minute document not found")

    minute, author_name = row

    if current_user.role != UserRole.SUPER_ADMIN and minute.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="You do not have permission to access this document")

    if current_user.role not in MOM_MANAGERS and minute.status == MinuteStatus.DRAFT:
        raise HTTPException(status_code=403, detail="This draft minute has not been published yet")

    data = MinuteResponse.model_validate(minute)
    data.author_name = author_name
    return data

@router.post("/{minute_id}/lock", response_model=LockResponse)
async def acquire_document_lock(
    minute_id: uuid.UUID,
    force: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in MOM_MANAGERS:
        raise HTTPException(status_code=403, detail="Permission denied to lock documents")

    minute_str = str(minute_id)
    acquired, lock_data = manager.acquire_mom_lock(
        minute_id=minute_str,
        user_id=str(current_user.id),
        user_name=current_user.full_name,
        user_role=current_user.role.value,
        force=force,
    )

    if acquired:
        # Lock එකක් ලබාගත් පසු අනෙක් අයට දැනුම් දීම
        if current_user.tenant_id:
            await manager.broadcast_to_tenant(
                str(current_user.tenant_id),
                {
                    "type": "MOM_LOCK_ACQUIRED",
                    "minute_id": minute_str,
                    "user_id": str(current_user.id),
                    "user_name": current_user.full_name,
                    "user_role": current_user.role.value,
                },
            )
        return LockResponse(is_locked_by_other=False, lock_holder=lock_data)

    # වෙනත් කෙනෙකු ලියමින් සිටී නම්
    return LockResponse(is_locked_by_other=True, lock_holder=lock_data)

@router.post("/{minute_id}/heartbeat")
async def heartbeat_document_lock(
    minute_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    success = manager.heartbeat_mom_lock(str(minute_id), str(current_user.id))
    return {"status": "extended" if success else "expired"}

@router.post("/{minute_id}/unlock")
async def release_document_lock(
    minute_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    minute_str = str(minute_id)
    manager.release_mom_lock(minute_str, str(current_user.id))

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id),
            {
                "type": "MOM_LOCK_RELEASED",
                "minute_id": minute_str,
            },
        )
    return {"status": "unlocked"}

@router.post("", response_model=MinuteResponse)
async def create_meeting_minute(
    minute_in: MinuteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in MOM_MANAGERS:
        raise HTTPException(status_code=403, detail="Only Secretary or President can create meeting minutes")

    clean_meeting_date = minute_in.meeting_date
    if clean_meeting_date.tzinfo is not None:
        clean_meeting_date = clean_meeting_date.replace(tzinfo=None)

    new_minute = MeetingMinute(
        tenant_id=current_user.tenant_id,
        author_id=current_user.id,
        title=minute_in.title,
        meeting_date=clean_meeting_date,
        location=minute_in.location,
        attendees=minute_in.attendees,
        content=minute_in.content,
        status=minute_in.status,
    )

    db.add(new_minute)
    await db.commit()
    await db.refresh(new_minute)

    if new_minute.tenant_id:
        await manager.broadcast_to_tenant(
            str(new_minute.tenant_id),
            {
                "type": "MOM_PUBLISHED" if new_minute.status == MinuteStatus.PUBLISHED else "MOM_SAVED",
                "minute_id": str(new_minute.id),
                "title": new_minute.title,
                "status": new_minute.status.value,
                "author_name": current_user.full_name,
            },
        )

    data = MinuteResponse.model_validate(new_minute)
    data.author_name = current_user.full_name
    return data

@router.put("/{minute_id}", response_model=MinuteResponse)
async def update_meeting_minute(
    minute_id: uuid.UUID,
    minute_update: MinuteUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in MOM_MANAGERS:
        raise HTTPException(status_code=403, detail="Permission denied to edit meeting minutes")

    # Save කිරීමට පෙර වෙනත් කෙනෙකු Lock කර ඇත්දැයි බැලීම
    active_lock = manager.get_mom_lock(str(minute_id))
    if active_lock and active_lock["user_id"] != str(current_user.id):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot save: document is currently being edited by {active_lock['user_name']}",
        )

    minute = (await db.execute(select(MeetingMinute).where(MeetingMinute.id == minute_id))).scalar_one_or_none()
    if not minute:
        raise HTTPException(status_code=404, detail="Meeting minute document not found")

    if minute_update.title is not None:
        minute.title = minute_update.title
    if minute_update.meeting_date is not None:
        clean_date = minute_update.meeting_date
        if clean_date.tzinfo is not None:
            clean_date = clean_date.replace(tzinfo=None)
        minute.meeting_date = clean_date
    if minute_update.location is not None:
        minute.location = minute_update.location
    if minute_update.attendees is not None:
        minute.attendees = minute_update.attendees
    if minute_update.content is not None:
        minute.content = minute_update.content
    if minute_update.status is not None:
        minute.status = minute_update.status

    await db.commit()
    await db.refresh(minute)

    # Save වූ පසු Lock එක නිදහස් කර අනෙක් අයට දැනුම් දීම
    manager.release_mom_lock(str(minute.id))

    if minute.tenant_id:
        await manager.broadcast_to_tenant(
            str(minute.tenant_id),
            {
                "type": "MOM_PUBLISHED" if minute.status == MinuteStatus.PUBLISHED else "MOM_SAVED",
                "minute_id": str(minute.id),
                "title": minute.title,
                "status": minute.status.value,
                "author_name": current_user.full_name,
            },
        )
        await manager.broadcast_to_tenant(
            str(minute.tenant_id),
            {
                "type": "MOM_LOCK_RELEASED",
                "minute_id": str(minute.id),
            },
        )

    data = MinuteResponse.model_validate(minute)
    data.author_name = current_user.full_name
    return data

@router.delete("/{minute_id}")
async def delete_meeting_minute(
    minute_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in MOM_MANAGERS:
        raise HTTPException(status_code=403, detail="Permission denied to delete meeting minutes")

    minute = (await db.execute(select(MeetingMinute).where(MeetingMinute.id == minute_id))).scalar_one_or_none()
    if not minute:
        raise HTTPException(status_code=404, detail="Meeting minute document not found")

    tenant_id = minute.tenant_id
    minute_id_str = str(minute.id)

    manager.release_mom_lock(minute_id_str)
    await db.delete(minute)
    await db.commit()

    if tenant_id:
        await manager.broadcast_to_tenant(
            str(tenant_id),
            {
                "type": "MOM_DELETED",
                "minute_id": minute_id_str,
            },
        )

    return {"message": "Meeting minute deleted successfully"}
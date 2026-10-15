# chatup/backend/app/api/v1/calling.py
import uuid
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.user import User

router = APIRouter(prefix="/calling", tags=["Audio & Video Calling"])

# Only authorized club leaders can initiate group conference
CALL_HOST_ROLES = [
    "SUPER_ADMIN",
    "PRESIDENT",
    "VICE_PRESIDENT",
    "SECRETARY",
    "VICE_SECRETARY",
    "PLANNER",
    "VICE_PLANNER",
]

class InitiateCallRequest(BaseModel):
    room_id: str
    call_type: str = "video"
    target_user_id: Optional[uuid.UUID] = None

class DeclineCallRequest(BaseModel):
    room_id: str
    caller_id: uuid.UUID

@router.post("/initiate")
async def initiate_call(
    req: InitiateCallRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User does not belong to a club")

    # If it's a group call, enforce leadership role permissions
    if not req.target_user_id and current_user.role.value not in CALL_HOST_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only President, Secretary, or Planner can initiate club conference meetings",
        )

    tenant_id_str = str(current_user.tenant_id).lower()

    target_user_name = None
    if req.target_user_id:
        target_user = (await db.execute(select(User).where(User.id == req.target_user_id))).scalar_one_or_none()
        if not target_user:
            raise HTTPException(status_code=404, detail="Target user not found")
        target_user_name = target_user.full_name

    # Broadcast incoming call ring event to all tenant members
    payload = {
        "type": "INCOMING_CALL",
        "room_id": req.room_id,
        "caller_id": str(current_user.id),
        "caller_name": current_user.full_name,
        "caller_role": current_user.role.value,
        "call_type": req.call_type,
        "is_direct": bool(req.target_user_id),
        "target_user_id": str(req.target_user_id) if req.target_user_id else None,
        "target_user_name": target_user_name,
    }

    await manager.broadcast_to_tenant(tenant_id_str, payload)
    return {"status": "ringing", "room_id": req.room_id}

@router.post("/decline")
async def decline_call(
    req: DeclineCallRequest,
    current_user: User = Depends(get_current_user),
):
    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "CALL_DECLINED",
                "room_id": req.room_id,
                "declined_by_name": current_user.full_name,
            },
        )
    return {"status": "declined"}
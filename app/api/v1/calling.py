# chatup/backend/app/api/v1/calling.py
import uuid
from typing import Dict, Set, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import jwt

from app.core.database import get_db, AsyncSessionLocal
from app.core.deps import get_current_user
from app.core.security import SECRET_KEY, ALGORITHM
from app.core.ws_manager import manager
from app.models.user import User

router = APIRouter(prefix="/calling", tags=["Audio & Video Calling"])

# WebRTC Room Connection Tracker: { room_id: Set[WebSocket] }
call_rooms: Dict[str, Set[WebSocket]] = {}

class InitiateCallRequest(BaseModel):
    room_id: str
    call_type: str = "video"  # "video" or "audio"
    target_user_id: Optional[uuid.UUID] = None  # None for group call, UUID for 1-on-1 direct call

class DeclineCallRequest(BaseModel):
    room_id: str
    caller_id: uuid.UUID

# 1. Endpoint to initiate a call (Rings the receiver's screen in real time)
CALL_HOST_ROLES = [
    "SUPER_ADMIN",
    "PRESIDENT",
    "VICE_PRESIDENT",
    "SECRETARY",
    "VICE_SECRETARY",
    "PLANNER",
    "VICE_PLANNER",
]

@router.post("/initiate")
async def initiate_call(
    req: InitiateCallRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User does not belong to a club")

    # Group Call එකක් නම් නිලධාරීන්ට පමණක් අවසර දීම
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

# 2. Endpoint when receiver declines the call
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

# 3. WebRTC Live Signaling WebSocket (Handles Offer, Answer, ICE Candidates)
@router.websocket("/ws/{room_id}")
async def webrtc_signaling_endpoint(
    websocket: WebSocket,
    room_id: str,
    token: str = Query(...),
):
    try:
        payload_data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = uuid.UUID(payload_data.get("sub"))
    except Exception:
        await websocket.close(code=1008)
        return

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            await websocket.close(code=1008)
            return
        user_name = user.full_name
        user_role = user.role.value

    await websocket.accept()

    r_key = str(room_id).lower()
    if r_key not in call_rooms:
        call_rooms[r_key] = set()
    call_rooms[r_key].add(websocket)

    # Notify others that this user joined the room
    for conn in list(call_rooms[r_key]):
        if conn != websocket:
            try:
                await conn.send_json({
                    "type": "peer-joined",
                    "peer_id": str(user_id),
                    "peer_name": user_name,
                    "peer_role": user_role,
                })
            except Exception:
                pass

    try:
        while True:
            data = await websocket.receive_json()
            # Forward WebRTC signaling (offer, answer, candidate) to all other peers in room
            for conn in list(call_rooms[r_key]):
                if conn != websocket:
                    try:
                        await conn.send_json(data)
                    except Exception:
                        pass
    except WebSocketDisconnect:
        if r_key in call_rooms:
            call_rooms[r_key].discard(websocket)
            if not call_rooms[r_key]:
                del call_rooms[r_key]

        # Notify others that this user left
        if r_key in call_rooms:
            for conn in list(call_rooms[r_key]):
                try:
                    await conn.send_json({
                        "type": "peer-left",
                        "peer_id": str(user_id),
                        "peer_name": user_name,
                    })
                except Exception:
                    pass
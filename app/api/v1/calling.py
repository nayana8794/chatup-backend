# chatup/backend/app/api/v1/calling.py
import uuid
from typing import Dict, Optional
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

# WebRTC Room Connection Tracker: { room_id: {websocket: user_info} }
call_rooms: Dict[str, Dict[WebSocket, dict]] = {}

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
    caller_id: Optional[uuid.UUID] = None


class EndCallRequest(BaseModel):
    room_id: str


@router.get("/active")
async def get_active_club_call(
    current_user: User = Depends(get_current_user),
):
    if not current_user.tenant_id:
        return {"active": False, "room_id": None, "participant_count": 0}

    room_id = f"club-meeting-{str(current_user.tenant_id).lower()}"
    participants = call_rooms.get(room_id, {})
    count = len(participants)

    return {
        "active": count > 0,
        "room_id": room_id,
        "participant_count": count,
    }


@router.post("/initiate")
async def initiate_call(
    req: InitiateCallRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User does not belong to a club")

    if not req.target_user_id and current_user.role.value not in CALL_HOST_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only President, Secretary, or Planner can initiate club conference meetings",
        )

    tenant_id_str = str(current_user.tenant_id).lower()

    target_user_name = None
    if req.target_user_id:
        target_user = (
            await db.execute(select(User).where(User.id == req.target_user_id))
        ).scalar_one_or_none()
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
                "declined_by_id": str(current_user.id),
                "declined_by_name": current_user.full_name,
            },
        )
    return {"status": "declined"}


@router.post("/end")
async def end_call(
    req: EndCallRequest,
    current_user: User = Depends(get_current_user),
):
    if current_user.tenant_id:
        r_key = str(req.room_id).lower()
        remaining = len(call_rooms.get(r_key, {}))
        # Broadcast CALL_ENDED if no one is left or if a host/caller cancels the ringing call
        if remaining <= 1 or current_user.role.value in CALL_HOST_ROLES:
            await manager.broadcast_to_tenant(
                str(current_user.tenant_id).lower(),
                {
                    "type": "CALL_ENDED",
                    "room_id": req.room_id,
                    "ended_by_id": str(current_user.id),
                    "ended_by_name": current_user.full_name,
                },
            )
    return {"status": "ended"}


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
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            await websocket.close(code=1008)
            return
        user_name = user.full_name
        user_role = user.role.value
        tenant_id_str = str(user.tenant_id).lower() if user.tenant_id else None

    await websocket.accept()

    r_key = str(room_id).lower()
    if r_key not in call_rooms:
        call_rooms[r_key] = {}

    # Send existing peers list to the newly joined user so their participant list is accurate
    for conn, info in list(call_rooms[r_key].items()):
        if conn != websocket:
            try:
                await websocket.send_json({
                    "type": "existing-peer",
                    "peer_id": info["user_id"],
                    "peer_name": info["user_name"],
                    "peer_role": info["user_role"],
                })
            except Exception:
                pass

    call_rooms[r_key][websocket] = {
        "user_id": str(user_id),
        "user_name": user_name,
        "user_role": user_role,
        "tenant_id": tenant_id_str,
    }

    # Notify existing peers that a new person joined
    for conn, info in list(call_rooms[r_key].items()):
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
            msg_type = data.get("type")

            if msg_type == "host-mute-peer":
                target_peer_id = data.get("target_peer_id")
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"] == target_peer_id:
                        try:
                            await conn.send_json({"type": "force-mute"})
                        except Exception:
                            pass
                continue

            if msg_type == "host-kick-peer":
                target_peer_id = data.get("target_peer_id")
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"] == target_peer_id:
                        try:
                            await conn.send_json({"type": "force-kick"})
                            await conn.close()
                        except Exception:
                            pass
                continue

            for conn in list(call_rooms[r_key].keys()):
                if conn != websocket:
                    try:
                        await conn.send_json(data)
                    except Exception:
                        pass
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        room_became_empty = False
        if r_key in call_rooms:
            call_rooms[r_key].pop(websocket, None)
            if not call_rooms[r_key]:
                del call_rooms[r_key]
                room_became_empty = True

        # Notify remaining peers in the room that this peer left
        if r_key in call_rooms:
            for conn in list(call_rooms[r_key].keys()):
                try:
                    await conn.send_json({
                        "type": "peer-left",
                        "peer_id": str(user_id),
                        "peer_name": user_name,
                    })
                except Exception:
                    pass

        # CRITICAL FIX: If the room is now empty (e.g. President hung up before or after call),
        # broadcast CALL_ENDED to the whole tenant so any ringing IncomingCallModal closes immediately!
        if room_became_empty and tenant_id_str:
            try:
                await manager.broadcast_to_tenant(
                    tenant_id_str,
                    {
                        "type": "CALL_ENDED",
                        "room_id": room_id,
                        "ended_by_id": str(user_id),
                    },
                )
            except Exception:
                pass
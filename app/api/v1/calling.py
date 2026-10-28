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

call_rooms: Dict[str, Dict[WebSocket, dict]] = {}
call_room_meta: Dict[str, dict] = {}

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
        return {"active": False, "room_id": None, "participant_count": 0, "call_type": "video"}

    room_id = f"club-meeting-{str(current_user.tenant_id).lower()}"
    participants = call_rooms.get(room_id, {})
    count = len(participants)

    meta = call_room_meta.get(room_id, {})
    call_type = meta.get("call_type", "video")

    return {
        "active": count > 0,
        "room_id": room_id,
        "participant_count": count,
        "call_type": call_type,
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
            detail="Only President or Club Officers can initiate club conference meetings",
        )

    tenant_id_str = str(current_user.tenant_id).lower()
    r_key = str(req.room_id).lower()
    clean_call_type = req.call_type.lower()

    call_room_meta[r_key] = {
        "call_type": clean_call_type,
        "host_id": str(current_user.id),
        "is_direct": bool(req.target_user_id),
    }

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
        "call_type": clean_call_type,
        "is_direct": bool(req.target_user_id),
        "target_user_id": str(req.target_user_id) if req.target_user_id else None,
        "target_user_name": target_user_name,
    }

    await manager.broadcast_to_tenant(tenant_id_str, payload)
    return {"status": "ringing", "room_id": req.room_id, "call_type": clean_call_type}


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
    r_key = str(req.room_id).lower()

    if r_key in call_room_meta:
        del call_room_meta[r_key]

    if r_key in call_rooms:
        for conn in list(call_rooms[r_key].keys()):
            try:
                await conn.send_json({
                    "type": "meeting-ended-by-host",
                    "message": "The meeting was ended by the President / Host.",
                })
            except Exception:
                pass
        call_rooms[r_key].clear()
        del call_rooms[r_key]

    if current_user.tenant_id:
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

    is_host = user_role in CALL_HOST_ROLES
    meta = call_room_meta.get(r_key, {})
    room_call_type = meta.get("call_type", "video")

    try:
        await websocket.send_json({
            "type": "room-info",
            "call_type": room_call_type,
        })
    except Exception:
        pass

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
        "is_host": is_host,
    }

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
            target_peer_id = data.get("target_peer_id")

            if msg_type == "host-end-meeting" and is_host:
                for conn in list(call_rooms[r_key].keys()):
                    try:
                        await conn.send_json({
                            "type": "meeting-ended-by-host",
                            "message": "The meeting was ended by the President / Host.",
                        })
                    except Exception:
                        pass
                if r_key in call_room_meta:
                    del call_room_meta[r_key]
                if tenant_id_str:
                    await manager.broadcast_to_tenant(
                        tenant_id_str,
                        {"type": "CALL_ENDED", "room_id": room_id, "ended_by_id": str(user_id)},
                    )
                break

            # Host Control: Force Mute Peer Mic
            if msg_type == "host-mute-peer" and is_host:
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"] == target_peer_id:
                        try:
                            await conn.send_json({"type": "force-mute"})
                        except Exception:
                            pass
                continue

            # Host Control: Force Turn Off Peer Camera
            if msg_type == "host-stop-video-peer" and is_host:
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"] == target_peer_id:
                        try:
                            await conn.send_json({"type": "force-stop-video"})
                        except Exception:
                            pass
                continue

            # Host Control: Force Kick Peer from Meeting
            if msg_type == "host-kick-peer" and is_host:
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"] == target_peer_id:
                        try:
                            await conn.send_json({"type": "force-kick"})
                            await conn.close()
                        except Exception:
                            pass
                continue

            # Targeted Peer Routing
            if target_peer_id:
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"].lower() == str(target_peer_id).lower():
                        try:
                            await conn.send_json(data)
                        except Exception:
                            pass
            else:
                for conn in list(call_rooms[r_key].keys()):
                    if conn != websocket:
                        try:
                            await conn.send_json(data)
                        except Exception:
                            pass
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        was_host_leaving = is_host and "club-meeting" in r_key

        if r_key in call_rooms:
            call_rooms[r_key].pop(websocket, None)

            if was_host_leaving and call_rooms[r_key]:
                for conn in list(call_rooms[r_key].keys()):
                    try:
                        await conn.send_json({
                            "type": "meeting-ended-by-host",
                            "message": "The President has left. The meeting has ended.",
                        })
                    except Exception:
                        pass
                call_rooms[r_key].clear()
                if r_key in call_room_meta:
                    del call_room_meta[r_key]

            if not call_rooms[r_key]:
                del call_rooms[r_key]
                if r_key in call_room_meta:
                    del call_room_meta[r_key]

        if was_host_leaving and tenant_id_str:
            try:
                await manager.broadcast_to_tenant(
                    tenant_id_str,
                    {"type": "CALL_ENDED", "room_id": room_id, "ended_by_id": str(user_id)},
                )
            except Exception:
                pass

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
# chatup/backend/app/api/v1/calling.py
import uuid
from typing import Dict, Optional, Set
from datetime import datetime
from pydantic import BaseModel
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Query,
    BackgroundTasks,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import jwt

from app.core.database import get_db, AsyncSessionLocal
from app.core.deps import get_current_user
from app.core.security import SECRET_KEY, ALGORITHM
from app.core.ws_manager import manager
from app.core.push_service import dispatch_push_to_users
from app.models.user import User

router = APIRouter(prefix="/calling", tags=["Audio & Video Calling"])

call_rooms: Dict[str, Dict[WebSocket, dict]] = {}
call_room_meta: Dict[str, dict] = {}
active_call_sessions: Dict[str, dict] = {}

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


# FIX A: Active Call Status Verification Endpoint for Mobile Clients
@router.get("/room-status")
async def get_room_call_status(
    room_id: str,
    current_user: User = Depends(get_current_user),
):
    r_key = str(room_id).lower()
    is_active = False

    # A room is only truly active if it has active WebSockets connected or active host session
    if r_key in call_rooms and len(call_rooms[r_key]) > 0:
        is_active = True
    elif r_key in active_call_sessions:
        # Check if call was created within last 60 seconds (ringing window)
        created_at = active_call_sessions[r_key].get("created_at")
        if created_at and (datetime.utcnow().replace(tzinfo=None) - created_at).total_seconds() < 60:
            is_active = True

    meta = call_room_meta.get(r_key, {})
    return {
        "is_active": is_active,
        "room_id": room_id,
        "call_type": meta.get("call_type", "video"),
    }


@router.post("/initiate")
async def initiate_call(
    req: InitiateCallRequest,
    background_tasks: BackgroundTasks,
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

    if r_key not in call_room_meta:
        call_room_meta[r_key] = {
            "call_type": clean_call_type,
            "host_id": str(current_user.id),
            "is_direct": bool(req.target_user_id),
        }
    else:
        clean_call_type = call_room_meta[r_key].get("call_type", clean_call_type)

    target_user_name = None
    if req.target_user_id:
        target_user = (
            await db.execute(select(User).where(User.id == req.target_user_id))
        ).scalar_one_or_none()
        if not target_user:
            raise HTTPException(status_code=404, detail="Target user not found")
        target_user_name = target_user.full_name

    active_call_sessions[r_key] = {
        "caller_id": current_user.id,
        "caller_name": current_user.full_name,
        "call_type": clean_call_type,
        "target_user_id": req.target_user_id,
        "answered_user_ids": set(),
        "created_at": datetime.utcnow().replace(tzinfo=None),
    }

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

    # 1. Real-time WebSocket Broadcast
    await manager.broadcast_to_tenant(tenant_id_str, payload)

    # 2. Universal iOS / Android Web Push Dispatch
    call_url = f"/calling?room={req.room_id}&type={clean_call_type}&autoJoin=true"

    if req.target_user_id:
        background_tasks.add_task(
            dispatch_push_to_users,
            user_ids=[req.target_user_id],
            title=f"📞 Incoming {clean_call_type.capitalize()} Call",
            body=f"{current_user.full_name} is calling you...",
            url=call_url,
            tag=f"call-{r_key}",
            badge_count=1,
            data={
                "type": "INCOMING_CALL",
                "room_id": req.room_id,
                "call_type": clean_call_type,
                "caller_name": current_user.full_name,
            },
        )
    else:
        club_members = (
            await db.execute(
                select(User.id).where(
                    User.tenant_id == current_user.tenant_id,
                    User.id != current_user.id,
                    User.is_active == True,
                )
            )
        ).scalars().all()
        if club_members:
            background_tasks.add_task(
                dispatch_push_to_users,
                user_ids=list(club_members),
                title="🦁 Live Club Meeting",
                body=f"{current_user.full_name} started a {clean_call_type} meeting. Tap to join.",
                url=call_url,
                tag=f"call-{r_key}",
                badge_count=1,
                data={
                    "type": "INCOMING_CALL",
                    "room_id": req.room_id,
                    "call_type": clean_call_type,
                    "caller_name": current_user.full_name,
                },
            )

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
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r_key = str(req.room_id).lower()

    if r_key in call_room_meta:
        del call_room_meta[r_key]

    if r_key in call_rooms:
        for conn in list(call_rooms[r_key].keys()):
            try:
                await conn.send_json({
                    "type": "meeting-ended-by-host",
                    "message": "The meeting was ended.",
                })
            except Exception:
                pass
        call_rooms[r_key].clear()
        del call_rooms[r_key]

    # Handle Missed Calls
    session = active_call_sessions.pop(r_key, None)
    if session and session.get("target_user_id"):
        target_uid = session["target_user_id"]
        answered_set = session.get("answered_user_ids", set())

        if str(target_uid).lower() not in answered_set:
            background_tasks.add_task(
                dispatch_push_to_users,
                user_ids=[target_uid],
                title="❌ Missed Call",
                body=f"You missed a {session['call_type']} call from {session['caller_name']}.",
                url="/calling",
                tag=f"call-{r_key}",
                badge_count=1,
                data={
                    "type": "MISSED_CALL",
                    "room_id": req.room_id,
                    "caller_id": str(session["caller_id"]),
                    "caller_name": session["caller_name"],
                },
            )

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

    if r_key in active_call_sessions:
        active_call_sessions[r_key]["answered_user_ids"].add(str(user_id).lower())

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

            if msg_type == "host-mute-peer" and is_host:
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"] == target_peer_id:
                        try:
                            await conn.send_json({"type": "force-mute"})
                        except Exception:
                            pass
                continue

            if msg_type == "host-stop-video-peer" and is_host:
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"] == target_peer_id:
                        try:
                            await conn.send_json({"type": "force-stop-video"})
                        except Exception:
                            pass
                continue

            if msg_type == "host-kick-peer" and is_host:
                for conn, info in list(call_rooms[r_key].items()):
                    if info["user_id"] == target_peer_id:
                        try:
                            await conn.send_json({"type": "force-kick"})
                            await conn.close()
                        except Exception:
                            pass
                continue

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
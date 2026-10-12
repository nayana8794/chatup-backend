# chatup/backend/app/api/v1/chat.py
import uuid
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func
import jwt

from app.core.database import get_db, AsyncSessionLocal
from app.core.deps import get_current_user
from app.core.security import SECRET_KEY, ALGORITHM
from app.core.ws_manager import manager
from app.models.chat import ChatRoom, ChatParticipant, ChatMessage, RoomType, ChatReadReceipt
from app.models.user import User, UserRole
from app.schemas.chat import ChatRoomResponse, MessageResponse, CreateDirectChatRequest

router = APIRouter(prefix="/chat", tags=["Real-time Chat"])

BOARD_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
    UserRole.SECRETARY,
    UserRole.VICE_SECRETARY,
    UserRole.PLANNER,
    UserRole.VICE_PLANNER,
    UserRole.TREASURER,
    UserRole.VICE_TREASURER,
]

# Helper to automatically ensure General and Board rooms exist for a club
async def ensure_club_default_rooms(tenant_id: uuid.UUID, db: AsyncSession):
    general_room = (
        await db.execute(
            select(ChatRoom).where(ChatRoom.tenant_id == tenant_id, ChatRoom.room_type == RoomType.GENERAL)
        )
    ).scalar_one_or_none()

    if not general_room:
        db.add(ChatRoom(tenant_id=tenant_id, name="General Club Chat", room_type=RoomType.GENERAL))

    board_room = (
        await db.execute(
            select(ChatRoom).where(ChatRoom.tenant_id == tenant_id, ChatRoom.room_type == RoomType.BOARD)
        )
    ).scalar_one_or_none()

    if not board_room:
        db.add(ChatRoom(tenant_id=tenant_id, name="Board Members Only", room_type=RoomType.BOARD))

    await db.commit()

@router.get("/rooms", response_model=List[ChatRoomResponse])
async def get_user_chat_rooms(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        return []

    await ensure_club_default_rooms(current_user.tenant_id, db)

    allowed_types = [RoomType.GENERAL]
    if current_user.role in BOARD_ROLES:
        allowed_types.append(RoomType.BOARD)

    rooms_query = select(ChatRoom).where(
        ChatRoom.tenant_id == current_user.tenant_id,
        ChatRoom.room_type.in_(allowed_types),
    )
    public_rooms = list((await db.execute(rooms_query)).scalars().all())

    direct_query = (
        select(ChatRoom)
        .join(ChatParticipant, ChatRoom.id == ChatParticipant.room_id)
        .where(
            ChatRoom.tenant_id == current_user.tenant_id,
            ChatRoom.room_type == RoomType.DIRECT,
            ChatParticipant.user_id == current_user.id,
        )
    )
    direct_rooms = list((await db.execute(direct_query)).scalars().all())

    all_rooms = public_rooms + direct_rooms
    response_list = []

    for r in all_rooms:
        display_name = r.name
        if r.room_type == RoomType.DIRECT:
            other_p = (
                await db.execute(
                    select(User)
                    .join(ChatParticipant, User.id == ChatParticipant.user_id)
                    .where(ChatParticipant.room_id == r.id, User.id != current_user.id)
                )
            ).scalar_one_or_none()
            display_name = other_p.full_name if other_p else "Direct Chat"

        last_msg = (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.room_id == r.id)
                .order_by(desc(ChatMessage.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

        # Unread messages ගණනය කිරීම (scalars().first() භාවිතා කර MultipleResultsFound වැළැක්වීම)
        read_receipt = (
            await db.execute(
                select(ChatReadReceipt)
                .where(
                    ChatReadReceipt.room_id == r.id,
                    ChatReadReceipt.user_id == current_user.id,
                )
                .order_by(desc(ChatReadReceipt.last_read_at))
            )
        ).scalars().first()

        unread_filter = [ChatMessage.room_id == r.id, ChatMessage.sender_id != current_user.id]
        if read_receipt:
            unread_filter.append(ChatMessage.created_at > read_receipt.last_read_at)

        unread_count = (
            await db.execute(select(func.count(ChatMessage.id)).where(and_(*unread_filter)))
        ).scalar_one()

        response_list.append(
            ChatRoomResponse(
                id=r.id,
                name=display_name or "Chat Room",
                room_type=r.room_type,
                last_message=last_msg.content if last_msg else None,
                last_message_time=last_msg.created_at if last_msg else None,
                unread_count=unread_count,
            )
        )

    return response_list

@router.post("/rooms/{room_id}/read")
async def mark_room_as_read(
    room_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    # පවතින සියලු receipts සොයා බැලීම
    receipts = (
        await db.execute(
            select(ChatReadReceipt)
            .where(
                ChatReadReceipt.room_id == room_id,
                ChatReadReceipt.user_id == current_user.id,
            )
            .order_by(desc(ChatReadReceipt.last_read_at))
        )
    ).scalars().all()

    if receipts:
        # නවතම receipt එක update කිරීම
        receipts[0].last_read_at = now
        # අතිරික්ත duplicate receipts තිබේ නම් database එකෙන් මකාදැමීම
        for duplicate in receipts[1:]:
            await db.delete(duplicate)
    else:
        db.add(ChatReadReceipt(room_id=room_id, user_id=current_user.id, last_read_at=now))

    await db.commit()
    return {"status": "success", "room_id": str(room_id)}

@router.get("/rooms/{room_id}/messages", response_model=List[MessageResponse])
async def get_room_messages(
    room_id: uuid.UUID,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(ChatMessage, User.full_name, User.role)
        .outerjoin(User, ChatMessage.sender_id == User.id)
        .where(ChatMessage.room_id == room_id)
        .order_by(desc(ChatMessage.created_at))
        .limit(limit)
    )
    result = await db.execute(query)
    messages = list(reversed(result.all()))

    return [
        MessageResponse(
            id=msg.id,
            room_id=msg.room_id,
            sender_id=msg.sender_id,
            sender_name=name or "Unknown",
            sender_role=role.value if role else "MEMBER",
            content=msg.content,
            created_at=msg.created_at,
        )
        for msg, name, role in messages
    ]

@router.post("/direct", response_model=ChatRoomResponse)
async def get_or_create_direct_chat(
    req: CreateDirectChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.target_user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot chat with yourself")

    target_user = (await db.execute(select(User).where(User.id == req.target_user_id))).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")

    my_direct_rooms = (
        await db.execute(
            select(ChatParticipant.room_id)
            .join(ChatRoom, ChatParticipant.room_id == ChatRoom.id)
            .where(ChatParticipant.user_id == current_user.id, ChatRoom.room_type == RoomType.DIRECT)
        )
    ).scalars().all()

    target_direct_rooms = (
        await db.execute(
            select(ChatParticipant.room_id)
            .join(ChatRoom, ChatParticipant.room_id == ChatRoom.id)
            .where(ChatParticipant.user_id == target_user.id, ChatRoom.room_type == RoomType.DIRECT)
        )
    ).scalars().all()

    common_rooms = set(my_direct_rooms).intersection(set(target_direct_rooms))

    if common_rooms:
        room_id = list(common_rooms)[0]
        return ChatRoomResponse(id=room_id, name=target_user.full_name, room_type=RoomType.DIRECT, unread_count=0)

    new_room = ChatRoom(tenant_id=current_user.tenant_id, room_type=RoomType.DIRECT)
    db.add(new_room)
    await db.flush()

    db.add(ChatParticipant(room_id=new_room.id, user_id=current_user.id))
    db.add(ChatParticipant(room_id=new_room.id, user_id=target_user.id))
    await db.commit()

    return ChatRoomResponse(id=new_room.id, name=target_user.full_name, room_type=RoomType.DIRECT, unread_count=0)

# ================= LIVE ROOM WEBSOCKET ENDPOINT =================
@router.websocket("/ws/{room_id}")
async def chat_websocket_endpoint(
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

    await manager.connect_room(room_id, websocket)

    try:
        while True:
            data = await websocket.receive_json()
            content = data.get("content", "").strip()

            if content:
                async with AsyncSessionLocal() as db:
                    now = datetime.utcnow()
                    msg = ChatMessage(
                        room_id=uuid.UUID(room_id),
                        sender_id=user.id,
                        content=content,
                        created_at=now,
                    )
                    db.add(msg)
                    await db.commit()
                    await db.refresh(msg)

                    payload = {
                        "type": "NEW_CHAT_MESSAGE",
                        "id": str(msg.id),
                        "room_id": str(msg.room_id),
                        "sender_id": str(user.id),
                        "sender_name": user.full_name,
                        "sender_role": user.role.value,
                        "content": msg.content,
                        "created_at": msg.created_at.isoformat(),
                    }

                # 1. කාමරයේ සිටින අයට පණිවිඩය සෘජුවම යැවීම
                await manager.broadcast_to_room(room_id, payload)
                # 2. Club එකේ සියලු දෙනාට Notification එක, Sound Chime එක සහ Unread Badges යැවීම
                if user.tenant_id:
                    await manager.broadcast_to_tenant(str(user.tenant_id), payload)

    except WebSocketDisconnect:
        manager.disconnect_room(room_id, websocket)

# ================= GLOBAL REAL-TIME SYNC WEBSOCKET =================
@router.websocket("/ws/global/sync")
async def global_realtime_sync_endpoint(
    websocket: WebSocket,
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
        if not user or not user.tenant_id:
            await websocket.close(code=1008)
            return
        tenant_id_str = str(user.tenant_id)

    await manager.connect_tenant(tenant_id_str, websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_tenant(tenant_id_str, websocket)
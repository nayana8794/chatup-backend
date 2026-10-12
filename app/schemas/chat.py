# chatup/backend/app/schemas/chat.py
import uuid
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from app.models.chat import RoomType

class MessageResponse(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    sender_id: Optional[uuid.UUID]
    sender_name: str
    sender_role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True

class ChatRoomResponse(BaseModel):
    id: uuid.UUID
    name: str
    room_type: RoomType
    last_message: Optional[str] = None
    last_message_time: Optional[datetime] = None
    unread_count: int = 0

class CreateDirectChatRequest(BaseModel):
    target_user_id: uuid.UUID
# chatup/backend/app/schemas/meeting_minute.py
import uuid
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from app.models.meeting_minute import MinuteStatus

class MinuteCreateRequest(BaseModel):
    title: str
    meeting_date: datetime
    location: Optional[str] = None
    attendees: Optional[str] = None
    content: str
    status: MinuteStatus = MinuteStatus.DRAFT

class MinuteUpdateRequest(BaseModel):
    title: Optional[str] = None
    meeting_date: Optional[datetime] = None
    location: Optional[str] = None
    attendees: Optional[str] = None
    content: Optional[str] = None
    status: Optional[MinuteStatus] = None

class MinuteResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    author_id: Optional[uuid.UUID]
    author_name: Optional[str] = None
    title: str
    meeting_date: datetime
    location: Optional[str]
    attendees: Optional[str]
    content: str
    status: MinuteStatus
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
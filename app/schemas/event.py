# chatup/backend/app/schemas/event.py
import uuid
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from app.models.event import EventType, RSVPStatus

class EventCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    event_type: EventType = EventType.REGULAR_MEETING
    start_time: datetime
    end_time: Optional[datetime] = None
    location: str
    is_online: bool = False
    meeting_link: Optional[str] = None

class EventUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    event_type: Optional[EventType] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    is_online: Optional[bool] = None
    meeting_link: Optional[str] = None

class SetRSVPRequest(BaseModel):
    status: RSVPStatus

class AttendeeInfo(BaseModel):
    user_id: uuid.UUID
    full_name: str
    role: str
    status: RSVPStatus

class EventResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    planner_id: Optional[uuid.UUID]
    planner_name: Optional[str] = None
    title: str
    description: Optional[str]
    event_type: EventType
    start_time: datetime
    end_time: Optional[datetime]
    location: str
    is_online: bool
    meeting_link: Optional[str]
    created_at: datetime
    going_count: int = 0
    maybe_count: int = 0
    declined_count: int = 0
    my_rsvp: Optional[RSVPStatus] = None

    class Config:
        from_attributes = True
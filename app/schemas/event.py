# chatup/backend/app/schemas/event.py
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.event import EventType, RSVPStatus

class DutyShiftItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    start_time: str
    end_time: str
    max_volunteers: Optional[int] = None
    assigned_count: Optional[int] = 0

class EventCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    event_type: EventType = EventType.REGULAR_MEETING
    start_time: datetime
    end_time: Optional[datetime] = None
    location: str
    is_online: bool = False
    meeting_link: Optional[str] = None
    allows_guests: bool = False
    duty_shifts: Optional[List[DutyShiftItem]] = []

class EventUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    event_type: Optional[EventType] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    is_online: Optional[bool] = None
    meeting_link: Optional[str] = None
    allows_guests: Optional[bool] = None
    duty_shifts: Optional[List[DutyShiftItem]] = None

class SetRSVPRequest(BaseModel):
    status: RSVPStatus
    guest_count: Optional[int] = 0
    duty_shift_id: Optional[str] = None
    is_virtual_attending: Optional[bool] = False

class AttendeeInfo(BaseModel):
    user_id: uuid.UUID
    full_name: str
    role: str
    status: RSVPStatus
    guest_count: int = 0
    duty_shift_id: Optional[str] = None
    duty_shift_title: Optional[str] = None

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
    allows_guests: bool = False
    duty_shifts: List[DutyShiftItem] = []
    created_at: datetime
    going_count: int = 0
    maybe_count: int = 0
    declined_count: int = 0
    total_guests_count: int = 0
    my_rsvp: Optional[RSVPStatus] = None
    my_guest_count: int = 0
    my_duty_shift_id: Optional[str] = None

    class Config:
        from_attributes = True
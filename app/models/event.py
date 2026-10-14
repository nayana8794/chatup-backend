# chatup/backend/app/models/event.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base

class EventType(str, enum.Enum):
    REGULAR_MEETING = "REGULAR_MEETING"    # සාමාන්‍ය මාසික රැස්වීම්
    BOARD_MEETING = "BOARD_MEETING"        # නිලධාරීන්ගේ මණ්ඩල රැස්වීම්
    CHARITY_PROJECT = "CHARITY_PROJECT"    # ප්‍රජා සත්කාරක වැඩසටහන්
    SOCIAL_EVENT = "SOCIAL_EVENT"          # සුහද හමුවීම් / උත්සව

class RSVPStatus(str, enum.Enum):
    GOING = "GOING"          # සහභාගී වේ
    MAYBE = "MAYBE"          # අවිනිශ්චිතයි
    DECLINED = "DECLINED"    # සහභාගී විය නොහැක

class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    planner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    event_type = Column(Enum(EventType), default=EventType.REGULAR_MEETING, nullable=False)
    
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    location = Column(String(255), nullable=False) # e.g. "Helsinki City Hall"
    is_online = Column(Boolean, default=False)
    meeting_link = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    planner = relationship("User")
    rsvps = relationship("EventRSVP", back_populates="event", cascade="all, delete-orphan")

class EventRSVP(Base):
    __tablename__ = "event_rsvps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(Enum(RSVPStatus), default=RSVPStatus.GOING, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    event = relationship("Event", back_populates="rsvps")
    user = relationship("User")

    # එක් සාමාජිකයෙකුට එක් Event එකක් සඳහා එක් RSVP record එකක් පමණක් තිබිය යුතුය
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_user_rsvp"),
    )
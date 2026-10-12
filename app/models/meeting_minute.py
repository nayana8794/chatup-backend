# chatup/backend/app/models/meeting_minute.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base

class MinuteStatus(str, enum.Enum):
    DRAFT = "DRAFT"          # Secretary ලියමින් සිටින කෙටුම්පත (සාමාජිකයින්ට නොපෙනේ)
    PUBLISHED = "PUBLISHED"  # නිල වශයෙන් ප්‍රකාශයට පත් කළ වාර්තාව (සියලු සාමාජිකයින්ට පෙනේ)

class MeetingMinute(Base):
    __tablename__ = "meeting_minutes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    meeting_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    location = Column(String(255), nullable=True) # e.g. "Club House, Helsinki"
    attendees = Column(Text, nullable=True)       # e.g. "Matti (President), Johan (Member)..."
    
    # MS Word ආකාරයේ Rich Text HTML content එක ගබඩා වන field එක
    content = Column(Text, nullable=False)
    
    status = Column(Enum(MinuteStatus), default=MinuteStatus.DRAFT, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    author = relationship("User")
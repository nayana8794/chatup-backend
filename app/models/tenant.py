# chatup/backend/app/models/tenant.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, index=True, nullable=False) # e.g. "helsinki-lions"
    district = Column(String(100), nullable=True)
    logo_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)

    # Super Admin Granular Feature Flags (JSONB)
    # Allows Super Admin to turn features ON/OFF per club
    features = Column(JSONB, default={
        "video_calls": True,
        "mom_editor": True,
        "social_feed": True,
        "dues_module": True,
        "max_members": 50
    })

    # Club-specific custom branding & night mode defaults
    theme_config = Column(JSONB, default={
        "primary_color": "#002F6C",    # Classic Lions Navy Blue
        "secondary_color": "#F2A900",  # Classic Lions Gold
        "dark_mode": False
    })

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to members
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
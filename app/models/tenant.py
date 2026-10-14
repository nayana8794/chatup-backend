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
    slug = Column(String(100), unique=True, index=True, nullable=False)
    district = Column(String(100), nullable=True)
    logo_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)

    features = Column(JSONB, default={
        "video_calls": True,
        "mom_editor": True,
        "social_feed": True,
        "dues_module": True,
        "max_members": 50
    })

    theme_config = Column(JSONB, default={
        "primary_color": "#002F6C",
        "secondary_color": "#F2A900",
        "dark_mode": False
    })

    # Club Official Banking & MobilePay Details
    bank_details = Column(JSONB, default={
        "recipient_name": "Lions Club Helsinki Ry",
        "iban": "FI42 1234 5678 9012 34",
        "bic": "DABAFIHH",
        "mobilepay_number": "12345"
    })

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
# chatup/backend/app/models/user.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    DISTRICT_GOVERNOR = "DISTRICT_GOVERNOR"
    PRESIDENT = "PRESIDENT"
    VICE_PRESIDENT = "VICE_PRESIDENT"
    SECRETARY = "SECRETARY"
    VICE_SECRETARY = "VICE_SECRETARY"
    PLANNER = "PLANNER"
    VICE_PLANNER = "VICE_PLANNER"
    TREASURER = "TREASURER"
    VICE_TREASURER = "VICE_TREASURER"
    MEMBER = "MEMBER"


class LanguagePreference(str, enum.Enum):
    EN = "en"
    FI = "fi"
    SV = "sv"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)

    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    phone_number = Column(String(50), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    avatar_url = Column(String(500), nullable=True)

    role = Column(Enum(UserRole), default=UserRole.MEMBER, nullable=False)
    language = Column(Enum(LanguagePreference), default=LanguagePreference.EN, nullable=False)

    # Jurisdiction district for District Governor (e.g. "District 107-N (Helsinki Metropolitan)")
    district = Column(String(100), nullable=True, index=True)

    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tenant = relationship("Tenant", back_populates="users")
# chatup/backend/app/models/document.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Enum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


class DocumentCategory(str, enum.Enum):
    BYLAWS = "BYLAWS"
    DISTRICT = "DISTRICT"
    FINANCIAL = "FINANCIAL"
    CERTIFICATES = "CERTIFICATES"
    PROJECTS = "PROJECTS"
    GENERAL = "GENERAL"


class ClubDocument(Base):
    __tablename__ = "club_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uploader_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(Enum(DocumentCategory), default=DocumentCategory.GENERAL, nullable=False, index=True)
    
    file_url = Column(String(1000), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_size_bytes = Column(Integer, default=0, nullable=False)
    file_type = Column(String(50), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tenant = relationship("Tenant", backref="documents")
    uploader = relationship("User", foreign_keys=[uploader_id])
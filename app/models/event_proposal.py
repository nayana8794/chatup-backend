# chatup/backend/app/models/event_proposal.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Float, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from app.core.database import Base


class ProposalStatus(str, enum.Enum):
    PENDING_ENDORSEMENT = "PENDING_ENDORSEMENT"
    PENDING_PRESIDENT_APPROVAL = "PENDING_PRESIDENT_APPROVAL"
    APPROVED_AND_PUBLISHED = "APPROVED_AND_PUBLISHED"
    REJECTED = "REJECTED"


class EventProposal(BaseModel if False else Base):  # type: ignore
    __tablename__ = "event_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    proposer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    proposer_name = Column(String(255), nullable=False)
    
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    proposed_date = Column(DateTime, nullable=False)
    location = Column(String(255), nullable=True)
    estimated_budget = Column(Float, default=0.0)
    
    status = Column(SQLEnum(ProposalStatus), default=ProposalStatus.PENDING_ENDORSEMENT, nullable=False)
    
    # List of member user IDs who endorsed/seconded this proposal
    endorsed_user_ids = Column(JSONB, default=list, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
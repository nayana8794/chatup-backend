# chatup/backend/app/schemas/proposal.py
import uuid
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime


class ProposalCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    proposed_date: datetime
    location: Optional[str] = None
    estimated_budget: Optional[float] = 0.0


class ProposalResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    proposer_id: uuid.UUID
    proposer_name: str
    title: str
    description: Optional[str] = None
    proposed_date: datetime
    location: Optional[str] = None
    estimated_budget: float
    status: str
    endorsed_user_ids: List[str] = []
    created_at: datetime

    class Config:
        from_attributes = True
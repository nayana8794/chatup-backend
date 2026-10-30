# chatup/backend/app/schemas/poll.py
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from app.models.poll import PollType, PollStatus


class PollOptionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class PollOptionResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str] = None
    vote_count: int
    percentage: float = 0.0

    class Config:
        from_attributes = True


class PollCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    poll_type: PollType = PollType.SINGLE_CHOICE
    is_secret: bool = True
    expires_at: Optional[datetime] = None
    options: List[PollOptionCreate] = Field(..., min_items=2)


class PollVoteRequest(BaseModel):
    selected_option_ids: List[uuid.UUID] = Field(..., min_items=1)


class PollResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    creator_id: Optional[uuid.UUID] = None
    creator_name: Optional[str] = None
    title: str
    description: Optional[str] = None
    poll_type: PollType
    status: PollStatus
    is_secret: bool
    total_votes: int
    has_voted: bool
    expires_at: Optional[datetime] = None
    created_at: datetime
    options: List[PollOptionResponse]

    class Config:
        from_attributes = True
# chatup/backend/app/schemas/tenant.py
import uuid
from typing import Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime

class ClubFeatures(BaseModel):
    video_calls: bool = True
    mom_editor: bool = True
    social_feed: bool = True
    dues_module: bool = True
    max_members: int = 50

class ClubCreateRequest(BaseModel):
    name: str
    slug: str
    district: Optional[str] = None
    features: Optional[ClubFeatures] = ClubFeatures()

class ClubUpdateRequest(BaseModel):
    name: Optional[str] = None
    district: Optional[str] = None
    is_active: Optional[bool] = None

class ClubResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    district: Optional[str] = None
    is_active: bool
    features: Dict[str, Any]
    theme_config: Dict[str, Any]
    created_at: datetime
    member_count: int = 0

    class Config:
        from_attributes = True
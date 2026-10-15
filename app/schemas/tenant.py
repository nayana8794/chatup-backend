import uuid
from typing import Optional, Dict, Any
from pydantic import BaseModel, EmailStr
from datetime import datetime

class ClubFeatures(BaseModel):
    video_calls: bool = True
    mom_editor: bool = True
    social_feed: bool = True
    dues_module: bool = True
    max_members: int = 50

class CredentialsInfo(BaseModel):
    club_name: str
    president_name: str
    email: str
    temp_password: str

class ClubPresidentInfo(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone_number: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True

class ClubPresidentUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None

class ClubCreateRequest(BaseModel):
    name: str
    slug: str
    district: Optional[str] = None
    features: Optional[ClubFeatures] = ClubFeatures()
    president_name: Optional[str] = None
    president_email: Optional[EmailStr] = None
    president_phone: Optional[str] = None

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
    president: Optional[ClubPresidentInfo] = None
    credentials: Optional[CredentialsInfo] = None

    class Config:
        from_attributes = True

class AssignPresidentRequest(BaseModel):
    full_name: str
    email: EmailStr
    phone_number: Optional[str] = None

class AssignPresidentResponse(BaseModel):
    message: str
    credentials: CredentialsInfo
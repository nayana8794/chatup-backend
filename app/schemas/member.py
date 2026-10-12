# chatup/backend/app/schemas/member.py
import uuid
from typing import Optional
from pydantic import BaseModel
from app.models.user import UserRole, LanguagePreference

class MemberCreateRequest(BaseModel):
    full_name: str
    email: str  # Allows internal and demo domains without DNS MX failures
    phone_number: Optional[str] = None
    role: UserRole = UserRole.MEMBER
    language: LanguagePreference = LanguagePreference.FI

class MemberResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone_number: Optional[str] = None
    role: UserRole
    language: LanguagePreference
    is_active: bool
    must_change_password: Optional[bool] = False

    class Config:
        from_attributes = True

class MemberCreatedWithTempPass(MemberResponse):
    temp_password: str

class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str
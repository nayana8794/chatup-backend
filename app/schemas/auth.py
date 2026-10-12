# chatup/backend/app/schemas/auth.py
import uuid
from typing import Optional
from pydantic import BaseModel, EmailStr
from app.models.user import UserRole, LanguagePreference

class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: uuid.UUID
    tenant_id: Optional[uuid.UUID] = None
    role: UserRole
    full_name: str
    language: LanguagePreference
    must_change_password: bool = False  # Frontend එකට Modal එක පෙන්වීමට මෙය අත්‍යවශ්‍යයි

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
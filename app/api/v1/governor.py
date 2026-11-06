# chatup/backend/app/api/v1/governor.py
import uuid
import logging
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import get_password_hash, generate_temporary_password
from app.models.user import User, UserRole, LanguagePreference
from app.models.tenant import Tenant
from app.schemas.tenant import CredentialsInfo, AssignPresidentResponse

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/governor", tags=["District Governor Regional Command Center"])

class GovernorCreateRequest(BaseModel):
    full_name: str
    email: EmailStr
    phone_number: Optional[str] = None
    district: str

class GovernorUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    district: Optional[str] = None

class GovernorResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone_number: Optional[str] = None
    district: str
    is_active: bool
    created_at: datetime

class GovernorClubOnboardRequest(BaseModel):
    name: str
    slug: str
    president_name: str
    president_email: EmailStr
    president_phone: Optional[str] = None

@router.get("/overview")
async def get_regional_overview(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    district = current_user.district
    clubs = (await db.execute(select(Tenant).where(Tenant.district == district))).scalars().all()
    club_ids = [c.id for c in clubs]
    
    total_members = (await db.execute(select(func.count(User.id)).where(User.tenant_id.in_(club_ids)))).scalar_one()
    
    return {
        "district_name": district,
        "governor_name": current_user.full_name,
        "total_district_clubs": len(clubs),
        "active_district_clubs": sum(1 for c in clubs if c.is_active),
        "total_regional_members": total_members,
        "total_club_presidents": 0, # Logic to be added based on needs
        "circulars_issued_count": 0
    }

@router.post("/clubs", response_model=AssignPresidentResponse)
async def governor_onboard_club(payload: GovernorClubOnboardRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # 1. Create Club
    new_club = Tenant(name=payload.name, slug=payload.slug.lower(), district=current_user.district)
    db.add(new_club)
    await db.flush()
    
    # 2. Create President
    temp_pwd = generate_temporary_password()
    president = User(
        tenant_id=new_club.id,
        full_name=payload.president_name,
        email=payload.president_email.lower(),
        phone_number=payload.president_phone,
        hashed_password=get_password_hash(temp_pwd),
        role=UserRole.PRESIDENT,
        must_change_password=True
    )
    db.add(president)
    await db.commit()
    return AssignPresidentResponse(message="Club chartered", credentials=CredentialsInfo(club_name=new_club.name, president_name=president.full_name, email=president.email, temp_password=temp_pwd))
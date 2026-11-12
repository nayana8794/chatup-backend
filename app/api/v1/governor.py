# chatup/backend/app/api/v1/governor.py
import re
import uuid
import logging
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import get_password_hash, generate_temporary_password
from app.core.ws_manager import manager
from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.schemas.tenant import (
    CredentialsInfo,
    AssignPresidentResponse,
    DistrictClubRosterResponse,
    ClubPresidentInfo,
    ClubOfficeBearerInfo,
    ClubPresidentUpdate,
)

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/governor", tags=["District Governor Regional Command Center"])


def extract_district_code(district_str: Optional[str]) -> str:
    """Extracts standard district code such as '107-N' from strings like 'District 107-N (Helsinki)'."""
    if not district_str:
        return ""
    match = re.search(r"(\d{3}-[A-Za-z]+)", district_str)
    if match:
        return match.group(1).upper()
    clean = re.sub(r"\(.*?\)", "", district_str)
    return clean.replace("Lions", "").replace("District", "").strip().lower()


async def verify_district_governor(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in [UserRole.DISTRICT_GOVERNOR, UserRole.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="District Governor privileges are required to access this resource",
        )
    return current_user


async def notify_platform():
    try:
        await manager.broadcast_global({"type": "PLATFORM_UPDATE"})
    except Exception:
        pass


class GovernorClubOnboardRequest(BaseModel):
    name: str
    slug: str
    president_name: str
    president_email: EmailStr
    president_phone: Optional[str] = None


@router.get("/overview")
async def get_regional_overview(
    current_user: User = Depends(verify_district_governor),
    db: AsyncSession = Depends(get_db),
):
    gov_district = current_user.district or ""
    dist_code = extract_district_code(gov_district)

    query = select(Tenant)
    if dist_code:
        query = query.where(
            or_(
                Tenant.district == gov_district,
                Tenant.district.ilike(f"%{dist_code}%"),
            )
        )
    else:
        query = query.where(Tenant.district == gov_district)

    clubs = (await db.execute(query)).scalars().all()
    club_ids = [c.id for c in clubs]

    total_members = 0
    total_presidents = 0
    if club_ids:
        total_members = (
            await db.execute(select(func.count(User.id)).where(User.tenant_id.in_(club_ids)))
        ).scalar_one() or 0

        total_presidents = (
            await db.execute(
                select(func.count(User.id)).where(
                    and_(User.tenant_id.in_(club_ids), User.role == UserRole.PRESIDENT)
                )
            )
        ).scalar_one() or 0

    return {
        "district_name": gov_district,
        "governor_name": current_user.full_name,
        "total_district_clubs": len(clubs),
        "active_district_clubs": sum(1 for c in clubs if c.is_active),
        "total_regional_members": total_members,
        "total_club_presidents": total_presidents,
        "circulars_issued_count": 0,
    }


@router.get("/clubs", response_model=List[DistrictClubRosterResponse])
async def list_district_clubs(
    current_user: User = Depends(verify_district_governor),
    db: AsyncSession = Depends(get_db),
):
    gov_district = current_user.district or ""
    dist_code = extract_district_code(gov_district)

    query = select(Tenant).order_by(Tenant.name.asc())
    if dist_code:
        query = query.where(
            or_(
                Tenant.district == gov_district,
                Tenant.district.ilike(f"%{dist_code}%"),
            )
        )
    else:
        query = query.where(Tenant.district == gov_district)

    clubs = (await db.execute(query)).scalars().all()
    if not clubs:
        return []

    club_ids = [c.id for c in clubs]

    # Fetch member counts per club
    counts_res = await db.execute(
        select(User.tenant_id, func.count(User.id))
        .where(User.tenant_id.in_(club_ids))
        .group_by(User.tenant_id)
    )
    counts_map = dict(counts_res.all())

    # Fetch all users who have an officer role or president role
    officers_res = await db.execute(
        select(User).where(
            and_(
                User.tenant_id.in_(club_ids),
                User.role != UserRole.MEMBER,
                User.role != UserRole.SUPER_ADMIN,
                User.role != UserRole.DISTRICT_GOVERNOR,
            )
        )
    )
    all_officers = officers_res.scalars().all()

    presidents_map: dict = {}
    office_bearers_map: dict = {}

    for off in all_officers:
        t_id = off.tenant_id
        if off.role == UserRole.PRESIDENT:
            presidents_map[t_id] = off
        else:
            if t_id not in office_bearers_map:
                office_bearers_map[t_id] = []
            office_bearers_map[t_id].append(
                ClubOfficeBearerInfo(
                    id=off.id,
                    full_name=off.full_name,
                    email=off.email,
                    phone_number=off.phone_number,
                    role=off.role.value if hasattr(off.role, "value") else str(off.role),
                    is_active=off.is_active,
                )
            )

    roster = []
    for c in clubs:
        pres = presidents_map.get(c.id)
        pres_info = None
        if pres:
            pres_info = ClubPresidentInfo(
                id=pres.id,
                full_name=pres.full_name,
                email=pres.email,
                phone_number=pres.phone_number,
                is_active=pres.is_active,
            )

        roster.append(
            DistrictClubRosterResponse(
                id=c.id,
                name=c.name,
                slug=c.slug,
                district=c.district,
                is_active=c.is_active,
                member_count=counts_map.get(c.id, 0),
                president=pres_info,
                office_bearers=office_bearers_map.get(c.id, []),
                features=c.features or {},
            )
        )

    return roster


@router.post("/clubs", response_model=AssignPresidentResponse)
async def governor_onboard_club(
    payload: GovernorClubOnboardRequest,
    current_user: User = Depends(verify_district_governor),
    db: AsyncSession = Depends(get_db),
):
    existing_tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == payload.slug.lower().strip()))
    ).scalar_one_or_none()
    if existing_tenant:
        raise HTTPException(
            status_code=400,
            detail=f"Club workspace with identifier '{payload.slug}' already exists",
        )

    now = datetime.utcnow().replace(tzinfo=None)
    new_club = Tenant(
        name=payload.name.strip(),
        slug=payload.slug.lower().strip(),
        district=current_user.district,
        features={
            "video_calls": True,
            "mom_editor": True,
            "social_feed": True,
            "dues_module": True,
            "max_members": 50,
        },
        theme_config={
            "primary_color": "#002F6C",
            "secondary_color": "#F2A900",
            "dark_mode": False,
        },
        created_at=now,
        updated_at=now,
    )
    db.add(new_club)
    await db.flush()

    temp_pwd = generate_temporary_password()
    president = User(
        id=uuid.uuid4(),
        tenant_id=new_club.id,
        full_name=payload.president_name.strip(),
        email=payload.president_email.lower().strip(),
        phone_number=payload.president_phone.strip() if payload.president_phone else None,
        hashed_password=get_password_hash(temp_pwd),
        role=UserRole.PRESIDENT,
        district=current_user.district,
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    db.add(president)
    await db.commit()

    await notify_platform()

    return AssignPresidentResponse(
        message=f"Club chartered and inaugural president commissioned successfully in {current_user.district}",
        credentials=CredentialsInfo(
            club_name=new_club.name,
            president_name=president.full_name,
            email=president.email,
            temp_password=temp_pwd,
        ),
    )


@router.patch("/clubs/{club_id}/president", response_model=ClubPresidentInfo)
async def governor_update_president(
    club_id: uuid.UUID,
    payload: ClubPresidentUpdate,
    current_user: User = Depends(verify_district_governor),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    dist_code = extract_district_code(current_user.district)
    club_dist_code = extract_district_code(club.district)
    if dist_code != club_dist_code and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403, detail="Governors can only manage clubs in their assigned district"
        )

    president = (
        await db.execute(
            select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT)
        )
    ).scalar_one_or_none()

    if not president:
        raise HTTPException(status_code=404, detail="President account not found for this club")

    if payload.email and payload.email != president.email:
        existing = (
            await db.execute(select(User).where(User.email == payload.email, User.id != president.id))
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use by another user")
        president.email = payload.email

    if payload.full_name is not None:
        president.full_name = payload.full_name.strip()
    if payload.phone_number is not None:
        president.phone_number = payload.phone_number.strip() if payload.phone_number else None

    president.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(president)

    await notify_platform()

    return ClubPresidentInfo.model_validate(president)


@router.post("/clubs/{club_id}/reset-president-password", response_model=AssignPresidentResponse)
async def governor_reset_president_password(
    club_id: uuid.UUID,
    current_user: User = Depends(verify_district_governor),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    dist_code = extract_district_code(current_user.district)
    club_dist_code = extract_district_code(club.district)
    if dist_code != club_dist_code and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Forbidden")

    president = (
        await db.execute(
            select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT)
        )
    ).scalar_one_or_none()

    if not president:
        raise HTTPException(status_code=404, detail="President account not found")

    temp_pwd = generate_temporary_password()
    president.hashed_password = get_password_hash(temp_pwd)
    president.must_change_password = True
    president.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()

    return AssignPresidentResponse(
        message="President temporary credentials regenerated successfully",
        credentials=CredentialsInfo(
            club_name=club.name,
            president_name=president.full_name,
            email=president.email,
            temp_password=temp_pwd,
        ),
    )
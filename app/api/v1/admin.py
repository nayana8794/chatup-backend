# chatup/backend/app/api/v1/admin.py
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.tenant import (
    ClubCreateRequest,
    ClubUpdateRequest,
    ClubResponse,
    ClubFeatures,
)

router = APIRouter(prefix="/admin", tags=["Super Admin Console"])

async def verify_super_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="මෙම පිටුවට පිවිසීමට Super Admin බලතල අවශ්‍ය වේ",
        )
    return current_user

@router.get("/clubs", response_model=List[ClubResponse])
async def list_all_clubs(
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    # Club එක සහ එහි සිටින සාමාජිකයින් ගණන (member_count) එකවර ලබා ගැනීම
    query = (
        select(Tenant, func.count(User.id).label("member_count"))
        .outerjoin(User, Tenant.id == User.tenant_id)
        .group_by(Tenant.id)
        .order_by(Tenant.name)
    )
    result = await db.execute(query)
    clubs_data = []

    for tenant, member_count in result.all():
        clubs_data.append(
            ClubResponse(
                id=tenant.id,
                name=tenant.name,
                slug=tenant.slug,
                district=tenant.district,
                is_active=tenant.is_active,
                features=tenant.features or {},
                theme_config=tenant.theme_config or {},
                created_at=tenant.created_at,
                member_count=member_count,
            )
        )
    return clubs_data

@router.post("/clubs", response_model=ClubResponse)
async def create_new_club(
    club_in: ClubCreateRequest,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    # Slug එක දැනටමත් තිබේදැයි බැලීම
    existing = await db.execute(select(Tenant).where(Tenant.slug == club_in.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="මෙම Club Slug එක දැනටමත් පවතී")

    new_club = Tenant(
        name=club_in.name,
        slug=club_in.slug,
        district=club_in.district,
        features=club_in.features.model_dump() if club_in.features else {
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
    )

    db.add(new_club)
    await db.commit()
    await db.refresh(new_club)

    return ClubResponse(
        id=new_club.id,
        name=new_club.name,
        slug=new_club.slug,
        district=new_club.district,
        is_active=new_club.is_active,
        features=new_club.features or {},
        theme_config=new_club.theme_config or {},
        created_at=new_club.created_at,
        member_count=0,
    )

@router.patch("/clubs/{club_id}/features", response_model=ClubResponse)
async def update_club_features(
    club_id: uuid.UUID,
    features_in: ClubFeatures,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club එක සොයාගත නොහැක")

    # Granular Feature Flags update කිරීම
    club.features = features_in.model_dump()
    flag_modified(club, "features")

    await db.commit()
    await db.refresh(club)

    # Member count ලබා ගැනීම
    count = (await db.execute(select(func.count(User.id)).where(User.tenant_id == club_id))).scalar_one()

    return ClubResponse(
        id=club.id,
        name=club.name,
        slug=club.slug,
        district=club.district,
        is_active=club.is_active,
        features=club.features or {},
        theme_config=club.theme_config or {},
        created_at=club.created_at,
        member_count=count,
    )

@router.patch("/clubs/{club_id}", response_model=ClubResponse)
async def update_club_status(
    club_id: uuid.UUID,
    club_update: ClubUpdateRequest,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club එක සොයාගත නොහැක")

    if club_update.name is not None:
        club.name = club_update.name
    if club_update.district is not None:
        club.district = club_update.district
    if club_update.is_active is not None:
        club.is_active = club_update.is_active

    await db.commit()
    await db.refresh(club)

    count = (await db.execute(select(func.count(User.id)).where(User.tenant_id == club_id))).scalar_one()

    return ClubResponse(
        id=club.id,
        name=club.name,
        slug=club.slug,
        district=club.district,
        is_active=club.is_active,
        features=club.features or {},
        theme_config=club.theme_config or {},
        created_at=club.created_at,
        member_count=count,
    )
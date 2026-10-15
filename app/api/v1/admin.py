import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import get_password_hash, generate_temporary_password
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.tenant import (
    ClubCreateRequest,
    ClubUpdateRequest,
    ClubResponse,
    ClubFeatures,
    CredentialsInfo,
    ClubPresidentInfo,
    ClubPresidentUpdate,
    AssignPresidentRequest,
    AssignPresidentResponse,
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
    # Club සහ Member Count ලබා ගැනීම
    query = (
        select(Tenant, func.count(User.id).label("member_count"))
        .outerjoin(User, Tenant.id == User.tenant_id)
        .group_by(Tenant.id)
        .order_by(Tenant.name)
    )
    result = await db.execute(query)
    
    # සියලුම Presidents ලා ලබා ගැනීම
    presidents_res = await db.execute(select(User).where(User.role == UserRole.PRESIDENT))
    presidents = presidents_res.scalars().all()
    presidents_map = {p.tenant_id: p for p in presidents}

    clubs_data = []
    for tenant, member_count in result.all():
        pres = presidents_map.get(tenant.id)
        pres_info = None
        if pres:
            pres_info = ClubPresidentInfo(
                id=pres.id,
                full_name=pres.full_name,
                email=pres.email,
                phone_number=pres.phone_number,
                is_active=pres.is_active,
            )

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
                president=pres_info,
            )
        )
    return clubs_data

@router.post("/clubs", response_model=ClubResponse)
async def create_new_club(
    club_in: ClubCreateRequest,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    existing_tenant = (await db.execute(select(Tenant).where(Tenant.slug == club_in.slug))).scalar_one_or_none()
    if existing_tenant:
        raise HTTPException(status_code=400, detail="මෙම Club Slug එක දැනටමත් පවතී")

    if club_in.president_email:
        existing_user = (await db.execute(select(User).where(User.email == club_in.president_email))).scalar_one_or_none()
        if existing_user:
            raise HTTPException(status_code=400, detail="මෙම Email ලිපිනය සහිත පරිශීලකයෙක් දැනටමත් පවතී")

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
    await db.flush()

    credentials_data = None
    pres_info = None

    if club_in.president_email and club_in.president_name:
        temp_pwd = generate_temporary_password()
        president = User(
            id=uuid.uuid4(),
            tenant_id=new_club.id,
            full_name=club_in.president_name,
            email=club_in.president_email,
            phone_number=club_in.president_phone,
            hashed_password=get_password_hash(temp_pwd),
            role=UserRole.PRESIDENT,
            is_active=True,
            must_change_password=True,
        )
        db.add(president)
        credentials_data = CredentialsInfo(
            club_name=new_club.name,
            president_name=president.full_name,
            email=president.email,
            temp_password=temp_pwd,
        )
        pres_info = ClubPresidentInfo(
            id=president.id,
            full_name=president.full_name,
            email=president.email,
            phone_number=president.phone_number,
            is_active=president.is_active,
        )

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
        member_count=1 if credentials_data else 0,
        president=pres_info,
        credentials=credentials_data,
    )

@router.post("/clubs/{club_id}/initial-president", response_model=AssignPresidentResponse)
async def assign_initial_president(
    club_id: uuid.UUID,
    payload: AssignPresidentRequest,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club එක සොයාගත නොහැක")

    existing_user = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=400, detail="මෙම Email ලිපිනය දැනටමත් පවතී")

    temp_pwd = generate_temporary_password()
    president = User(
        id=uuid.uuid4(),
        tenant_id=club.id,
        full_name=payload.full_name,
        email=payload.email,
        phone_number=payload.phone_number,
        hashed_password=get_password_hash(temp_pwd),
        role=UserRole.PRESIDENT,
        is_active=True,
        must_change_password=True,
    )
    db.add(president)
    await db.commit()

    return AssignPresidentResponse(
        message="Club President provisioned successfully",
        credentials=CredentialsInfo(
            club_name=club.name,
            president_name=president.full_name,
            email=president.email,
            temp_password=temp_pwd,
        )
    )

# ================= RESET PRESIDENT PASSWORD =================
@router.post("/clubs/{club_id}/reset-president-password", response_model=AssignPresidentResponse)
async def reset_president_password(
    club_id: uuid.UUID,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club එක සොයාගත නොහැක")

    # එම Club එකේ President සොයා ගැනීම
    president = (
        await db.execute(
            select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT)
        )
    ).scalar_one_or_none()

    if not president:
        raise HTTPException(status_code=404, detail="මෙම Club එක සඳහා President ගිණුමක් සොයාගත නොහැක")

    temp_pwd = generate_temporary_password()
    president.hashed_password = get_password_hash(temp_pwd)
    president.must_change_password = True  # නැවත Login වූ විට Password මාරු කිරීම අනිවාර්ය කරයි

    await db.commit()

    return AssignPresidentResponse(
        message="President password reset successfully",
        credentials=CredentialsInfo(
            club_name=club.name,
            president_name=president.full_name,
            email=president.email,
            temp_password=temp_pwd,
        ),
    )

# ================= EDIT PRESIDENT DETAILS =================
@router.patch("/clubs/{club_id}/president", response_model=ClubPresidentInfo)
async def update_president_details(
    club_id: uuid.UUID,
    payload: ClubPresidentUpdate,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    president = (
        await db.execute(
            select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT)
        )
    ).scalar_one_or_none()

    if not president:
        raise HTTPException(status_code=404, detail="President ගිණුම සොයාගත නොහැක")

    if payload.email and payload.email != president.email:
        existing = (
            await db.execute(select(User).where(User.email == payload.email, User.id != president.id))
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="මෙම Email ලිපිනය දැනටමත් භාවිතයේ පවතී")
        president.email = payload.email

    if payload.full_name is not None:
        president.full_name = payload.full_name
    if payload.phone_number is not None:
        president.phone_number = payload.phone_number

    await db.commit()
    await db.refresh(president)

    return ClubPresidentInfo.model_validate(president)

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

    club.features = features_in.model_dump()
    flag_modified(club, "features")
    await db.commit()
    await db.refresh(club)

    count = (await db.execute(select(func.count(User.id)).where(User.tenant_id == club_id))).scalar_one()

    pres = (
        await db.execute(select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT))
    ).scalar_one_or_none()

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
        president=ClubPresidentInfo.model_validate(pres) if pres else None,
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

    pres = (
        await db.execute(select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT))
    ).scalar_one_or_none()

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
        president=ClubPresidentInfo.model_validate(pres) if pres else None,
    )
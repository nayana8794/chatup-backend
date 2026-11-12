# chatup/backend/app/api/v1/admin.py
import re
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import get_password_hash, generate_temporary_password
from app.core.ws_manager import manager
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.tenant import (
    ClubCreateRequest,
    ClubUpdateRequest,
    ClubResponse,
    ClubFeatures,
    CredentialsInfo,
    ClubPresidentInfo,
    ClubGovernorInfo,
    ClubPresidentUpdate,
    AssignPresidentRequest,
    AssignPresidentResponse,
)

router = APIRouter(prefix="/admin", tags=["Super Admin Console"])


def extract_district_code(district_str: Optional[str]) -> str:
    if not district_str:
        return ""
    match = re.search(r"(\d{3}-[A-Za-z]+)", district_str)
    if match:
        return match.group(1).upper()
    clean = re.sub(r"\(.*?\)", "", district_str)
    return clean.replace("Lions", "").replace("District", "").strip().lower()


async def verify_super_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin privileges are required to access this resource",
        )
    return current_user


async def verify_admin_or_governor(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.DISTRICT_GOVERNOR]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin or District Governor privileges are required",
        )
    return current_user


async def notify_platform():
    try:
        await manager.broadcast_global({"type": "PLATFORM_UPDATE"})
    except Exception:
        pass


class GovernorClubBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    member_count: int = 0
    president_name: Optional[str] = None
    president_email: Optional[str] = None


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
    total_clubs: int = 0
    total_members: int = 0
    clubs: List[GovernorClubBrief] = []


@router.get("/stats")
async def get_platform_telemetry(
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    total_clubs = (await db.execute(select(func.count(Tenant.id)))).scalar_one() or 0
    active_clubs = (
        await db.execute(select(func.count(Tenant.id)).where(Tenant.is_active == True))
    ).scalar_one() or 0

    total_members = (
        await db.execute(
            select(func.count(User.id)).where(
                and_(
                    User.role != UserRole.SUPER_ADMIN,
                    User.role != UserRole.DISTRICT_GOVERNOR,
                )
            )
        )
    ).scalar_one() or 0

    total_presidents = (
        await db.execute(select(func.count(User.id)).where(User.role == UserRole.PRESIDENT))
    ).scalar_one() or 0

    total_governors = (
        await db.execute(select(func.count(User.id)).where(User.role == UserRole.DISTRICT_GOVERNOR))
    ).scalar_one() or 0

    return {
        "total_clubs": total_clubs,
        "active_clubs": active_clubs,
        "suspended_clubs": total_clubs - active_clubs,
        "total_members": total_members,
        "total_presidents": total_presidents,
        "total_governors": total_governors,
        "subscription_health": "100%",
        "system_status": "Healthy",
    }


# ================= DISTRICT GOVERNORS MANAGEMENT =================
@router.get("/governors", response_model=List[GovernorResponse])
async def list_district_governors(
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(User)
        .where(User.role == UserRole.DISTRICT_GOVERNOR)
        .order_by(User.full_name.asc())
    )
    result = await db.execute(query)
    governors = result.scalars().all()

    # Pre-fetch all tenants and presidents for hierarchy mapping
    all_clubs = (await db.execute(select(Tenant))).scalars().all()
    all_presidents = (
        await db.execute(select(User).where(User.role == UserRole.PRESIDENT))
    ).scalars().all()
    presidents_map = {p.tenant_id: p for p in all_presidents}

    # Member counts map
    counts_res = await db.execute(
        select(User.tenant_id, func.count(User.id)).group_by(User.tenant_id)
    )
    counts_map = dict(counts_res.all())

    response_list = []
    for g in governors:
        g_code = extract_district_code(g.district)
        matched_clubs = []
        g_total_members = 0

        for c in all_clubs:
            c_code = extract_district_code(c.district)
            if (g_code and c_code and g_code == c_code) or (c.district == g.district):
                m_count = counts_map.get(c.id, 0)
                g_total_members += m_count
                pres = presidents_map.get(c.id)
                matched_clubs.append(
                    GovernorClubBrief(
                        id=c.id,
                        name=c.name,
                        slug=c.slug,
                        member_count=m_count,
                        president_name=pres.full_name if pres else None,
                        president_email=pres.email if pres else None,
                    )
                )

        response_list.append(
            GovernorResponse(
                id=g.id,
                full_name=g.full_name,
                email=g.email,
                phone_number=g.phone_number,
                district=g.district or "Unassigned District",
                is_active=g.is_active,
                created_at=g.created_at,
                total_clubs=len(matched_clubs),
                total_members=g_total_members,
                clubs=matched_clubs,
            )
        )
    return response_list


@router.post("/governors", response_model=AssignPresidentResponse)
async def create_district_governor(
    payload: GovernorCreateRequest,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(select(User).where(User.email == payload.email.lower().strip()))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="A user with this email address already exists")

    temp_pwd = generate_temporary_password()
    now = datetime.utcnow().replace(tzinfo=None)
    governor = User(
        id=uuid.uuid4(),
        tenant_id=None,
        full_name=payload.full_name.strip(),
        email=payload.email.lower().strip(),
        phone_number=payload.phone_number.strip() if payload.phone_number else None,
        hashed_password=get_password_hash(temp_pwd),
        role=UserRole.DISTRICT_GOVERNOR,
        district=payload.district.strip(),
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    db.add(governor)
    await db.commit()
    await notify_platform()

    return AssignPresidentResponse(
        message=f"District Governor appointed successfully for {payload.district}",
        credentials=CredentialsInfo(
            club_name=payload.district,
            president_name=governor.full_name,
            email=governor.email,
            temp_password=temp_pwd,
        ),
    )


@router.patch("/governors/{governor_id}", response_model=GovernorResponse)
async def update_district_governor(
    governor_id: uuid.UUID,
    payload: GovernorUpdateRequest,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    gov = (
        await db.execute(
            select(User).where(User.id == governor_id, User.role == UserRole.DISTRICT_GOVERNOR)
        )
    ).scalar_one_or_none()
    if not gov:
        raise HTTPException(status_code=404, detail="Governor not found")

    if payload.email and payload.email.lower().strip() != gov.email.lower():
        existing = (
            await db.execute(
                select(User).where(User.email == payload.email.lower().strip(), User.id != governor_id)
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use")
        gov.email = payload.email.lower().strip()

    if payload.full_name is not None:
        gov.full_name = payload.full_name.strip()
    if payload.phone_number is not None:
        gov.phone_number = payload.phone_number.strip() if payload.phone_number else None
    if payload.district is not None:
        gov.district = payload.district.strip()

    gov.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()
    await notify_platform()

    return GovernorResponse(
        id=gov.id,
        full_name=gov.full_name,
        email=gov.email,
        phone_number=gov.phone_number,
        district=gov.district or "Unassigned District",
        is_active=gov.is_active,
        created_at=gov.created_at,
        total_clubs=0,
        total_members=0,
        clubs=[],
    )


@router.post("/governors/{governor_id}/reset-password", response_model=AssignPresidentResponse)
async def reset_governor_password(
    governor_id: uuid.UUID,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    governor = (
        await db.execute(
            select(User).where(User.id == governor_id, User.role == UserRole.DISTRICT_GOVERNOR)
        )
    ).scalar_one_or_none()

    if not governor:
        raise HTTPException(status_code=404, detail="District Governor account not found")

    temp_pwd = generate_temporary_password()
    governor.hashed_password = get_password_hash(temp_pwd)
    governor.must_change_password = True
    governor.updated_at = datetime.utcnow().replace(tzinfo=None)

    await db.commit()

    return AssignPresidentResponse(
        message="Governor temporary credentials regenerated successfully",
        credentials=CredentialsInfo(
            club_name=governor.district or "District Jurisdiction",
            president_name=governor.full_name,
            email=governor.email,
            temp_password=temp_pwd,
        ),
    )


# ================= CLUB WORKSPACES DIRECTORY & MANAGEMENT =================
@router.get("/clubs", response_model=List[ClubResponse])
async def list_all_clubs(
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Tenant, func.count(User.id).label("member_count"))
        .outerjoin(User, Tenant.id == User.tenant_id)
        .group_by(Tenant.id)
        .order_by(Tenant.name.asc())
    )

    if search:
        s = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Tenant.name).like(s),
                func.lower(Tenant.slug).like(s),
                func.lower(Tenant.district).like(s),
            )
        )

    if status_filter == "active":
        query = query.where(Tenant.is_active == True)
    elif status_filter == "suspended":
        query = query.where(Tenant.is_active == False)

    result = await db.execute(query)

    # Fetch presidents
    presidents_res = await db.execute(select(User).where(User.role == UserRole.PRESIDENT))
    presidents = presidents_res.scalars().all()
    presidents_map = {p.tenant_id: p for p in presidents}

    # Fetch governors to associate with each club's district
    governors_res = await db.execute(
        select(User).where(User.role == UserRole.DISTRICT_GOVERNOR)
    )
    governors = governors_res.scalars().all()

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

        # Match governor by district code
        tenant_dist_code = extract_district_code(tenant.district)
        gov_info = None
        for g in governors:
            g_dist_code = extract_district_code(g.district)
            if (tenant_dist_code and g_dist_code and tenant_dist_code == g_dist_code) or (
                tenant.district and g.district and tenant.district == g.district
            ):
                gov_info = ClubGovernorInfo(
                    id=g.id,
                    full_name=g.full_name,
                    email=g.email,
                    phone_number=g.phone_number,
                    district=g.district,
                    is_active=g.is_active,
                )
                break

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
                governor=gov_info,
            )
        )
    return clubs_data


@router.post("/clubs", response_model=ClubResponse)
async def create_new_club(
    club_in: ClubCreateRequest,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    existing_tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == club_in.slug))
    ).scalar_one_or_none()
    if existing_tenant:
        raise HTTPException(
            status_code=400,
            detail=f"Club workspace with identifier '{club_in.slug}' already exists",
        )

    now = datetime.utcnow().replace(tzinfo=None)
    new_club = Tenant(
        name=club_in.name,
        slug=club_in.slug,
        district=club_in.district,
        features=club_in.features.model_dump()
        if club_in.features
        else {
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
    await db.commit()
    await db.refresh(new_club)

    await notify_platform()

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
        president=None,
        governor=None,
        credentials=None,
    )


@router.post("/clubs/{club_id}/initial-president", response_model=AssignPresidentResponse)
async def assign_initial_president(
    club_id: uuid.UUID,
    payload: AssignPresidentRequest,
    current_user: User = Depends(verify_admin_or_governor),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    if current_user.role == UserRole.DISTRICT_GOVERNOR:
        dist_code = extract_district_code(current_user.district)
        club_dist_code = extract_district_code(club.district)
        if dist_code != club_dist_code:
            raise HTTPException(
                status_code=403,
                detail="Governors can only assign presidents within their own district",
            )

    existing_user = (
        await db.execute(select(User).where(User.email == payload.email.lower().strip()))
    ).scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail=f"A user with email '{payload.email}' is already registered",
        )

    now = datetime.utcnow().replace(tzinfo=None)
    temp_pwd = generate_temporary_password()
    president = User(
        id=uuid.uuid4(),
        tenant_id=club.id,
        full_name=payload.full_name.strip(),
        email=payload.email.lower().strip(),
        phone_number=payload.phone_number.strip() if payload.phone_number else None,
        hashed_password=get_password_hash(temp_pwd),
        role=UserRole.PRESIDENT,
        district=club.district,
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    db.add(president)
    await db.commit()
    await notify_platform()

    return AssignPresidentResponse(
        message="Club President provisioned successfully",
        credentials=CredentialsInfo(
            club_name=club.name,
            president_name=president.full_name,
            email=president.email,
            temp_password=temp_pwd,
        ),
    )


@router.post("/clubs/{club_id}/reset-president-password", response_model=AssignPresidentResponse)
async def reset_president_password(
    club_id: uuid.UUID,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    president = (
        await db.execute(
            select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT)
        )
    ).scalar_one_or_none()

    if not president:
        raise HTTPException(
            status_code=404,
            detail="No President account found associated with this club workspace",
        )

    temp_pwd = generate_temporary_password()
    president.hashed_password = get_password_hash(temp_pwd)
    president.must_change_password = True
    president.updated_at = datetime.utcnow().replace(tzinfo=None)

    await db.commit()

    return AssignPresidentResponse(
        message="President temporary credentials generated successfully",
        credentials=CredentialsInfo(
            club_name=club.name,
            president_name=president.full_name,
            email=president.email,
            temp_password=temp_pwd,
        ),
    )


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
        raise HTTPException(status_code=404, detail="President account not found")

    if payload.email and payload.email.lower().strip() != president.email.lower():
        existing = (
            await db.execute(
                select(User).where(
                    User.email == payload.email.lower().strip(), User.id != president.id
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Email address '{payload.email}' is already in use by another user",
            )
        president.email = payload.email.lower().strip()

    if payload.full_name is not None:
        president.full_name = payload.full_name.strip()
    if payload.phone_number is not None:
        president.phone_number = payload.phone_number.strip() if payload.phone_number else None

    president.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(president)
    await notify_platform()

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
        raise HTTPException(status_code=404, detail="Club not found")

    club.features = features_in.model_dump()
    flag_modified(club, "features")
    club.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(club)
    await notify_platform()

    count = (
        await db.execute(select(func.count(User.id)).where(User.tenant_id == club_id))
    ).scalar_one()

    pres = (
        await db.execute(
            select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT)
        )
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
        governor=None,
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
        raise HTTPException(status_code=404, detail="Club not found")

    if club_update.name is not None:
        club.name = club_update.name
    if club_update.district is not None:
        club.district = club_update.district
    if club_update.is_active is not None:
        club.is_active = club_update.is_active

    club.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(club)
    await notify_platform()

    count = (
        await db.execute(select(func.count(User.id)).where(User.tenant_id == club_id))
    ).scalar_one()

    pres = (
        await db.execute(
            select(User).where(User.tenant_id == club_id, User.role == UserRole.PRESIDENT)
        )
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
        governor=None,
    )


@router.delete("/clubs/{club_id}")
async def delete_club_workspace(
    club_id: uuid.UUID,
    _: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    club = (await db.execute(select(Tenant).where(Tenant.id == club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    await db.delete(club)
    await db.commit()
    await notify_platform()

    return {"status": "success", "message": f"Club workspace '{club.name}' successfully deleted"}
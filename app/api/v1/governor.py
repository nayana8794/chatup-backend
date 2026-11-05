# chatup/backend/app/api/v1/governor.py
import os
import uuid
import logging
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, EmailStr
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
import cloudinary
import cloudinary.uploader

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.core.security import get_password_hash, generate_temporary_password
from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.models.document import ClubDocument, DocumentCategory
from app.models.event import Event, EventType
from app.schemas.tenant import CredentialsInfo, AssignPresidentResponse

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/governor", tags=["District Governor Regional Command Center"])


async def verify_governor_or_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.DISTRICT_GOVERNOR]:
        raise HTTPException(
            status_code=403,
            detail="District Governor or Super Admin privileges are required to access this resource",
        )
    return current_user


class RegionalOverviewResponse(BaseModel):
    district_name: str
    governor_name: str
    total_district_clubs: int
    active_district_clubs: int
    total_regional_members: int
    total_club_presidents: int
    circulars_issued_count: int


class DistrictClubItem(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    district: str
    is_active: bool
    member_count: int
    president_name: Optional[str] = None
    president_email: Optional[str] = None
    president_phone: Optional[str] = None


class DistrictEventCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    location: str
    meeting_link: Optional[str] = None


class GovernorClubOnboardRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=255)
    slug: str = Field(..., min_length=3, max_length=100)
    president_name: str = Field(..., min_length=2, max_length=255)
    president_email: EmailStr
    president_phone: Optional[str] = None


@router.get("/overview", response_model=RegionalOverviewResponse)
async def get_regional_overview(
    current_user: User = Depends(verify_governor_or_admin),
    db: AsyncSession = Depends(get_db),
):
    district_filter = current_user.district
    if not district_filter and current_user.role == UserRole.SUPER_ADMIN:
        first_tenant = (await db.execute(select(Tenant).where(Tenant.district.isnot(None)).limit(1))).scalar_one_or_none()
        district_filter = first_tenant.district if first_tenant else "District 107-N"

    clubs_res = await db.execute(
        select(Tenant).where(Tenant.district == district_filter)
    )
    clubs = clubs_res.scalars().all()
    total_clubs = len(clubs)
    active_clubs = sum(1 for c in clubs if c.is_active)
    club_ids = [c.id for c in clubs]

    total_members = 0
    total_presidents = 0
    if club_ids:
        total_members = (
            await db.execute(
                select(func.count(User.id)).where(User.tenant_id.in_(club_ids), User.is_active == True)
            )
        ).scalar_one() or 0

        total_presidents = (
            await db.execute(
                select(func.count(User.id)).where(
                    User.tenant_id.in_(club_ids),
                    User.role == UserRole.PRESIDENT,
                    User.is_active == True,
                )
            )
        ).scalar_one() or 0

    circulars_count = (
        await db.execute(
            select(func.count(ClubDocument.id)).where(
                ClubDocument.uploader_id == current_user.id,
                ClubDocument.category == DocumentCategory.DISTRICT,
            )
        )
    ).scalar_one() or 0

    return RegionalOverviewResponse(
        district_name=district_filter or "Nordic District",
        governor_name=current_user.full_name,
        total_district_clubs=total_clubs,
        active_district_clubs=active_clubs,
        total_regional_members=total_members,
        total_club_presidents=total_presidents,
        circulars_issued_count=circulars_count,
    )


@router.get("/clubs", response_model=List[DistrictClubItem])
async def list_district_clubs(
    current_user: User = Depends(verify_governor_or_admin),
    db: AsyncSession = Depends(get_db),
):
    district_filter = current_user.district
    if not district_filter and current_user.role == UserRole.SUPER_ADMIN:
        first_tenant = (await db.execute(select(Tenant).where(Tenant.district.isnot(None)).limit(1))).scalar_one_or_none()
        district_filter = first_tenant.district if first_tenant else "District 107-N"

    clubs_res = await db.execute(
        select(Tenant).where(Tenant.district == district_filter).order_by(Tenant.name.asc())
    )
    clubs = clubs_res.scalars().all()

    items = []
    for c in clubs:
        member_count = (
            await db.execute(select(func.count(User.id)).where(User.tenant_id == c.id, User.is_active == True))
        ).scalar_one() or 0

        pres = (
            await db.execute(
                select(User).where(User.tenant_id == c.id, User.role == UserRole.PRESIDENT, User.is_active == True)
            )
        ).scalar_one_or_none()

        items.append(
            DistrictClubItem(
                id=c.id,
                name=c.name,
                slug=c.slug,
                district=c.district or district_filter,
                is_active=c.is_active,
                member_count=member_count,
                president_name=pres.full_name if pres else None,
                president_email=pres.email if pres else None,
                president_phone=pres.phone_number if pres else None,
            )
        )

    return items


# District Governor onboards a new Club under their specific District
@router.post("/clubs", response_model=AssignPresidentResponse)
async def governor_onboard_club(
    payload: GovernorClubOnboardRequest,
    current_user: User = Depends(verify_governor_or_admin),
    db: AsyncSession = Depends(get_db),
):
    district_name = current_user.district
    if not district_name and current_user.role == UserRole.SUPER_ADMIN:
        district_name = "District 107-N"

    # 1. Validate slug uniqueness
    clean_slug = payload.slug.lower().strip()
    existing_tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == clean_slug))
    ).scalar_one_or_none()
    if existing_tenant:
        raise HTTPException(status_code=400, detail=f"Club workspace slug '{clean_slug}' already exists")

    # 2. Validate email uniqueness
    clean_email = payload.president_email.lower().strip()
    existing_user = (
        await db.execute(select(User).where(User.email == clean_email))
    ).scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=400, detail=f"Email address '{clean_email}' is already registered")

    now = datetime.utcnow().replace(tzinfo=None)

    # 3. Create club workspace chartered under this governor's district
    new_club = Tenant(
        id=uuid.uuid4(),
        name=payload.name.strip(),
        slug=clean_slug,
        district=district_name,
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

    # 4. Appoint Inaugural Club President
    temp_pwd = generate_temporary_password()
    president = User(
        id=uuid.uuid4(),
        tenant_id=new_club.id,
        full_name=payload.president_name.strip(),
        email=clean_email,
        phone_number=payload.president_phone.strip() if payload.president_phone else None,
        hashed_password=get_password_hash(temp_pwd),
        role=UserRole.PRESIDENT,
        district=district_name,
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    db.add(president)
    await db.commit()

    return AssignPresidentResponse(
        message=f"Club '{new_club.name}' successfully chartered under {district_name}",
        credentials=CredentialsInfo(
            club_name=new_club.name,
            president_name=president.full_name,
            email=president.email,
            temp_password=temp_pwd,
        ),
    )


# Broadcast District Circular (Piirikirje) to ALL clubs in District
@router.post("/circulars")
async def broadcast_district_circular(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(verify_governor_or_admin),
    db: AsyncSession = Depends(get_db),
):
    district_filter = current_user.district
    if not district_filter and current_user.role == UserRole.SUPER_ADMIN:
        first_tenant = (await db.execute(select(Tenant).where(Tenant.district.isnot(None)).limit(1))).scalar_one_or_none()
        district_filter = first_tenant.district if first_tenant else "District 107-N"

    clubs_res = await db.execute(select(Tenant).where(Tenant.district == district_filter))
    district_clubs = clubs_res.scalars().all()

    if not district_clubs:
        raise HTTPException(status_code=400, detail="No clubs found in this district to distribute circular to")

    original_filename = file.filename or "district_circular.pdf"
    file_ext = os.path.splitext(original_filename)[1].lower().replace(".", "") or "pdf"
    
    file_bytes = await file.read()
    file_size = len(file_bytes)
    await file.seek(0)

    try:
        upload_result = cloudinary.uploader.upload(
            file.file,
            folder=f"chatup_district_circulars/{district_filter.replace(' ', '_').lower()}",
            resource_type="auto",
            access_mode="public",
            use_filename=True,
            unique_filename=True,
        )
        secure_url = upload_result.get("secure_url")
        if not secure_url:
            raise Exception("Cloudinary secure URL not received")
    except Exception as e:
        logger.error("District circular upload failed: %s", str(e))
        raise HTTPException(status_code=500, detail="Failed to upload circular to cloud CDN")

    now = datetime.utcnow().replace(tzinfo=None)

    # Distribute into Document Repository of every club in district
    for c in district_clubs:
        doc = ClubDocument(
            tenant_id=c.id,
            uploader_id=current_user.id,
            title=f"🏛️ [Piirikirje] {title.strip()}",
            description=description.strip() if description else f"Official Directive from District Governor {current_user.full_name}",
            category=DocumentCategory.DISTRICT,
            file_url=secure_url,
            file_name=original_filename,
            file_size_bytes=file_size,
            file_type=file_ext,
            is_board_only=False,
            created_at=now,
            updated_at=now,
        )
        db.add(doc)

    await db.commit()

    for c in district_clubs:
        try:
            await manager.broadcast_to_tenant(
                str(c.id).lower(),
                {
                    "type": "DOCUMENT_UPLOADED",
                    "title": f"🏛️ [Piirikirje] {title.strip()}",
                    "category": "DISTRICT",
                    "uploader_name": f"District Governor {current_user.full_name}",
                },
            )
        except Exception:
            pass

    return {
        "status": "success",
        "distributed_clubs_count": len(district_clubs),
        "message": f"District Circular successfully published to all {len(district_clubs)} clubs in {district_filter}",
    }


# Schedule District Convention across all clubs in district
@router.post("/events")
async def schedule_district_convention(
    req: DistrictEventCreateRequest,
    current_user: User = Depends(verify_governor_or_admin),
    db: AsyncSession = Depends(get_db),
):
    district_filter = current_user.district
    if not district_filter and current_user.role == UserRole.SUPER_ADMIN:
        first_tenant = (await db.execute(select(Tenant).where(Tenant.district.isnot(None)).limit(1))).scalar_one_or_none()
        district_filter = first_tenant.district if first_tenant else "District 107-N"

    clubs_res = await db.execute(select(Tenant).where(Tenant.district == district_filter))
    district_clubs = clubs_res.scalars().all()

    if not district_clubs:
        raise HTTPException(status_code=400, detail="No clubs found in this district to schedule event for")

    start_clean = req.start_time.replace(tzinfo=None) if req.start_time.tzinfo else req.start_time
    end_clean = req.end_time.replace(tzinfo=None) if req.end_time and req.end_time.tzinfo else req.end_time

    for c in district_clubs:
        ev = Event(
            tenant_id=c.id,
            planner_id=current_user.id,
            title=f"🏛️ [District Convention] {req.title.strip()}",
            description=req.description.strip() if req.description else f"Official District Convention hosted by District Governor {current_user.full_name}",
            event_type=EventType.REGULAR_MEETING,
            start_time=start_clean,
            end_time=end_clean,
            location=req.location.strip(),
            is_online=bool(req.meeting_link),
            meeting_link=req.meeting_link,
            allows_guests=True,
        )
        db.add(ev)

    await db.commit()

    for c in district_clubs:
        try:
            await manager.broadcast_to_tenant(
                str(c.id).lower(),
                {
                    "type": "EVENT_CREATED",
                    "title": f"🏛️ [District Convention] {req.title.strip()}",
                    "planner_name": f"District Governor {current_user.full_name}",
                },
            )
        except Exception:
            pass

    return {
        "status": "success",
        "synced_clubs_count": len(district_clubs),
        "message": f"District event successfully synchronized with {len(district_clubs)} club calendars",
    }
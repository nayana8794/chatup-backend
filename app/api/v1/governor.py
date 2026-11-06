import os
import uuid
import shutil
from typing import List, Optional, Any
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    UploadFile,
    File,
    Form,
    Query
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import ws_manager
from app.core.config import settings

from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.models.document import ClubDocument, DocumentCategory
from app.models.event import Event, EventRSVP

# Cloudinary optional integration with fallback
try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = bool(
        settings.CLOUDINARY_CLOUD_NAME and 
        settings.CLOUDINARY_API_KEY and 
        settings.CLOUDINARY_API_SECRET
    )
    if CLOUDINARY_AVAILABLE:
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True
        )
except Exception:
    CLOUDINARY_AVAILABLE = False

router = APIRouter(prefix="/governor", tags=["District Governor"])


# =====================================================================
# SECURITY DEPENDENCY
# =====================================================================

async def verify_governor_or_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Ensures the user holds either DISTRICT_GOVERNOR or SUPER_ADMIN privileges.
    """
    if current_user.role not in [UserRole.DISTRICT_GOVERNOR, UserRole.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted: District Governor or Super Admin privileges required."
        )
    
    if current_user.role == UserRole.DISTRICT_GOVERNOR and not current_user.district:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="District Governor profile is not bound to any administrative district."
        )
        
    return current_user


# =====================================================================
# 1. DISTRICT TELEMETRY & STATS
# =====================================================================

@router.get("/stats", response_model=dict)
async def get_district_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_governor_or_admin)
) -> Any:
    """
    Calculates live analytical counts for the District Governor's dashboard.
    """
    target_district = current_user.district

    # Base query filter for tenants in the governor's district
    tenant_filter = Tenant.district == target_district if current_user.role == UserRole.DISTRICT_GOVERNOR else True

    # 1. Total Clubs
    clubs_query = select(func.count(Tenant.id)).where(tenant_filter)
    total_clubs = (await db.execute(clubs_query)).scalar() or 0

    # 2. Total Members across all district clubs
    members_query = (
        select(func.count(User.id))
        .join(Tenant, User.tenant_id == Tenant.id)
        .where(tenant_filter)
        .where(User.is_active == True)
    )
    total_members = (await db.execute(members_query)).scalar() or 0

    # 3. Total Presidents Appointed
    presidents_query = (
        select(func.count(User.id))
        .join(Tenant, User.tenant_id == Tenant.id)
        .where(tenant_filter)
        .where(User.role == UserRole.PRESIDENT)
        .where(User.is_active == True)
    )
    total_presidents = (await db.execute(presidents_query)).scalar() or 0

    # 4. Total Circulars Distributed (Unique titles issued under DISTRICT category)
    circulars_query = (
        select(func.count(func.distinct(ClubDocument.title)))
        .join(Tenant, ClubDocument.tenant_id == Tenant.id)
        .where(tenant_filter)
        .where(ClubDocument.category == DocumentCategory.DISTRICT)
    )
    circulars_count = (await db.execute(circulars_query)).scalar() or 0

    # 5. Upcoming District Conventions/Events
    now = datetime.utcnow()
    events_query = (
        select(func.count(func.distinct(Event.title)))
        .join(Tenant, Event.tenant_id == Tenant.id)
        .where(tenant_filter)
        .where(Event.start_time >= now)
    )
    upcoming_events = (await db.execute(events_query)).scalar() or 0

    return {
        "district": target_district or "ALL",
        "total_clubs": total_clubs,
        "total_members": total_members,
        "total_presidents": total_presidents,
        "circulars_issued_count": circulars_count,
        "upcoming_events_count": upcoming_events
    }


# =====================================================================
# 2. DISTRICT CLUBS DIRECTORY & EXECUTIVE ROSTER
# =====================================================================

@router.get("/clubs", response_model=List[dict])
async def get_district_clubs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_governor_or_admin)
) -> Any:
    """
    Returns all clubs in the Governor's district along with:
    - Member counts
    - Appointed Club President details (name, email, phone)
    - Charter date & active status
    """
    target_district = current_user.district
    query = select(Tenant)
    if current_user.role == UserRole.DISTRICT_GOVERNOR:
        query = query.where(Tenant.district == target_district)
    
    query = query.order_by(Tenant.name.asc())
    result = await db.execute(query)
    clubs = result.scalars().all()

    response = []
    for club in clubs:
        # Calculate active members for this club
        members_count_query = select(func.count(User.id)).where(
            and_(User.tenant_id == club.id, User.is_active == True)
        )
        member_count = (await db.execute(members_count_query)).scalar() or 0

        # Fetch appointed President details
        president_query = select(User).where(
            and_(
                User.tenant_id == club.id,
                User.role == UserRole.PRESIDENT,
                User.is_active == True
            )
        ).limit(1)
        president = (await db.execute(president_query)).scalar_one_or_none()

        response.append({
            "id": club.id,
            "name": club.name,
            "district": club.district,
            "is_active": club.is_active,
            "created_at": club.created_at.isoformat() if club.created_at else None,
            "member_count": member_count,
            "president": {
                "id": president.id,
                "full_name": president.full_name,
                "email": president.email,
                "phone_number": president.phone_number
            } if president else None
        })

    return response


# =====================================================================
# 3. REGIONAL CIRCULARS MULTIPART BROADCAST (Piirikirjeet)
# =====================================================================

@router.post("/circulars", status_code=status.HTTP_201_CREATED)
async def broadcast_district_circular(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_governor_or_admin)
) -> Any:
    """
    Uploads a regional circular document and broadcasts it across
    EVERY club document vault in the Governor's district.
    """
    target_district = current_user.district

    # 1. Fetch all eligible target clubs in this district
    tenant_query = select(Tenant.id).where(Tenant.is_active == True)
    if current_user.role == UserRole.DISTRICT_GOVERNOR:
        tenant_query = tenant_query.where(Tenant.district == target_district)
    
    tenant_ids = (await db.execute(tenant_query)).scalars().all()

    if not tenant_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active clubs found in District '{target_district}'."
        )

    # 2. File Persistence (Cloudinary with Local Storage fallback)
    file_url = ""
    public_id = f"circular_{uuid.uuid4().hex[:10]}_{file.filename}"

    if CLOUDINARY_AVAILABLE:
        try:
            upload_result = cloudinary.uploader.upload(
                file.file,
                resource_type="auto",
                folder=f"chatup/districts/{target_district}/circulars",
                public_id=public_id
            )
            file_url = upload_result.get("secure_url")
        except Exception as e:
            # Fallback to local file storage on external upload error
            pass

    if not file_url:
        # Local file storage fallback
        upload_dir = os.path.join("static", "uploads", "circulars")
        os.makedirs(upload_dir, exist_ok=True)
        local_filename = f"{uuid.uuid4().hex}_{file.filename}"
        file_path = os.path.join(upload_dir, local_filename)

        file.file.seek(0)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        file_url = f"/static/uploads/circulars/{local_filename}"

    # 3. Insert ClubDocument record into each Club Tenant in the district
    created_documents = []
    for tenant_id in tenant_ids:
        doc = ClubDocument(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            uploader_id=current_user.id,
            title=title,
            description=description or f"Official District Circular issued by Governor {current_user.full_name}",
            file_url=file_url,
            category=DocumentCategory.DISTRICT,
            created_at=datetime.utcnow()
        )
        db.add(doc)
        created_documents.append((tenant_id, doc))

    await db.commit()

    # 4. Dispatch Real-Time WebSocket notification to all clubs
    ws_payload = {
        "type": "DOCUMENT_UPLOADED",
        "category": "DISTRICT",
        "title": title,
        "file_url": file_url,
        "issued_by": current_user.full_name,
        "timestamp": datetime.utcnow().isoformat()
    }

    for tenant_id, doc in created_documents:
        try:
            await ws_manager.broadcast_to_tenant(tenant_id, ws_payload)
        except Exception:
            # WebSocket dispatch failure shouldn't abort the transaction
            pass

    return {
        "status": "success",
        "message": f"Circular successfully broadcasted to {len(tenant_ids)} clubs in district {target_district}.",
        "file_url": file_url,
        "clubs_affected": len(tenant_ids)
    }


# =====================================================================
# 4. REGIONAL CONVENTIONS & EVENTS SYNCHRONIZATION
# =====================================================================

@router.post("/events", status_code=status.HTTP_201_CREATED)
async def create_district_convention(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    start_time: datetime = Form(...),
    end_time: datetime = Form(...),
    location: Optional[str] = Form(None),
    meeting_link: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_governor_or_admin)
) -> Any:
    """
    Creates a regional convention event and automatically synchronizes
    it across all club calendars within the Governor's district.
    """
    target_district = current_user.district

    tenant_query = select(Tenant.id).where(Tenant.is_active == True)
    if current_user.role == UserRole.DISTRICT_GOVERNOR:
        tenant_query = tenant_query.where(Tenant.district == target_district)
        
    tenant_ids = (await db.execute(tenant_query)).scalars().all()

    if not tenant_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active clubs found to schedule this event."
        )

    # Replicate event for each club tenant
    created_events = []
    for tenant_id in tenant_ids:
        convention_event = Event(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            creator_id=current_user.id,
            title=f"[District {target_district}] {title}",
            description=description,
            start_time=start_time,
            end_time=end_time,
            location=location or "District Convention Hall / Online",
            meeting_link=meeting_link,
            is_district_event=True,
            created_at=datetime.utcnow()
        )
        db.add(convention_event)
        created_events.append((tenant_id, convention_event))

    await db.commit()

    # Dispatch WebSocket notification to notify all clubs in real-time
    ws_event_payload = {
        "type": "EVENT_CREATED",
        "title": title,
        "is_district_event": True,
        "start_time": start_time.isoformat(),
        "location": location,
        "issued_by": current_user.full_name
    }

    for tenant_id, _ in created_events:
        try:
            await ws_manager.broadcast_to_tenant(tenant_id, ws_event_payload)
        except Exception:
            pass

    return {
        "status": "success",
        "message": f"District convention successfully synchronized with {len(tenant_ids)} clubs.",
        "clubs_synced": len(tenant_ids)
    }
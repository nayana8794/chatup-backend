# chatup/backend/app/api/v1/members.py
import hmac
import hashlib
import uuid
import secrets
import string
import logging
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
import cloudinary
import cloudinary.uploader

from app.core.database import get_db
from app.core.deps import get_current_president, get_current_user
from app.core.security import get_password_hash
from app.core.config import settings
from app.core.ws_manager import manager
from app.models.tenant import Tenant
from app.models.user import User, UserRole, LanguagePreference
from app.models.financial import DuesInvoice
from app.schemas.member import (
    MemberCreateRequest,
    MemberResponse,
    MemberCreatedWithTempPass,
)

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/members", tags=["Member Management"])


class MemberRoleUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    role: Optional[UserRole] = None
    language: Optional[LanguagePreference] = None


class MyClubBrandingUpdate(BaseModel):
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None


class DigitalMembershipCardResponse(BaseModel):
    member_id: str
    full_name: str
    email: str
    phone_number: Optional[str] = None
    role: str
    club_name: str
    club_slug: str
    district: str
    joined_date: str
    dues_standing: str
    qr_payload: str
    card_serial: str


class MemberVerificationResult(BaseModel):
    is_valid: bool
    full_name: str
    role: str
    club_name: str
    district: str
    member_since: str
    dues_standing: str
    verified_at: str


def generate_friendly_temp_password() -> str:
    chars = string.ascii_letters + string.digits
    random_str = "".join(secrets.choice(chars) for _ in range(6))
    return f"Lions@{random_str}"


def generate_qr_signature(user_id: str, tenant_id: str) -> str:
    secret = getattr(settings, "SECRET_KEY", "chatup_default_secret_key_2026")
    if not isinstance(secret, (str, bytes)):
        secret = str(secret)
    if isinstance(secret, str):
        secret = secret.encode()
    payload = f"{user_id}:{tenant_id}".encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()[:16]


@router.post("", response_model=MemberCreatedWithTempPass)
async def create_member(
    member_in: MemberCreateRequest,
    president: User = Depends(get_current_president),
    db: AsyncSession = Depends(get_db),
):
    existing_user = await db.execute(select(User).where(User.email == member_in.email))
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address is already registered",
        )

    tenant_id = president.tenant_id
    if not tenant_id and president.role == UserRole.SUPER_ADMIN:
        first_tenant = (await db.execute(select(Tenant).limit(1))).scalar_one_or_none()
        tenant_id = first_tenant.id if first_tenant else None

    temp_password = generate_friendly_temp_password()
    hashed_pass = get_password_hash(temp_password)

    new_member = User(
        tenant_id=tenant_id,
        full_name=member_in.full_name,
        email=member_in.email,
        phone_number=member_in.phone_number,
        hashed_password=hashed_pass,
        role=member_in.role,
        language=member_in.language,
        must_change_password=True,
    )

    db.add(new_member)
    await db.commit()
    await db.refresh(new_member)

    return MemberCreatedWithTempPass(
        id=new_member.id,
        full_name=new_member.full_name,
        email=new_member.email,
        phone_number=new_member.phone_number,
        role=new_member.role,
        language=new_member.language,
        is_active=new_member.is_active,
        must_change_password=new_member.must_change_password,
        temp_password=temp_password,
    )


@router.get("", response_model=List[MemberResponse])
async def list_club_members(
    president: User = Depends(get_current_president),
    db: AsyncSession = Depends(get_db),
):
    if president.role == UserRole.SUPER_ADMIN:
        query = select(User).order_by(User.full_name)
    else:
        query = select(User).where(User.tenant_id == president.tenant_id).order_by(User.full_name)

    result = await db.execute(query)
    return result.scalars().all()


@router.patch("/{member_id}", response_model=MemberResponse)
async def update_member_role_and_details(
    member_id: uuid.UUID,
    payload: MemberRoleUpdateRequest,
    president: User = Depends(get_current_president),
    db: AsyncSession = Depends(get_db),
):
    if president.role == UserRole.SUPER_ADMIN:
        query = select(User).where(User.id == member_id)
    else:
        query = select(User).where(User.id == member_id, User.tenant_id == president.tenant_id)

    member = (await db.execute(query)).scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member account not found")

    if member.id == president.id and payload.role and payload.role != UserRole.PRESIDENT:
        raise HTTPException(
            status_code=400,
            detail="President cannot change their own role. Presidential transfers are managed via Super Admin Console.",
        )

    if payload.email and payload.email != member.email:
        duplicate = (
            await db.execute(select(User).where(User.email == payload.email, User.id != member_id))
        ).scalar_one_or_none()
        if duplicate:
            raise HTTPException(status_code=400, detail="This email address is already in use by another member")
        member.email = payload.email

    if payload.full_name is not None:
        member.full_name = payload.full_name.strip()
    if payload.phone_number is not None:
        member.phone_number = payload.phone_number.strip() if payload.phone_number else None
    if payload.role is not None:
        member.role = payload.role
    if payload.language is not None:
        member.language = payload.language

    await db.commit()
    await db.refresh(member)

    return member


@router.post("/{member_id}/reset-password", response_model=MemberCreatedWithTempPass)
async def reset_member_password(
    member_id: uuid.UUID,
    president: User = Depends(get_current_president),
    db: AsyncSession = Depends(get_db),
):
    if president.role == UserRole.SUPER_ADMIN:
        query = select(User).where(User.id == member_id)
    else:
        query = select(User).where(User.id == member_id, User.tenant_id == president.tenant_id)

    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=404, detail="Member account not found")

    temp_password = generate_friendly_temp_password()
    member.hashed_password = get_password_hash(temp_password)
    member.must_change_password = True

    await db.commit()
    await db.refresh(member)

    return MemberCreatedWithTempPass(
        id=member.id,
        full_name=member.full_name,
        email=member.email,
        phone_number=member.phone_number,
        role=member.role,
        language=member.language,
        is_active=member.is_active,
        must_change_password=member.must_change_password,
        temp_password=temp_password,
    )


@router.delete("/{member_id}")
async def delete_member(
    member_id: uuid.UUID,
    president: User = Depends(get_current_president),
    db: AsyncSession = Depends(get_db),
):
    if president.role == UserRole.SUPER_ADMIN:
        query = select(User).where(User.id == member_id)
    else:
        query = select(User).where(User.id == member_id, User.tenant_id == president.tenant_id)

    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=404, detail="Member account not found")

    if member.id == president.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own President account")

    await db.delete(member)
    await db.commit()
    return {"message": "Member removed successfully"}


# ================= PRESIDENT & MEMBER CLUB BRANDING SYNC =================
@router.get("/my-club/branding")
async def get_my_club_branding(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns active branding configuration (colors & logo URL) for the authenticated member's club."""
    if not current_user.tenant_id:
        return {"theme_config": {}}

    club = (
        await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
    ).scalar_one_or_none()
    if not club:
        return {"theme_config": {}}

    return {"theme_config": club.theme_config or {}}


@router.patch("/my-club/branding")
async def update_my_club_branding(
    payload: MyClubBrandingUpdate,
    president: User = Depends(get_current_president),
    db: AsyncSession = Depends(get_db),
):
    """Allows Club President to customize their local workspace brand colors."""
    if not president.tenant_id:
        raise HTTPException(status_code=400, detail="No club workspace assigned")

    club = (await db.execute(select(Tenant).where(Tenant.id == president.tenant_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club workspace not found")

    current_config = club.theme_config or {}
    if payload.primary_color:
        current_config["primary_color"] = payload.primary_color
    if payload.secondary_color:
        current_config["secondary_color"] = payload.secondary_color

    club.theme_config = current_config
    flag_modified(club, "theme_config")
    club.updated_at = datetime.utcnow().replace(tzinfo=None)

    await db.commit()
    try:
        await manager.broadcast_global({"type": "PLATFORM_UPDATE"})
    except Exception:
        pass
    return {"status": "success", "theme_config": club.theme_config}


@router.post("/my-club/upload-crest")
async def upload_my_club_crest(
    file: UploadFile = File(...),
    president: User = Depends(get_current_president),
    db: AsyncSession = Depends(get_db),
):
    """Allows Club President to upload their official club crest/logo to Cloudinary CDN."""
    if not president.tenant_id:
        raise HTTPException(status_code=400, detail="No club workspace assigned")

    club = (await db.execute(select(Tenant).where(Tenant.id == president.tenant_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club workspace not found")

    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are accepted")

    try:
        content = await file.read()
        upload_result = cloudinary.uploader.upload(
            content,
            folder="chatup_branding",
            public_id=f"crest_{club.slug}",
            overwrite=True,
            resource_type="image",
        )
        logo_url = upload_result.get("secure_url")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {str(e)}")

    current_config = club.theme_config or {}
    current_config["logo_url"] = logo_url
    club.theme_config = current_config
    flag_modified(club, "theme_config")
    club.updated_at = datetime.utcnow().replace(tzinfo=None)

    await db.commit()
    try:
        await manager.broadcast_global({"type": "PLATFORM_UPDATE"})
    except Exception:
        pass
    return {"status": "success", "logo_url": logo_url, "theme_config": club.theme_config}


# ================= DIGITAL MEMBERSHIP CARD & QR VERIFICATION =================
@router.get("/me/digital-card")
async def get_my_digital_membership_card(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves digital wallet membership card with 100% crash-proof fallbacks."""
    try:
        tenant_id = current_user.tenant_id
        club = None
        if tenant_id:
            club = (
                await db.execute(select(Tenant).where(Tenant.id == tenant_id))
            ).scalar_one_or_none()

        if not club:
            club = (await db.execute(select(Tenant).limit(1))).scalar_one_or_none()

        club_name = club.name if club else "Lions Community Club"
        club_slug = club.slug if club else "lions-club"
        district = (
            (club.district if club and club.district else current_user.district)
            or "District 107-N"
        )
        club_id_str = str(club.id) if club else str(uuid.uuid4())

        dues_standing = "PAID"
        try:
            inv_res = await db.execute(
                select(DuesInvoice).where(DuesInvoice.user_id == current_user.id)
            )
            invoices = inv_res.scalars().all()
            for inv in invoices:
                st = str(getattr(inv, "status", "")).upper()
                if "OVERDUE" in st:
                    dues_standing = "OVERDUE"
                    break
                elif "PENDING" in st or "UNPAID" in st:
                    dues_standing = "PENDING"
        except Exception:
            dues_standing = "PAID"

        sig = generate_qr_signature(str(current_user.id), club_id_str)
        card_serial = f"LIONS-{str(current_user.id)[:8].upper()}"
        verification_url = f"https://chatup-frontend-steel.vercel.app/verify-member?uid={current_user.id}&tid={club_id_str}&sig={sig}"

        role_str = (
            current_user.role.value
            if hasattr(current_user.role, "value")
            else str(current_user.role or "MEMBER")
        )

        joined_dt = current_user.created_at or datetime.utcnow()
        joined_iso = (
            joined_dt.isoformat()
            if hasattr(joined_dt, "isoformat")
            else str(joined_dt)
        )

        return {
            "member_id": str(current_user.id),
            "full_name": current_user.full_name or "Club Member",
            "email": current_user.email or "member@chatup.com",
            "phone_number": current_user.phone_number,
            "role": role_str,
            "club_name": club_name,
            "club_slug": club_slug,
            "district": district,
            "joined_date": joined_iso,
            "dues_standing": dues_standing,
            "qr_payload": verification_url,
            "card_serial": card_serial,
        }
    except Exception as e:
        logger.error(f"Error generating digital card: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Card generation error: {str(e)}",
        )


@router.get("/public-verify", response_model=MemberVerificationResult)
async def verify_member_credentials(
    uid: uuid.UUID,
    tid: uuid.UUID,
    sig: str,
    db: AsyncSession = Depends(get_db),
):
    """Public verification endpoint scanned by smartphones at conventions or meetings."""
    expected_sig = generate_qr_signature(str(uid), str(tid))
    if not hmac.compare_digest(sig, expected_sig):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, tampered, or expired membership card signature",
        )

    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    club = (await db.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()

    if not user or not club:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member record or associated club workspace not found",
        )

    dues_standing = "PAID"
    try:
        invoices_res = await db.execute(
            select(DuesInvoice).where(DuesInvoice.user_id == user.id)
        )
        invoices = invoices_res.scalars().all()
        for inv in invoices:
            status_val = str(getattr(inv, "status", "")).upper()
            if "OVERDUE" in status_val:
                dues_standing = "OVERDUE"
                break
            elif "PENDING" in status_val or "UNPAID" in status_val:
                dues_standing = "PENDING"
    except Exception:
        dues_standing = "PAID"

    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    member_since = (
        user.created_at.strftime("%B %Y") if user.created_at else "Active Member"
    )

    role_str = user.role.value if hasattr(user.role, "value") else str(user.role)

    return MemberVerificationResult(
        is_valid=user.is_active,
        full_name=user.full_name,
        role=role_str,
        club_name=club.name,
        district=club.district or "District 107-N",
        member_since=member_since,
        dues_standing=dues_standing,
        verified_at=now_utc,
    )
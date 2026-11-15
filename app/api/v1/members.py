# chatup/backend/app/api/v1/members.py
import uuid
import secrets
import string
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.deps import get_current_president
from app.core.security import get_password_hash
from app.models.tenant import Tenant
from app.models.user import User, UserRole, LanguagePreference
from app.schemas.member import (
    MemberCreateRequest,
    MemberResponse,
    MemberCreatedWithTempPass,
)

router = APIRouter(prefix="/members", tags=["Member Management"])


class MemberRoleUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    role: Optional[UserRole] = None
    language: Optional[LanguagePreference] = None


def generate_friendly_temp_password() -> str:
    chars = string.ascii_letters + string.digits
    random_str = "".join(secrets.choice(chars) for _ in range(6))
    return f"Lions@{random_str}"


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


# Promote / Demote / Edit Role without resetting account
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

    # Safety: President cannot demote themselves from here
    if member.id == president.id and payload.role and payload.role != UserRole.PRESIDENT:
        raise HTTPException(
            status_code=400,
            detail="President cannot change their own role. Presidential transfers are managed via Super Admin Console.",
        )

    # Validate duplicate email if email is changed
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
    return {"message": "Member removed successfully"}# chatup/backend/app/api/v1/members.py
import hmac
import hashlib
import uuid
import secrets
import string
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db
from app.core.deps import get_current_president, get_current_user
from app.core.security import get_password_hash
from app.core.config import settings
from app.models.tenant import Tenant
from app.models.user import User, UserRole, LanguagePreference
from app.models.financial import DuesInvoice, DuesStatus
from app.schemas.member import (
    MemberCreateRequest,
    MemberResponse,
    MemberCreatedWithTempPass,
)

router = APIRouter(prefix="/members", tags=["Member Management"])


class MemberRoleUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    role: Optional[UserRole] = None
    language: Optional[LanguagePreference] = None


class DigitalMembershipCardResponse(BaseModel):
    member_id: str
    full_name: str
    email: str
    phone_number: Optional[str] = None
    role: str
    club_name: str
    club_slug: str
    district: str
    joined_date: datetime
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
    """Generates a tamper-proof HMAC-SHA256 signature for physical/digital QR verification."""
    secret = settings.SECRET_KEY.encode()
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


# Promote / Demote / Edit Role without resetting account
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

    # Safety: President cannot demote themselves from here
    if member.id == president.id and payload.role and payload.role != UserRole.PRESIDENT:
        raise HTTPException(
            status_code=400,
            detail="President cannot change their own role. Presidential transfers are managed via Super Admin Console.",
        )

    # Validate duplicate email if email is changed
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


# ================= DIGITAL MEMBERSHIP CARD & QR VERIFICATION =================
@router.get("/me/digital-card", response_model=DigitalMembershipCardResponse)
async def get_my_digital_membership_card(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves digital wallet membership card parameters and signed dynamic QR for the authenticated member."""
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not belong to a chartered club workspace",
        )

    club = (
        await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
    ).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club workspace not found")

    # Check dues status for the current fiscal period
    latest_invoice = (
        await db.execute(
            select(DuesInvoice)
            .where(
                and_(
                    DuesInvoice.user_id == current_user.id,
                    DuesInvoice.tenant_id == current_user.tenant_id,
                )
            )
            .order_by(DuesInvoice.created_at.desc())
        )
    ).scalar_one_or_none()

    dues_standing = "PAID"
    if latest_invoice:
        if latest_invoice.status == DuesStatus.PENDING:
            dues_standing = "PENDING"
        elif latest_invoice.status == DuesStatus.OVERDUE:
            dues_standing = "OVERDUE"
    else:
        dues_standing = "PAID"

    sig = generate_qr_signature(str(current_user.id), str(club.id))
    card_serial = f"LIONS-{str(current_user.id)[:8].upper()}"
    verification_url = f"https://chatup-frontend-steel.vercel.app/verify-member?uid={current_user.id}&tid={club.id}&sig={sig}"

    return DigitalMembershipCardResponse(
        member_id=str(current_user.id),
        full_name=current_user.full_name,
        email=current_user.email,
        phone_number=current_user.phone_number,
        role=current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
        club_name=club.name,
        club_slug=club.slug,
        district=club.district or "District 107-N",
        joined_date=current_user.created_at,
        dues_standing=dues_standing,
        qr_payload=verification_url,
        card_serial=card_serial,
    )


@router.get("/public-verify", response_model=MemberVerificationResult)
async def verify_member_credentials(
    uid: uuid.UUID,
    tid: uuid.UUID,
    sig: str,
    db: AsyncSession = Depends(get_db),
):
    """Public verification endpoint scanned by smartphone cameras at conventions or meetings."""
    expected_sig = generate_qr_signature(str(uid), str(tid))
    if not hmac.compare_digest(sig, expected_sig):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, tampered, or expired membership card signature",
        )

    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    club = (await db.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()

    if not user or not club or user.tenant_id != club.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member record or associated club workspace not found",
        )

    latest_invoice = (
        await db.execute(
            select(DuesInvoice)
            .where(and_(DuesInvoice.user_id == user.id, DuesInvoice.tenant_id == club.id))
            .order_by(DuesInvoice.created_at.desc())
        )
    ).scalar_one_or_none()

    dues_standing = "PAID"
    if latest_invoice and latest_invoice.status != DuesStatus.PAID:
        dues_standing = (
            latest_invoice.status.value
            if hasattr(latest_invoice.status, "value")
            else str(latest_invoice.status)
        )

    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    return MemberVerificationResult(
        is_valid=user.is_active,
        full_name=user.full_name,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        club_name=club.name,
        district=club.district or "District 107-N",
        member_since=user.created_at.strftime("%B %Y"),
        dues_standing=dues_standing,
        verified_at=now_utc,
    )
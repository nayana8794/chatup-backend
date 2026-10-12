# chatup/backend/app/api/v1/members.py
import uuid
import secrets
import string
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.deps import get_current_president
from app.core.security import get_password_hash
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.member import (
    MemberCreateRequest,
    MemberResponse,
    MemberCreatedWithTempPass,
)

router = APIRouter(prefix="/members", tags=["Member Management"])

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
    # Email එක දැනටමත් තිබේදැයි පරීක්ෂා කිරීම
    existing_user = await db.execute(select(User).where(User.email == member_in.email))
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="මෙම විද්‍යුත් තැපෑල දැනටමත් ලියාපදිංචි කර ඇත (Email already registered)",
        )

    # Tenant ID තීරණය කිරීම (Super Admin නම් පළමු club එකට assign කරයි, President නම් ඔහුගේ club එකට)
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
    # Super Admin නම් පද්ධතියේ සියලු සාමාජිකයින් පෙන්වයි
    if president.role == UserRole.SUPER_ADMIN:
        query = select(User).order_by(User.full_name)
    else:
        # President නම් ඔහුගේ Club එකේ සාමාජිකයින් පමණක් පෙන්වයි
        query = select(User).where(User.tenant_id == president.tenant_id).order_by(User.full_name)

    result = await db.execute(query)
    return result.scalars().all()

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
        raise HTTPException(status_code=404, detail="සාමාජිකයා සොයාගත නොහැක (Member not found)")

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
        raise HTTPException(status_code=404, detail="සාමාජිකයා සොයාගත නොහැක (Member not found)")
    
    if member.id == president.id:
        raise HTTPException(status_code=400, detail="තමාගේම ගිණුම delete කළ නොහැක")

    await db.delete(member)
    await db.commit()
    return {"message": "සාමාජිකයා සාර්ථකව ඉවත් කරන ලදී (Member deleted successfully)"}
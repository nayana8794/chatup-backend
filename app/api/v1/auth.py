# chatup/backend/app/api/v1/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import verify_password, create_access_token, get_password_hash
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.auth import LoginRequest, Token
from app.schemas.member import PasswordChangeRequest

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/login", response_model=Token)
async def login(credentials: LoginRequest, db: AsyncSession = Depends(get_db)):
    query = select(User).where(User.email == credentials.email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role.value, "tenant_id": str(user.tenant_id) if user.tenant_id else None}
    )

    return Token(
        access_token=access_token,
        token_type="bearer",
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        full_name=user.full_name,
        language=user.language,
        must_change_password=bool(user.must_change_password), # True හෝ False ලෙස අනිවාර්යයෙන් යැවීම
    )

@router.post("/change-password")
async def change_password(
    req: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(req.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect old password")

    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Minimum 6 characters")

    current_user.hashed_password = get_password_hash(req.new_password)
    current_user.must_change_password = False
    await db.commit()

    return {"message": "Password updated successfully"}
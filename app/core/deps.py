# chatup/backend/app/core/deps.py
import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import SECRET_KEY, ALGORITHM
from app.models.user import User, UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = uuid.UUID(user_id_str)
    except Exception:
        raise credentials_exception

    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception
    return user

async def get_current_president(current_user: User = Depends(get_current_user)) -> User:
    # President හෝ Vice President හෝ Super Admin හට පමණක් සාමාජිකයින් කළමනාකරණය කළ හැක
    allowed_roles = [UserRole.PRESIDENT, UserRole.VICE_PRESIDENT, UserRole.SUPER_ADMIN]
    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only President, Vice President, or Super Admin can perform this action",
        )
    return current_user
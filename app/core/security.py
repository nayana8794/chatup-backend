from datetime import datetime, timedelta
from typing import Optional
import secrets
import string
import bcrypt
import jwt

SECRET_KEY = "chatup_super_secret_jwt_key_change_in_production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 days

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def generate_temporary_password(prefix: str = "Lions@") -> str:
    """Generates friendly temporary password: e.g., Lions@A8F2K9"""
    chars = string.ascii_uppercase + string.digits
    random_suffix = "".join(secrets.choice(chars) for _ in range(6))
    return f"{prefix}{random_suffix}"

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
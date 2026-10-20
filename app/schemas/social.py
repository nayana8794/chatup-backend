# chatup/backend/app/schemas/social.py
import uuid
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

class CommentCreate(BaseModel):
    content: str

class CommentResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    author_name: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True

class PostCreateRequest(BaseModel):
    caption: str
    image_url: Optional[str] = None

class PostResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    author_id: Optional[uuid.UUID]
    author_name: Optional[str] = None
    author_role: Optional[str] = None
    caption: str
    image_url: Optional[str] = None
    created_at: datetime
    likes_count: int = 0
    is_liked_by_me: bool = False
    comments: List[CommentResponse] = []

    class Config:
        from_attributes = True
# chatup/backend/app/schemas/document.py
import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.models.document import DocumentCategory


class DocumentResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    uploader_id: Optional[uuid.UUID] = None
    uploader_name: Optional[str] = None
    title: str
    description: Optional[str] = None
    category: DocumentCategory
    file_url: str
    file_name: str
    file_size_bytes: int
    file_type: str
    is_board_only: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
    category: Optional[DocumentCategory] = None
    is_board_only: Optional[bool] = None
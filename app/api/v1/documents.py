# chatup/backend/app/api/v1/documents.py
import os
import uuid
import logging
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, desc
import cloudinary
import cloudinary.uploader

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.user import User, UserRole
from app.models.document import ClubDocument, DocumentCategory
from app.schemas.document import DocumentResponse, DocumentUpdateRequest

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/documents", tags=["Club Document & Certificate Repository"])

BOARD_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
    UserRole.SECRETARY,
    UserRole.VICE_SECRETARY,
    UserRole.PLANNER,
    UserRole.VICE_PLANNER,
    UserRole.TREASURER,
    UserRole.VICE_TREASURER,
]


@router.get("", response_model=List[DocumentResponse])
async def list_club_documents(
    category: Optional[DocumentCategory] = Query(None),
    search: Optional[str] = Query(None),
    board_only: Optional[bool] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        return []

    is_board_member = current_user.role in BOARD_ROLES

    query = (
        select(ClubDocument, User.full_name)
        .outerjoin(User, ClubDocument.uploader_id == User.id)
        .where(ClubDocument.tenant_id == current_user.tenant_id)
    )

    # Regular members can NEVER see board-only confidential documents
    if not is_board_member:
        query = query.where(ClubDocument.is_board_only == False)
    elif board_only is not None:
        query = query.where(ClubDocument.is_board_only == board_only)

    if category:
        query = query.where(ClubDocument.category == category)

    if search:
        s = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                ClubDocument.title.ilike(s),
                ClubDocument.description.ilike(s),
                ClubDocument.file_name.ilike(s),
            )
        )

    query = query.order_by(desc(ClubDocument.created_at))
    result = await db.execute(query)
    rows = result.all()

    return [
        DocumentResponse(
            id=doc.id,
            tenant_id=doc.tenant_id,
            uploader_id=doc.uploader_id,
            uploader_name=uploader_name or "Club Officer",
            title=doc.title,
            description=doc.description,
            category=doc.category,
            file_url=doc.file_url,
            file_name=doc.file_name,
            file_size_bytes=doc.file_size_bytes,
            file_type=doc.file_type,
            is_board_only=doc.is_board_only,
            created_at=doc.created_at,
        )
        for doc, uploader_name in rows
    ]


@router.post("", response_model=DocumentResponse)
async def upload_club_document(
    title: str = Form(...),
    category: DocumentCategory = Form(DocumentCategory.GENERAL),
    description: Optional[str] = Form(None),
    is_board_only: bool = Form(False),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User does not belong to a club")

    if current_user.role not in BOARD_ROLES:
        raise HTTPException(status_code=403, detail="Permission denied to upload official club documents")

    original_filename = file.filename or "document.pdf"
    file_ext = os.path.splitext(original_filename)[1].lower().replace(".", "") or "pdf"
    
    file_bytes = await file.read()
    file_size = len(file_bytes)
    await file.seek(0)

    try:
        upload_result = cloudinary.uploader.upload(
            file.file,
            folder=f"chatup_documents/{str(current_user.tenant_id).lower()}",
            resource_type="auto",
            access_mode="public",
            use_filename=True,
            unique_filename=True,
        )
        secure_url = upload_result.get("secure_url")
        if not secure_url:
            raise Exception("Cloudinary secure URL not received")
    except Exception as e:
        logger.error("Document upload failed: %s", str(e))
        raise HTTPException(status_code=500, detail="Failed to upload document to cloud storage")

    now = datetime.utcnow().replace(tzinfo=None)
    doc = ClubDocument(
        tenant_id=current_user.tenant_id,
        uploader_id=current_user.id,
        title=title.strip(),
        description=description.strip() if description else None,
        category=category,
        file_url=secure_url,
        file_name=original_filename,
        file_size_bytes=file_size,
        file_type=file_ext,
        is_board_only=is_board_only,
        created_at=now,
        updated_at=now,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    await manager.broadcast_to_tenant(
        str(current_user.tenant_id).lower(),
        {
            "type": "DOCUMENT_UPLOADED",
            "document_id": str(doc.id),
            "title": doc.title,
            "category": doc.category.value,
            "is_board_only": doc.is_board_only,
            "uploader_name": current_user.full_name,
        },
    )

    return DocumentResponse(
        id=doc.id,
        tenant_id=doc.tenant_id,
        uploader_id=doc.uploader_id,
        uploader_name=current_user.full_name,
        title=doc.title,
        description=doc.description,
        category=doc.category,
        file_url=doc.file_url,
        file_name=doc.file_name,
        file_size_bytes=doc.file_size_bytes,
        file_type=doc.file_type,
        is_board_only=doc.is_board_only,
        created_at=doc.created_at,
    )


@router.delete("/{document_id}")
async def delete_club_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in BOARD_ROLES:
        raise HTTPException(status_code=403, detail="Permission denied to delete club documents")

    doc = (
        await db.execute(
            select(ClubDocument).where(
                ClubDocument.id == document_id,
                ClubDocument.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    tenant_id = doc.tenant_id
    await db.delete(doc)
    await db.commit()

    if tenant_id:
        await manager.broadcast_to_tenant(
            str(tenant_id).lower(),
            {"type": "DOCUMENT_DELETED", "document_id": str(document_id)},
        )

    return {"status": "success", "message": "Document deleted successfully"}
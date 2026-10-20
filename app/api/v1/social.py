# chatup/backend/app/api/v1/social.py
import uuid
import os
import shutil
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.social import SocialPost, PostLike, PostComment
from app.models.user import User, UserRole
from app.schemas.social import PostCreateRequest, PostResponse, CommentCreate, CommentResponse

router = APIRouter(prefix="/social", tags=["Club Social Wall"])
UPLOAD_DIR = "app/static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

class PostUpdateRequest(BaseModel):
    caption: str
    image_url: Optional[str] = None

@router.get("/posts", response_model=List[PostResponse])
async def list_social_posts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(SocialPost, User.full_name, User.role)
        .outerjoin(User, SocialPost.author_id == User.id)
        .where(SocialPost.tenant_id == current_user.tenant_id)
        .order_by(desc(SocialPost.created_at))
    )
    result = await db.execute(query)
    rows = result.all()

    response_list = []
    for post, author_name, author_role in rows:
        likes_res = await db.execute(select(PostLike).where(PostLike.post_id == post.id))
        likes = likes_res.scalars().all()
        likes_count = len(likes)
        is_liked_by_me = any(l.user_id == current_user.id for l in likes)

        comments_res = await db.execute(
            select(PostComment, User.full_name)
            .join(User, PostComment.user_id == User.id)
            .where(PostComment.post_id == post.id)
            .order_by(PostComment.created_at.asc())
        )
        comments_list = [
            CommentResponse(
                id=c.id,
                user_id=c.user_id,
                author_name=c_name,
                content=c.content,
                created_at=c.created_at,
            )
            for c, c_name in comments_res.all()
        ]

        response_list.append(
            PostResponse(
                id=post.id,
                tenant_id=post.tenant_id,
                author_id=post.author_id,
                author_name=author_name,
                author_role=author_role.value if author_role else "MEMBER",
                caption=post.caption,
                image_url=post.image_url,
                created_at=post.created_at,
                likes_count=likes_count,
                is_liked_by_me=is_liked_by_me,
                comments=comments_list,
            )
        )

    return response_list

@router.post("/posts", response_model=PostResponse)
async def create_social_post(
    req: PostCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    post = SocialPost(
        tenant_id=current_user.tenant_id,
        author_id=current_user.id,
        caption=req.caption,
        image_url=req.image_url,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {"type": "SOCIAL_POST_CREATED", "post_id": str(post.id)},
        )

    return PostResponse(
        id=post.id,
        tenant_id=post.tenant_id,
        author_id=post.author_id,
        author_name=current_user.full_name,
        author_role=current_user.role.value,
        caption=post.caption,
        image_url=post.image_url,
        created_at=post.created_at,
        likes_count=0,
        is_liked_by_me=False,
        comments=[],
    )

@router.put("/posts/{post_id}", response_model=PostResponse)
async def update_social_post(
    post_id: uuid.UUID,
    req: PostUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    post = (await db.execute(select(SocialPost).where(SocialPost.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Only author or President/Super Admin can update
    if post.author_id != current_user.id and current_user.role not in [UserRole.SUPER_ADMIN, UserRole.PRESIDENT]:
        raise HTTPException(status_code=403, detail="Permission denied to edit this post")

    post.caption = req.caption
    post.image_url = req.image_url
    await db.commit()
    await db.refresh(post)

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {"type": "SOCIAL_POST_UPDATED", "post_id": str(post.id)},
        )

    return PostResponse(
        id=post.id,
        tenant_id=post.tenant_id,
        author_id=post.author_id,
        author_name=current_user.full_name,
        author_role=current_user.role.value,
        caption=post.caption,
        image_url=post.image_url,
        created_at=post.created_at,
        likes_count=0,
        is_liked_by_me=False,
        comments=[],
    )

@router.delete("/posts/{post_id}")
async def delete_social_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    post = (await db.execute(select(SocialPost).where(SocialPost.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.author_id != current_user.id and current_user.role not in [UserRole.SUPER_ADMIN, UserRole.PRESIDENT, UserRole.SECRETARY]:
        raise HTTPException(status_code=403, detail="Permission denied to delete this post")

    tenant_id = post.tenant_id
    await db.delete(post)
    await db.commit()

    if tenant_id:
        await manager.broadcast_to_tenant(
            str(tenant_id).lower(),
            {"type": "SOCIAL_POST_DELETED", "post_id": str(post_id)},
        )

    return {"status": "success", "message": "Post deleted successfully"}

@router.post("/posts/{post_id}/like")
async def toggle_post_like(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == current_user.id)
        )
    ).scalar_one_or_none()

    if existing:
        await db.delete(existing)
        liked = False
    else:
        db.add(PostLike(post_id=post_id, user_id=current_user.id))
        liked = True

    await db.commit()

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {"type": "SOCIAL_POST_LIKED", "post_id": str(post_id)},
        )

    return {"status": "success", "liked": liked}

@router.post("/posts/{post_id}/comments", response_model=CommentResponse)
async def add_post_comment(
    post_id: uuid.UUID,
    req: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    comment = PostComment(
        post_id=post_id,
        user_id=current_user.id,
        content=req.content,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {"type": "SOCIAL_POST_COMMENTED", "post_id": str(post_id)},
        )

    return CommentResponse(
        id=comment.id,
        user_id=comment.user_id,
        author_name=current_user.full_name,
        content=comment.content,
        created_at=comment.created_at,
    )

@router.post("/upload-image")
async def upload_social_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    file_ext = file.filename.split(".")[-1]
    unique_filename = f"{uuid.uuid4()}.{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Local / Cloud URL එක Return කිරීම
    image_url = f"/static/uploads/{unique_filename}"
    return {"status": "success", "image_url": image_url}
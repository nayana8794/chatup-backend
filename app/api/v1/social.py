# chatup/backend/app/api/v1/social.py
import os
import uuid
import shutil
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.tenant import Tenant
from app.models.social import SocialPost, PostLike, PostComment
from app.models.user import User, UserRole
from app.schemas.social import (
    PostCreateRequest,
    PostUpdateRequest,
    PostResponse,
    CommentCreate,
    CommentResponse,
)

router = APIRouter(prefix="/social", tags=["Club Social Wall"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/upload-images")
async def upload_multiple_images(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    saved_urls = []
    for file in files:
        file_ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
        unique_filename = f"{uuid.uuid4()}.{file_ext}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        saved_urls.append(f"/static/uploads/{unique_filename}")

    return {"status": "success", "image_urls": saved_urls}

@router.get("/posts", response_model=List[PostResponse])
async def list_social_posts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(SocialPost, User.full_name, User.role)
        .outerjoin(User, SocialPost.author_id == User.id)
    )

    # Super Admin හැර අනෙක් සියලු සාමාජිකයින්ට තම Club එකේ සියලුම සාමාජිකයින්ගේ පෝස්ට් පෙන්වීම
    if current_user.role != UserRole.SUPER_ADMIN:
        query = query.where(SocialPost.tenant_id == current_user.tenant_id)

    query = query.order_by(desc(SocialPost.created_at))
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

        combined_images = list(post.image_urls or [])
        if post.image_url and post.image_url not in combined_images:
            combined_images.insert(0, post.image_url)

        response_list.append(
            PostResponse(
                id=post.id,
                tenant_id=post.tenant_id,
                author_id=post.author_id,
                author_name=author_name,
                author_role=author_role.value if author_role else "MEMBER",
                caption=post.caption,
                image_url=post.image_url,
                image_urls=combined_images,
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
    # Tenant ID තහවුරු කිරීම (Super Admin නම් පළමු club එකට fallback කිරීම)
    tenant_id = current_user.tenant_id
    if not tenant_id and current_user.role == UserRole.SUPER_ADMIN:
        first_tenant = (await db.execute(select(Tenant).limit(1))).scalar_one_or_none()
        tenant_id = first_tenant.id if first_tenant else None

    all_imgs = list(req.image_urls or [])
    if req.image_url and req.image_url not in all_imgs:
        all_imgs.insert(0, req.image_url)

    first_img = all_imgs[0] if all_imgs else None

    post = SocialPost(
        tenant_id=tenant_id,
        author_id=current_user.id,
        caption=req.caption,
        image_url=first_img,
        image_urls=all_imgs,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    # මුළු Club එකේම සාමාජිකයින්ට WebSocket මගින් සජීවීව දැනුම් දීම
    if tenant_id:
        await manager.broadcast_to_tenant(
            str(tenant_id).lower(),
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
        image_urls=post.image_urls or [],
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

    if post.author_id != current_user.id and current_user.role not in [UserRole.SUPER_ADMIN, UserRole.PRESIDENT]:
        raise HTTPException(status_code=403, detail="Permission denied to edit this post")

    all_imgs = list(req.image_urls or [])
    post.caption = req.caption
    post.image_urls = all_imgs
    post.image_url = all_imgs[0] if all_imgs else None

    await db.commit()
    await db.refresh(post)

    if post.tenant_id:
        await manager.broadcast_to_tenant(
            str(post.tenant_id).lower(),
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
        image_urls=post.image_urls or [],
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
    post = (await db.execute(select(SocialPost).where(SocialPost.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

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

    if post.tenant_id:
        await manager.broadcast_to_tenant(
            str(post.tenant_id).lower(),
            {"type": "SOCIAL_POST_LIKED", "post_id": str(post.id)},
        )

    return {"status": "success", "liked": liked}

@router.post("/posts/{post_id}/comments", response_model=CommentResponse)
async def add_post_comment(
    post_id: uuid.UUID,
    req: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    post = (await db.execute(select(SocialPost).where(SocialPost.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    comment = PostComment(
        post_id=post_id,
        user_id=current_user.id,
        content=req.content,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    if post.tenant_id:
        await manager.broadcast_to_tenant(
            str(post.tenant_id).lower(),
            {"type": "SOCIAL_POST_COMMENTED", "post_id": str(post.id)},
        )

    return CommentResponse(
        id=comment.id,
        user_id=comment.user_id,
        author_name=current_user.full_name,
        content=comment.content,
        created_at=comment.created_at,
    )
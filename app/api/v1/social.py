# chatup/backend/app/api/v1/social.py
import os
import uuid
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, or_
import cloudinary
import cloudinary.uploader
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

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/social", tags=["Club Social Wall"])

# Configure Cloudinary credentials from Environment Variables
cloudinary_cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
cloudinary_api_key = os.getenv("CLOUDINARY_API_KEY")
cloudinary_api_secret = os.getenv("CLOUDINARY_API_SECRET")

if cloudinary_cloud_name and cloudinary_api_key and cloudinary_api_secret:
    cloudinary.config(
        cloud_name=cloudinary_cloud_name.strip(),
        api_key=cloudinary_api_key.strip(),
        api_secret=cloudinary_api_secret.strip(),
        secure=True,
    )
elif os.getenv("CLOUDINARY_URL"):
    cloudinary.config(secure=True)


@router.post("/upload-images")
async def upload_multiple_images(
    files: List[UploadFile] = File(default=[]),
    file: Optional[UploadFile] = File(default=None),
    current_user: User = Depends(get_current_user),
):
    upload_list = list(files)
    if file and file not in upload_list:
        upload_list.append(file)

    if not upload_list:
        raise HTTPException(status_code=400, detail="No image files provided")

    saved_urls = []

    for f in upload_list:
        try:
            # Rewind file stream to beginning to avoid empty payload
            f.file.seek(0)

            # Upload directly and permanently to Cloudinary Cloud CDN
            upload_result = cloudinary.uploader.upload(
                f.file,
                folder="chatup_social",
                resource_type="image",
                use_filename=False,
                unique_filename=True,
            )

            secure_url = upload_result.get("secure_url")
            if secure_url:
                saved_urls.append(secure_url)
            else:
                logger.error("Cloudinary failed to return a secure URL for file: %s", f.filename)

        except Exception as e:
            logger.error("Cloudinary upload exception for %s: %s", f.filename, str(e))
            continue

    if not saved_urls:
        raise HTTPException(
            status_code=500,
            detail="Failed to upload photos to cloud storage. Please check Cloudinary configuration.",
        )

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

    first_tenant_id = (await db.execute(select(Tenant.id).limit(1))).scalar_one_or_none()

    tenant_filter = []
    if current_user.tenant_id:
        tenant_filter.append(SocialPost.tenant_id == current_user.tenant_id)
    if first_tenant_id:
        tenant_filter.append(SocialPost.tenant_id == first_tenant_id)
    tenant_filter.append(SocialPost.tenant_id.is_(None))

    if current_user.role != UserRole.SUPER_ADMIN:
        query = query.where(or_(*tenant_filter))

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
                tenant_id=post.tenant_id or current_user.tenant_id or uuid.uuid4(),
                author_id=post.author_id,
                author_name=author_name or "Club Member",
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
    tenant_id = current_user.tenant_id
    if not tenant_id:
        first_tenant = (await db.execute(select(Tenant.id).limit(1))).scalar_one_or_none()
        tenant_id = first_tenant

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

    first_tenant_id = (await db.execute(select(Tenant.id).limit(1))).scalar_one_or_none()
    broadcast_tenants = {str(t).lower() for t in [tenant_id, first_tenant_id, current_user.tenant_id] if t}
    for t_id in broadcast_tenants:
        await manager.broadcast_to_tenant(
            t_id,
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
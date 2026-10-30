# chatup/backend/app/api/v1/polls.py
import uuid
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.user import User, UserRole
from app.models.poll import Poll, PollOption, PollVoter, PollType, PollStatus
from app.schemas.poll import (
    PollCreateRequest,
    PollVoteRequest,
    PollResponse,
    PollOptionResponse,
)

router = APIRouter(prefix="/polls", tags=["Democratic Voting & Elections"])

ELECTION_MANAGERS = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
    UserRole.SECRETARY,
    UserRole.VICE_SECRETARY,
]


@router.get("", response_model=List[PollResponse])
async def list_club_polls(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        return []

    # Query polls for tenant with options
    query = (
        select(Poll)
        .where(Poll.tenant_id == current_user.tenant_id)
        .order_by(Poll.status.asc(), Poll.created_at.desc())
    )
    result = await db.execute(query)
    polls = result.scalars().all()

    # Query which polls the current user has already voted in
    voted_poll_ids = set(
        (
            await db.execute(
                select(PollVoter.poll_id).where(PollVoter.user_id == current_user.id)
            )
        ).scalars().all()
    )

    response_list = []
    for p in polls:
        opts_res = await db.execute(
            select(PollOption).where(PollOption.poll_id == p.id).order_by(PollOption.order_index)
        )
        opts = opts_res.scalars().all()
        total_votes = sum(o.vote_count for o in opts)

        creator_name = None
        if p.creator_id:
            creator_res = await db.execute(select(User.full_name).where(User.id == p.creator_id))
            creator_name = creator_res.scalar_one_or_none()

        formatted_options = []
        for o in opts:
            pct = round((o.vote_count / total_votes * 100), 1) if total_votes > 0 else 0.0
            formatted_options.append(
                PollOptionResponse(
                    id=o.id,
                    title=o.title,
                    description=o.description,
                    vote_count=o.vote_count if (p.status == PollStatus.CLOSED or not p.is_secret or p.id in voted_poll_ids) else 0,
                    percentage=pct if (p.status == PollStatus.CLOSED or not p.is_secret or p.id in voted_poll_ids) else 0.0,
                )
            )

        response_list.append(
            PollResponse(
                id=p.id,
                tenant_id=p.tenant_id,
                creator_id=p.creator_id,
                creator_name=creator_name or "Club Officer",
                title=p.title,
                description=p.description,
                poll_type=p.poll_type,
                status=p.status,
                is_secret=p.is_secret,
                total_votes=total_votes,
                has_voted=p.id in voted_poll_ids,
                expires_at=p.expires_at,
                created_at=p.created_at,
                options=formatted_options,
            )
        )

    return response_list


@router.post("", response_model=PollResponse)
async def create_club_poll(
    req: PollCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in ELECTION_MANAGERS:
        raise HTTPException(status_code=403, detail="Only Club Officers can create ballots and elections")

    now = datetime.utcnow().replace(tzinfo=None)
    clean_expires = req.expires_at.replace(tzinfo=None) if req.expires_at else None

    poll = Poll(
        tenant_id=current_user.tenant_id,
        creator_id=current_user.id,
        title=req.title.strip(),
        description=req.description.strip() if req.description else None,
        poll_type=req.poll_type,
        status=PollStatus.ACTIVE,
        is_secret=req.is_secret,
        expires_at=clean_expires,
        created_at=now,
        updated_at=now,
    )
    db.add(poll)
    await db.flush()

    for idx, opt_data in enumerate(req.options):
        opt = PollOption(
            poll_id=poll.id,
            title=opt_data.title.strip(),
            description=opt_data.description.strip() if opt_data.description else None,
            order_index=idx,
            vote_count=0,
        )
        db.add(opt)

    await db.commit()
    await db.refresh(poll)

    # Realtime WebSocket Broadcast to all club members
    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "POLL_CREATED",
                "poll_id": str(poll.id),
                "title": poll.title,
                "is_secret": poll.is_secret,
            },
        )

    opts_res = await db.execute(
        select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.order_index)
    )
    opts = opts_res.scalars().all()

    return PollResponse(
        id=poll.id,
        tenant_id=poll.tenant_id,
        creator_id=poll.creator_id,
        creator_name=current_user.full_name,
        title=poll.title,
        description=poll.description,
        poll_type=poll.poll_type,
        status=poll.status,
        is_secret=poll.is_secret,
        total_votes=0,
        has_voted=False,
        expires_at=poll.expires_at,
        created_at=poll.created_at,
        options=[
            PollOptionResponse(
                id=o.id,
                title=o.title,
                description=o.description,
                vote_count=0,
                percentage=0.0,
            )
            for o in opts
        ],
    )


@router.post("/{poll_id}/vote")
async def cast_secret_vote(
    poll_id: uuid.UUID,
    req: PollVoteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    poll = (await db.execute(select(Poll).where(Poll.id == poll_id))).scalar_one_or_none()
    if not poll:
        raise HTTPException(status_code=404, detail="Ballot not found")

    if poll.status != PollStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="This ballot has already concluded")

    now = datetime.utcnow().replace(tzinfo=None)
    if poll.expires_at and poll.expires_at <= now:
        poll.status = PollStatus.CLOSED
        await db.commit()
        raise HTTPException(status_code=400, detail="Voting deadline has passed")

    # Enforce One Member = One Vote
    existing_voter = (
        await db.execute(
            select(PollVoter).where(PollVoter.poll_id == poll_id, PollVoter.user_id == current_user.id)
        )
    ).scalar_one_or_none()

    if existing_voter:
        raise HTTPException(status_code=400, detail="You have already cast your ballot for this vote")

    if poll.poll_type == PollType.SINGLE_CHOICE and len(req.selected_option_ids) > 1:
        raise HTTPException(status_code=400, detail="Only single selection is permitted for this ballot")

    # Increment option vote counts atomically
    opts_res = await db.execute(
        select(PollOption).where(
            PollOption.poll_id == poll_id,
            PollOption.id.in_(req.selected_option_ids),
        )
    )
    valid_options = opts_res.scalars().all()
    if len(valid_options) != len(req.selected_option_ids):
        raise HTTPException(status_code=400, detail="Invalid option selection")

    for opt in valid_options:
        opt.vote_count += 1

    # Record voter receipt (mathematically detached from selected options for anonymity)
    db.add(PollVoter(poll_id=poll_id, user_id=current_user.id, voted_at=now))
    await db.commit()

    # Realtime turn-out update broadcast
    if poll.tenant_id:
        await manager.broadcast_to_tenant(
            str(poll.tenant_id).lower(),
            {
                "type": "POLL_VOTED",
                "poll_id": str(poll.id),
            },
        )

    return {"status": "success", "message": "Your ballot was cast anonymously and securely"}


@router.post("/{poll_id}/close", response_model=PollResponse)
async def close_club_poll(
    poll_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in ELECTION_MANAGERS:
        raise HTTPException(status_code=403, detail="Permission denied to conclude ballot")

    poll = (await db.execute(select(Poll).where(Poll.id == poll_id))).scalar_one_or_none()
    if not poll:
        raise HTTPException(status_code=404, detail="Ballot not found")

    poll.status = PollStatus.CLOSED
    poll.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(poll)

    if poll.tenant_id:
        await manager.broadcast_to_tenant(
            str(poll.tenant_id).lower(),
            {
                "type": "POLL_CLOSED",
                "poll_id": str(poll.id),
                "title": poll.title,
            },
        )

    opts_res = await db.execute(
        select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.order_index)
    )
    opts = opts_res.scalars().all()
    total_votes = sum(o.vote_count for o in opts)

    return PollResponse(
        id=poll.id,
        tenant_id=poll.tenant_id,
        creator_id=poll.creator_id,
        title=poll.title,
        description=poll.description,
        poll_type=poll.poll_type,
        status=poll.status,
        is_secret=poll.is_secret,
        total_votes=total_votes,
        has_voted=True,
        expires_at=poll.expires_at,
        created_at=poll.created_at,
        options=[
            PollOptionResponse(
                id=o.id,
                title=o.title,
                description=o.description,
                vote_count=o.vote_count,
                percentage=round((o.vote_count / total_votes * 100), 1) if total_votes > 0 else 0.0,
            )
            for o in opts
        ],
    )


@router.delete("/{poll_id}")
async def delete_club_poll(
    poll_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.PRESIDENT]:
        raise HTTPException(status_code=403, detail="Only President can delete election ballots")

    poll = (await db.execute(select(Poll).where(Poll.id == poll_id))).scalar_one_or_none()
    if not poll:
        raise HTTPException(status_code=404, detail="Ballot not found")

    tenant_id = poll.tenant_id
    await db.delete(poll)
    await db.commit()

    if tenant_id:
        await manager.broadcast_to_tenant(
            str(tenant_id).lower(),
            {"type": "POLL_DELETED", "poll_id": str(poll_id)},
        )

    return {"status": "success", "message": "Ballot deleted"}
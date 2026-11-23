# chatup/backend/app/api/v1/proposals.py
import uuid
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.user import User, UserRole
from app.models.event_proposal import EventProposal, ProposalStatus
from app.models.event import Event, EventType, RSVPStatus
from app.schemas.proposal import ProposalCreateRequest, ProposalResponse

router = APIRouter(prefix="/proposals", tags=["Community Event Proposals"])


async def notify_tenant(tenant_id: uuid.UUID, event_type: str, data: dict = None):
    try:
        payload = {"type": event_type, **(data or {})}
        await manager.broadcast_to_tenant(str(tenant_id).lower(), payload)
    except Exception:
        pass


@router.get("", response_model=List[ProposalResponse])
async def list_proposals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves all event proposals for the user's club workspace."""
    if not current_user.tenant_id:
        return []

    query = (
        select(EventProposal)
        .where(EventProposal.tenant_id == current_user.tenant_id)
        .order_by(EventProposal.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=ProposalResponse)
async def create_proposal(
    payload: ProposalCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Allows any club member to propose a new event or service program."""
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User does not belong to a club workspace")

    now = datetime.utcnow().replace(tzinfo=None)
    proposal = EventProposal(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        proposer_id=current_user.id,
        proposer_name=current_user.full_name,
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        proposed_date=payload.proposed_date.replace(tzinfo=None),
        location=payload.location.strip() if payload.location else None,
        estimated_budget=payload.estimated_budget or 0.0,
        status=ProposalStatus.PENDING_ENDORSEMENT,
        endorsed_user_ids=[],
        created_at=now,
        updated_at=now,
    )
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)

    await notify_tenant(current_user.tenant_id, "EVENT_PROPOSAL_CREATED", {"proposal_id": str(proposal.id)})
    return proposal


@router.post("/{proposal_id}/endorse", response_model=ProposalResponse)
async def endorse_proposal(
    proposal_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Allows another member to second/endorse a proposal."""
    proposal = (
        await db.execute(select(EventProposal).where(EventProposal.id == proposal_id))
    ).scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    if proposal.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    user_id_str = str(current_user.id).lower()
    endorsed_list = list(proposal.endorsed_user_ids or [])

    if user_id_str in endorsed_list:
        raise HTTPException(status_code=400, detail="You have already endorsed this proposal")

    endorsed_list.append(user_id_str)
    proposal.endorsed_user_ids = endorsed_list

    # If at least 2 members endorse, automatically elevate to President approval queue
    if len(endorsed_list) >= 2 and proposal.status == ProposalStatus.PENDING_ENDORSEMENT:
        proposal.status = ProposalStatus.PENDING_PRESIDENT_APPROVAL

    proposal.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(proposal)

    await notify_tenant(proposal.tenant_id, "EVENT_PROPOSAL_ENDORSED", {"proposal_id": str(proposal.id)})
    return proposal


@router.post("/{proposal_id}/approve", response_model=ProposalResponse)
async def approve_and_publish_proposal(
    proposal_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """President approves the proposal, officially converting it into a club event in the calendar."""
    is_pres = ["SUPER_ADMIN", "PRESIDENT", "VICE_PRESIDENT"].includes(current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role))
    if not is_pres and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only Club President can approve event proposals.")

    proposal = (
        await db.execute(select(EventProposal).where(EventProposal.id == proposal_id))
    ).scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    if proposal.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    proposal.status = ProposalStatus.APPROVED_AND_PUBLISHED
    proposal.updated_at = datetime.utcnow().replace(tzinfo=None)

    # Automatically create official club event in calendar
    new_event = Event(
        id=uuid.uuid4(),
        tenant_id=proposal.tenant_id,
        creator_id=current_user.id,
        title=proposal.title,
        description=f"Approved Community Proposal (Proposed by {proposal.proposer_name}).\n\n{proposal.description or ''}",
        start_time=proposal.proposed_date,
        end_time=proposal.proposed_date,
        location=proposal.location or "Club Assembly Hall",
        event_type=EventType.COMMUNITY_PROJECT,
        allows_guests=True,
        created_at=datetime.utcnow().replace(tzinfo=None),
        updated_at=datetime.utcnow().replace(tzinfo=None),
    )
    db.add(new_event)
    await db.commit()
    await db.refresh(proposal)

    await notify_tenant(proposal.tenant_id, "EVENT_CREATED", {"event_id": str(new_event.id)})
    await notify_tenant(proposal.tenant_id, "EVENT_PROPOSAL_APPROVED", {"proposal_id": str(proposal.id)})
    return proposal


@router.delete("/{proposal_id}")
async def delete_proposal(
    proposal_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deletes a proposal (proposer or president only)."""
    proposal = (
        await db.execute(select(EventProposal).where(EventProposal.id == proposal_id))
    ).scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    is_pres = ["SUPER_ADMIN", "PRESIDENT", "VICE_PRESIDENT"].includes(current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role))
    if proposal.proposer_id != current_user.id and not is_pres:
        raise HTTPException(status_code=403, detail="Forbidden")

    tenant_id = proposal.tenant_id
    await db.delete(proposal)
    await db.commit()

    await notify_tenant(tenant_id, "EVENT_PROPOSAL_DELETED", {"proposal_id": str(proposal_id)})
    return {"message": "Proposal deleted"}
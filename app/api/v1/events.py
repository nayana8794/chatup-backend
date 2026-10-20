# chatup/backend/app/api/v1/events.py
import uuid
from datetime import datetime
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.event import Event, EventRSVP, EventType, RSVPStatus
from app.models.user import User, UserRole
from app.schemas.event import (
    EventCreateRequest,
    EventUpdateRequest,
    SetRSVPRequest,
    EventResponse,
    AttendeeInfo,
    DutyShiftItem,
)

router = APIRouter(prefix="/events", tags=["Event Planning & Calendar"])

EVENT_MANAGERS = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
    UserRole.PLANNER,
    UserRole.VICE_PLANNER,
]

@router.get("", response_model=List[EventResponse])
async def list_club_events(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Event, User.full_name.label("planner_name"))
        .outerjoin(User, Event.planner_id == User.id)
    )

    if current_user.role != UserRole.SUPER_ADMIN:
        query = query.where(Event.tenant_id == current_user.tenant_id)

    query = query.order_by(Event.start_time.asc())
    result = await db.execute(query)
    events_rows = result.all()

    response_list = []
    for event, planner_name in events_rows:
        rsvps = (await db.execute(select(EventRSVP).where(EventRSVP.event_id == event.id))).scalars().all()
        
        going = sum(1 for r in rsvps if r.status == RSVPStatus.GOING)
        maybe = sum(1 for r in rsvps if r.status == RSVPStatus.MAYBE)
        declined = sum(1 for r in rsvps if r.status == RSVPStatus.DECLINED)
        total_guests = sum(r.guest_count for r in rsvps if r.status == RSVPStatus.GOING)

        user_rsvp = next((r for r in rsvps if r.user_id == current_user.id), None)
        my_rsvp = user_rsvp.status if user_rsvp else None
        my_guest_count = user_rsvp.guest_count if user_rsvp else 0
        my_duty_shift_id = user_rsvp.duty_shift_id if user_rsvp else None

        # Calculate live volunteer counts for duty shifts
        formatted_shifts = []
        raw_shifts = event.duty_shifts or []
        for s in raw_shifts:
            shift_id = s.get("id")
            assigned = sum(1 for r in rsvps if r.duty_shift_id == shift_id and r.status == RSVPStatus.GOING)
            formatted_shifts.append(
                DutyShiftItem(
                    id=shift_id,
                    title=s.get("title", ""),
                    start_time=s.get("start_time", ""),
                    end_time=s.get("end_time", ""),
                    max_volunteers=s.get("max_volunteers"),
                    assigned_count=assigned,
                )
            )

        item = EventResponse(
            id=event.id,
            tenant_id=event.tenant_id,
            planner_id=event.planner_id,
            planner_name=planner_name,
            title=event.title,
            description=event.description,
            event_type=event.event_type,
            start_time=event.start_time,
            end_time=event.end_time,
            location=event.location,
            is_online=event.is_online,
            meeting_link=event.meeting_link,
            allows_guests=event.allows_guests or False,
            duty_shifts=formatted_shifts,
            created_at=event.created_at,
            going_count=going,
            maybe_count=maybe,
            declined_count=declined,
            total_guests_count=total_guests,
            my_rsvp=my_rsvp,
            my_guest_count=my_guest_count,
            my_duty_shift_id=my_duty_shift_id,
        )
        response_list.append(item)

    return response_list

@router.post("", response_model=EventResponse)
async def create_event(
    event_in: EventCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in EVENT_MANAGERS:
        raise HTTPException(status_code=403, detail="Only Planner or President can create events")

    start_time_clean = event_in.start_time.replace(tzinfo=None) if event_in.start_time.tzinfo else event_in.start_time
    end_time_clean = (
        event_in.end_time.replace(tzinfo=None) if event_in.end_time and event_in.end_time.tzinfo else event_in.end_time
    )

    now = datetime.utcnow()
    if start_time_clean <= now:
        raise HTTPException(status_code=400, detail="Event start time must be in the future")

    # Serialize duty shifts
    serialized_shifts = [s.model_dump() for s in (event_in.duty_shifts or [])]

    new_event = Event(
        tenant_id=current_user.tenant_id,
        planner_id=current_user.id,
        title=event_in.title,
        description=event_in.description,
        event_type=event_in.event_type,
        start_time=start_time_clean,
        end_time=end_time_clean,
        location=event_in.location,
        is_online=event_in.is_online,
        meeting_link=event_in.meeting_link,
        allows_guests=event_in.allows_guests,
        duty_shifts=serialized_shifts,
    )

    db.add(new_event)
    await db.commit()
    await db.refresh(new_event)

    # Creator auto-mark as GOING
    creator_rsvp = EventRSVP(
        event_id=new_event.id,
        user_id=current_user.id,
        status=RSVPStatus.GOING,
        guest_count=0,
        duty_shift_id=None,
    )
    db.add(creator_rsvp)
    await db.commit()

    # Real-Time WebSocket broadcast to all connected tenant members for immediate dashboard & badge sync
    if new_event.tenant_id:
        await manager.broadcast_to_tenant(
            str(new_event.tenant_id).lower(),
            {
                "type": "EVENT_CREATED",
                "event_id": str(new_event.id),
                "title": new_event.title,
                "start_time": new_event.start_time.isoformat(),
                "location": new_event.location,
                "planner_name": current_user.full_name,
            },
        )

    formatted_shifts = [
        DutyShiftItem(
            id=s.get("id", str(uuid.uuid4())),
            title=s.get("title", ""),
            start_time=s.get("start_time", ""),
            end_time=s.get("end_time", ""),
            max_volunteers=s.get("max_volunteers"),
            assigned_count=0,
        )
        for s in serialized_shifts
    ]

    return EventResponse(
        id=new_event.id,
        tenant_id=new_event.tenant_id,
        planner_id=new_event.planner_id,
        planner_name=current_user.full_name,
        title=new_event.title,
        description=new_event.description,
        event_type=new_event.event_type,
        start_time=new_event.start_time,
        end_time=new_event.end_time,
        location=new_event.location,
        is_online=new_event.is_online,
        meeting_link=new_event.meeting_link,
        allows_guests=new_event.allows_guests,
        duty_shifts=formatted_shifts,
        created_at=new_event.created_at,
        going_count=1,
        maybe_count=0,
        declined_count=0,
        total_guests_count=0,
        my_rsvp=RSVPStatus.GOING,
        my_guest_count=0,
        my_duty_shift_id=None,
    )

@router.put("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: uuid.UUID,
    event_update: EventUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in EVENT_MANAGERS:
        raise HTTPException(status_code=403, detail="Permission denied to edit events")

    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    now = datetime.utcnow()
    if event.start_time <= now:
        raise HTTPException(status_code=400, detail="Cannot edit an event that has already started or passed")

    if event_update.title is not None:
        event.title = event_update.title
    if event_update.description is not None:
        event.description = event_update.description
    if event_update.event_type is not None:
        event.event_type = event_update.event_type
    if event_update.location is not None:
        event.location = event_update.location
    if event_update.is_online is not None:
        event.is_online = event_update.is_online
    if event_update.meeting_link is not None:
        event.meeting_link = event_update.meeting_link
    if event_update.allows_guests is not None:
        event.allows_guests = event_update.allows_guests
    if event_update.duty_shifts is not None:
        event.duty_shifts = [s.model_dump() for s in event_update.duty_shifts]

    if event_update.start_time is not None:
        s_clean = (
            event_update.start_time.replace(tzinfo=None)
            if event_update.start_time.tzinfo
            else event_update.start_time
        )
        if s_clean <= now:
            raise HTTPException(status_code=400, detail="New start time must be in the future")
        event.start_time = s_clean

    if event_update.end_time is not None:
        e_clean = (
            event_update.end_time.replace(tzinfo=None)
            if event_update.end_time.tzinfo
            else event_update.end_time
        )
        event.end_time = e_clean

    await db.commit()
    await db.refresh(event)

    # Real-Time Update Broadcast
    if event.tenant_id:
        await manager.broadcast_to_tenant(
            str(event.tenant_id).lower(),
            {
                "type": "EVENT_UPDATED",
                "event_id": str(event.id),
                "title": event.title,
            },
        )

    rsvps = (await db.execute(select(EventRSVP).where(EventRSVP.event_id == event.id))).scalars().all()
    going = sum(1 for r in rsvps if r.status == RSVPStatus.GOING)
    maybe = sum(1 for r in rsvps if r.status == RSVPStatus.MAYBE)
    declined = sum(1 for r in rsvps if r.status == RSVPStatus.DECLINED)
    total_guests = sum(r.guest_count for r in rsvps if r.status == RSVPStatus.GOING)

    user_rsvp = next((r for r in rsvps if r.user_id == current_user.id), None)
    my_rsvp = user_rsvp.status if user_rsvp else None
    my_guest_count = user_rsvp.guest_count if user_rsvp else 0
    my_duty_shift_id = user_rsvp.duty_shift_id if user_rsvp else None

    planner_name = (
        await db.execute(select(User.full_name).where(User.id == event.planner_id))
    ).scalar_one_or_none()

    formatted_shifts = [
        DutyShiftItem(
            id=s.get("id"),
            title=s.get("title", ""),
            start_time=s.get("start_time", ""),
            end_time=s.get("end_time", ""),
            max_volunteers=s.get("max_volunteers"),
            assigned_count=sum(1 for r in rsvps if r.duty_shift_id == s.get("id") and r.status == RSVPStatus.GOING),
        )
        for s in (event.duty_shifts or [])
    ]

    return EventResponse(
        id=event.id,
        tenant_id=event.tenant_id,
        planner_id=event.planner_id,
        planner_name=planner_name,
        title=event.title,
        description=event.description,
        event_type=event.event_type,
        start_time=event.start_time,
        end_time=event.end_time,
        location=event.location,
        is_online=event.is_online,
        meeting_link=event.meeting_link,
        allows_guests=event.allows_guests,
        duty_shifts=formatted_shifts,
        created_at=event.created_at,
        going_count=going,
        maybe_count=maybe,
        declined_count=declined,
        total_guests_count=total_guests,
        my_rsvp=my_rsvp,
        my_guest_count=my_guest_count,
        my_duty_shift_id=my_duty_shift_id,
    )

@router.post("/{event_id}/rsvp")
async def set_event_rsvp(
    event_id: uuid.UUID,
    rsvp_in: SetRSVPRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    now = datetime.utcnow()
    if event.start_time <= now:
        raise HTTPException(status_code=400, detail="Cannot change RSVP for an event that has already started")

    # Guest count security validation
    guests = max(0, rsvp_in.guest_count or 0)
    if not event.allows_guests:
        guests = 0

    # Duty shift validation
    selected_shift_id = rsvp_in.duty_shift_id
    if selected_shift_id:
        existing_shifts = event.duty_shifts or []
        shift = next((s for s in existing_shifts if s.get("id") == selected_shift_id), None)
        if not shift:
            selected_shift_id = None
        elif shift.get("max_volunteers"):
            # Check capacity
            current_volunteers = (
                await db.execute(
                    select(func.count(EventRSVP.id)).where(
                        EventRSVP.event_id == event_id,
                        EventRSVP.duty_shift_id == selected_shift_id,
                        EventRSVP.user_id != current_user.id,
                        EventRSVP.status == RSVPStatus.GOING,
                    )
                )
            ).scalar() or 0
            if current_volunteers >= shift["max_volunteers"] and rsvp_in.status == RSVPStatus.GOING:
                raise HTTPException(status_code=400, detail="This duty shift slot is already full")

    existing_rsvp = (
        await db.execute(
            select(EventRSVP).where(EventRSVP.event_id == event_id, EventRSVP.user_id == current_user.id)
        )
    ).scalar_one_or_none()

    if existing_rsvp:
        existing_rsvp.status = rsvp_in.status
        existing_rsvp.guest_count = guests if rsvp_in.status == RSVPStatus.GOING else 0
        existing_rsvp.duty_shift_id = selected_shift_id if rsvp_in.status == RSVPStatus.GOING else None
        existing_rsvp.updated_at = datetime.utcnow()
    else:
        db.add(
            EventRSVP(
                event_id=event_id,
                user_id=current_user.id,
                status=rsvp_in.status,
                guest_count=guests if rsvp_in.status == RSVPStatus.GOING else 0,
                duty_shift_id=selected_shift_id if rsvp_in.status == RSVPStatus.GOING else None,
            )
        )

    await db.commit()

    if event.tenant_id:
        await manager.broadcast_to_tenant(
            str(event.tenant_id).lower(),
            {
                "type": "RSVP_UPDATED",
                "event_id": str(event.id),
                "user_name": current_user.full_name,
                "status": rsvp_in.status.value,
            },
        )

    return {"status": "success", "rsvp": rsvp_in.status.value, "guest_count": guests, "duty_shift_id": selected_shift_id}

@router.get("/{event_id}/attendees", response_model=List[AttendeeInfo])
async def get_event_attendees(
    event_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    shifts_dict = {s.get("id"): s.get("title") for s in (event.duty_shifts or [])} if event else {}

    query = (
        select(EventRSVP.status, EventRSVP.guest_count, EventRSVP.duty_shift_id, User.id, User.full_name, User.role)
        .join(User, EventRSVP.user_id == User.id)
        .where(EventRSVP.event_id == event_id)
        .order_by(User.full_name.asc())
    )
    result = await db.execute(query)
    rows = result.all()

    return [
        AttendeeInfo(
            user_id=uid,
            full_name=name,
            role=role.value,
            status=status,
            guest_count=guest_count or 0,
            duty_shift_id=shift_id,
            duty_shift_title=shifts_dict.get(shift_id) if shift_id else None,
        )
        for status, guest_count, shift_id, uid, name, role in rows
    ]

@router.delete("/{event_id}")
async def delete_event(
    event_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in EVENT_MANAGERS:
        raise HTTPException(status_code=403, detail="Permission denied to delete events")

    event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    tenant_id = event.tenant_id
    await db.delete(event)
    await db.commit()

    if tenant_id:
        await manager.broadcast_to_tenant(
            str(tenant_id).lower(),
            {
                "type": "EVENT_DELETED",
                "event_id": str(event_id),
            },
        )

    return {"message": "Event deleted successfully"}
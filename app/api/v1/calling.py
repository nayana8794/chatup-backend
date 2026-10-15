# chatup/backend/app/api/v1/calling.py
import os
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from livekit import api
from app.core.deps import get_current_user
from app.models.user import User
from app.core.ws_manager import manager  # Added missing ws_manager import

router = APIRouter(prefix="/calling", tags=["Audio & Video Calling"])

# LiveKit Credentials (Production වලදී .env ගොනුවෙන් ලබා ගත හැක)
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
LIVEKIT_WS_URL = os.getenv("LIVEKIT_WS_URL", "wss://chatup-demo.livekit.cloud")

class TokenResponse(BaseModel):
    token: str
    ws_url: str
    room_name: str

@router.post("/token", response_model=TokenResponse)
async def generate_calling_token(
    current_user: User = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User does not belong to any club workspace")

    room_name = f"club-room-{str(current_user.tenant_id)}"
    identity = str(current_user.id)
    name = current_user.full_name

    try:
        lk_token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(identity) \
            .with_name(name) \
            .with_grants(api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            ))
        
        token_str = lk_token.to_jwt()

        return TokenResponse(
            token=token_str,
            ws_url=LIVEKIT_WS_URL,
            room_name=room_name,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate calling token: {str(e)}",
        )

@router.post("/start")
async def start_club_meeting_call(
    current_user: User = Depends(get_current_user),
):
    allowed = ["SUPER_ADMIN", "PRESIDENT", "VICE_PRESIDENT", "PLANNER", "VICE_PLANNER"]
    if current_user.role not in allowed:
        raise HTTPException(status_code=403, detail="Only Club Officers can initiate a live meeting call")

    room_name = f"club-room-{str(current_user.tenant_id)}"

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "INCOMING_CALL",
                "room_name": room_name,
                "host_name": current_user.full_name,
                "host_role": current_user.role.value,
            },
        )

    return {"status": "success", "message": "Call broadcast sent to all club members", "room_name": room_name}
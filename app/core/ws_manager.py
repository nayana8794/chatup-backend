# chatup/backend/app/core/ws_manager.py
from typing import Dict, Set, Optional
from datetime import datetime, timedelta
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # Room-level active WebSockets: { room_id: Set[WebSocket] }
        self.active_rooms: Dict[str, Set[WebSocket]] = {}
        # Tenant-level active WebSockets for global notifications: { tenant_id: Set[WebSocket] }
        self.active_tenants: Dict[str, Set[WebSocket]] = {}
        # Real-time Document Locks: { minute_id: dict }
        self.mom_locks: Dict[str, dict] = {}

    # Room connections
    async def connect_room(self, room_id: str, websocket: WebSocket):
        await websocket.accept()
        r_key = str(room_id).lower()
        if r_key not in self.active_rooms:
            self.active_rooms[r_key] = set()
        self.active_rooms[r_key].add(websocket)

    def disconnect_room(self, room_id: str, websocket: WebSocket):
        r_key = str(room_id).lower()
        if r_key in self.active_rooms:
            self.active_rooms[r_key].discard(websocket)
            if not self.active_rooms[r_key]:
                del self.active_rooms[r_key]

    async def broadcast_to_room(self, room_id: str, message_data: dict):
        r_key = str(room_id).lower()
        if r_key in self.active_rooms:
            for connection in list(self.active_rooms[r_key]):
                try:
                    await connection.send_json(message_data)
                except Exception:
                    self.disconnect_room(r_key, connection)

    # Tenant-level global connections
    async def connect_tenant(self, tenant_id: str, websocket: WebSocket):
        await websocket.accept()
        t_key = str(tenant_id).lower()
        if t_key not in self.active_tenants:
            self.active_tenants[t_key] = set()
        self.active_tenants[t_key].add(websocket)

    def disconnect_tenant(self, tenant_id: str, websocket: WebSocket):
        t_key = str(tenant_id).lower()
        if t_key in self.active_tenants:
            self.active_tenants[t_key].discard(websocket)
            if not self.active_tenants[t_key]:
                del self.active_tenants[t_key]

    async def broadcast_to_tenant(self, tenant_id: str, event_data: dict):
        t_key = str(tenant_id).lower()
        # Case-insensitive robust matching
        for active_t, conns in list(self.active_tenants.items()):
            if active_t.lower() == t_key:
                for connection in list(conns):
                    try:
                        await connection.send_json(event_data)
                    except Exception:
                        self.disconnect_tenant(active_t, connection)

    # Real-time MOM Document Lock Engine
    def acquire_mom_lock(
        self, minute_id: str, user_id: str, user_name: str, user_role: str, force: bool = False
    ) -> tuple[bool, Optional[dict]]:
        now = datetime.utcnow()
        current_lock = self.mom_locks.get(minute_id)

        if current_lock and current_lock["expires_at"] > now:
            if current_lock["user_id"] == user_id or force:
                self.mom_locks[minute_id] = {
                    "minute_id": minute_id,
                    "user_id": user_id,
                    "user_name": user_name,
                    "user_role": user_role,
                    "locked_at": now,
                    "expires_at": now + timedelta(seconds=60),
                }
                return True, self.mom_locks[minute_id]
            return False, current_lock

        self.mom_locks[minute_id] = {
            "minute_id": minute_id,
            "user_id": user_id,
            "user_name": user_name,
            "user_role": user_role,
            "locked_at": now,
            "expires_at": now + timedelta(seconds=60),
        }
        return True, self.mom_locks[minute_id]

    def heartbeat_mom_lock(self, minute_id: str, user_id: str) -> bool:
        current_lock = self.mom_locks.get(minute_id)
        if current_lock and current_lock["user_id"] == user_id:
            current_lock["expires_at"] = datetime.utcnow() + timedelta(seconds=60)
            return True
        return False

    def release_mom_lock(self, minute_id: str, user_id: Optional[str] = None) -> bool:
        current_lock = self.mom_locks.get(minute_id)
        if not current_lock:
            return False
        if user_id is None or current_lock["user_id"] == user_id:
            del self.mom_locks[minute_id]
            return True
        return False

    def get_mom_lock(self, minute_id: str) -> Optional[dict]:
        current_lock = self.mom_locks.get(minute_id)
        if current_lock and current_lock["expires_at"] > datetime.utcnow():
            return current_lock
        return None

manager = ConnectionManager()
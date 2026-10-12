# chatup/backend/app/core/ws_manager.py
from typing import Dict, Set, Optional
from datetime import datetime, timedelta
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # Room-level active WebSockets: { room_id: Set[WebSocket] }
        self.active_rooms: Dict[str, Set[WebSocket]] = {}
        # Tenant-level active WebSockets: { tenant_id: Set[WebSocket] }
        self.active_tenants: Dict[str, Set[WebSocket]] = {}
        # Real-time Document Locks: { minute_id: dict }
        self.mom_locks: Dict[str, dict] = {}

    # Room connections
    async def connect_room(self, room_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_rooms:
            self.active_rooms[room_id] = set()
        self.active_rooms[room_id].add(websocket)

    def disconnect_room(self, room_id: str, websocket: WebSocket):
        if room_id in self.active_rooms:
            self.active_rooms[room_id].discard(websocket)
            if not self.active_rooms[room_id]:
                del self.active_rooms[room_id]

    async def broadcast_to_room(self, room_id: str, message_data: dict):
        if room_id in self.active_rooms:
            for connection in list(self.active_rooms[room_id]):
                try:
                    await connection.send_json(message_data)
                except Exception:
                    self.disconnect_room(room_id, connection)

    # Tenant-level global connections
    async def connect_tenant(self, tenant_id: str, websocket: WebSocket):
        await websocket.accept()
        if tenant_id not in self.active_tenants:
            self.active_tenants[tenant_id] = set()
        self.active_tenants[tenant_id].add(websocket)

    def disconnect_tenant(self, tenant_id: str, websocket: WebSocket):
        if tenant_id in self.active_tenants:
            self.active_tenants[tenant_id].discard(websocket)
            if not self.active_tenants[tenant_id]:
                del self.active_tenants[tenant_id]

    async def broadcast_to_tenant(self, tenant_id: str, event_data: dict):
        if tenant_id in self.active_tenants:
            for connection in list(self.active_tenants[tenant_id]):
                try:
                    await connection.send_json(event_data)
                except Exception:
                    self.disconnect_tenant(tenant_id, connection)

    # Real-time MOM Document Lock Engine
    def acquire_mom_lock(
        self, minute_id: str, user_id: str, user_name: str, user_role: str, force: bool = False
    ) -> tuple[bool, Optional[dict]]:
        now = datetime.utcnow()
        current_lock = self.mom_locks.get(minute_id)

        # Lock එකක් පවතී නම් සහ එය තවමත් වලංගු නම් (විනාඩි 1කට අඩු)
        if current_lock and current_lock["expires_at"] > now:
            # එම පරිශීලකයාම නැවත විවෘත කරන්නේ නම් හෝ President විසින් Take Over කරන්නේ නම්
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
            # වෙනත් කෙනෙකු සංස්කරණය කරමින් සිටී නම් Lock එක ප්‍රතික්ෂේප කිරීම
            return False, current_lock

        # Lock එකක් නැතිනම් හෝ Expire වී ඇත්නම් අලුතින් Lock කිරීම
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
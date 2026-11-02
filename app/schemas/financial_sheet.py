# chatup/backend/app/schemas/financial_sheet.py
import uuid
from datetime import datetime
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field
from app.models.financial_sheet import SheetType, SheetStatus


class SheetCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    fiscal_year: str = Field(default="2026/2027", max_length=50)
    sheet_type: SheetType = SheetType.BUDGET
    grid_data: Dict[str, Any]


class SheetUpdateRequest(BaseModel):
    title: Optional[str] = None
    fiscal_year: Optional[str] = None
    sheet_type: Optional[SheetType] = None
    grid_data: Optional[Dict[str, Any]] = None


class SheetVoidRequest(BaseModel):
    deletion_reason: str = Field(..., min_length=5, max_length=500)


class SheetResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    creator_id: Optional[uuid.UUID] = None
    creator_name: Optional[str] = None
    approved_by_id: Optional[uuid.UUID] = None
    approver_name: Optional[str] = None
    title: str
    fiscal_year: str
    sheet_type: SheetType
    status: SheetStatus
    grid_data: Dict[str, Any]
    total_income: float
    total_expense: float
    net_balance: float
    is_deleted: bool
    deleted_at: Optional[datetime] = None
    deleted_by_name: Optional[str] = None
    deletion_reason: Optional[str] = None
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
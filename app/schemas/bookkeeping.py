# chatup/backend/app/schemas/bookkeeping.py
import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from app.models.financial import TransactionType


class TransactionCreateRequest(BaseModel):
    transaction_type: TransactionType
    category: str = Field(..., min_length=2, max_length=100)
    description: str = Field(..., min_length=2, max_length=255)
    amount: float = Field(..., gt=0)
    reference_number: Optional[str] = None
    transaction_date: Optional[datetime] = None


class TransactionResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    transaction_type: TransactionType
    category: str
    description: str
    amount: float
    currency: str
    reference_number: Optional[str] = None
    transaction_date: datetime
    recorded_by: Optional[uuid.UUID] = None
    recorder_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class BookkeepingSummaryResponse(BaseModel):
    total_dues_income: float
    total_other_income: float
    total_expenses: float
    net_treasury_balance: float
    dues_collection_rate: float
    total_transactions_count: int
    category_breakdown: List[dict]
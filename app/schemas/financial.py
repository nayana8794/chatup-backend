# chatup/backend/app/schemas/financial.py
import uuid
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from app.models.financial import DuesStatus, TransactionType, BillingCycle

class BankDetailsUpdate(BaseModel):
    recipient_name: str
    iban: str
    bic: str
    mobilepay_number: Optional[str] = None

class BatchGenerateDuesRequest(BaseModel):
    title: str
    billing_cycle: BillingCycle = BillingCycle.YEARLY
    amount: float
    due_date: datetime

class ReportPaymentRequest(BaseModel):
    payment_method: str
    transaction_reference: Optional[str] = None
    notes: Optional[str] = None

class ConfirmPaymentRequest(BaseModel):
    payment_method: Optional[str] = None
    notes: Optional[str] = None

class DuesInvoiceResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    member_name: str
    member_role: str
    member_email: str
    title: str
    billing_cycle: BillingCycle
    amount: float
    currency: str
    due_date: datetime
    status: DuesStatus
    is_approved: bool = True
    reported_at: Optional[datetime] = None
    reported_method: Optional[str] = None
    paid_at: Optional[datetime] = None
    payment_method: Optional[str] = None
    verified_by: Optional[uuid.UUID] = None
    verifier_name: Optional[str] = None
    reference_number: str
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class FinancialStatsResponse(BaseModel):
    total_collected: float
    total_pending: float
    total_awaiting_verification: float
    paid_members_count: int
    pending_members_count: int
    awaiting_verification_count: int
    pending_approval_count: int = 0
    recipient_name: str
    club_iban: str
    club_bic: str
    mobilepay_number: Optional[str] = None

class TransactionCreateRequest(BaseModel):
    title: str
    transaction_type: TransactionType
    category: str
    amount: float
    notes: Optional[str] = None

class TransactionResponse(BaseModel):
    id: uuid.UUID
    title: str
    transaction_type: TransactionType
    category: str
    amount: float
    transaction_date: datetime
    notes: Optional[str]
    recorder_name: Optional[str]

    class Config:
        from_attributes = True
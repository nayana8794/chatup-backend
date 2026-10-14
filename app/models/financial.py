# chatup/backend/app/models/financial.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum, Numeric, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base

class DuesStatus(str, enum.Enum):
    PENDING = "PENDING"
    AWAITING_VERIFICATION = "AWAITING_VERIFICATION"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    WAIVED = "WAIVED"

class BillingCycle(str, enum.Enum):
    YEARLY = "YEARLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ONE_TIME = "ONE_TIME"

class TransactionType(str, enum.Enum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"

class DuesInvoice(Base):
    __tablename__ = "dues_invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    title = Column(String(255), nullable=False)
    billing_cycle = Column(Enum(BillingCycle), default=BillingCycle.YEARLY, server_default="YEARLY", nullable=False)
    amount = Column(Numeric(10, 2), nullable=False, default=120.00)
    currency = Column(String(10), default="EUR", nullable=False)
    due_date = Column(DateTime, nullable=False)

    status = Column(Enum(DuesStatus), default=DuesStatus.PENDING, nullable=False)
    
    # President Approval flag (Treasurer assign කළ පසු President අනුමත කළ යුතුය)
    is_approved = Column(Boolean, default=True, server_default="true", nullable=False)

    # Member payment report details
    reported_at = Column(DateTime, nullable=True)
    reported_method = Column(String(100), nullable=True)
    transaction_reference = Column(String(255), nullable=True)

    # Treasurer verification details
    paid_at = Column(DateTime, nullable=True)
    payment_method = Column(String(100), nullable=True)
    verified_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    reference_number = Column(String(50), nullable=False, index=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    user = relationship("User", foreign_keys=[user_id])
    verifier = relationship("User", foreign_keys=[verified_by])

class ClubTransaction(Base):
    __tablename__ = "club_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    transaction_type = Column(Enum(TransactionType), nullable=False)
    category = Column(String(100), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    transaction_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    recorder = relationship("User")
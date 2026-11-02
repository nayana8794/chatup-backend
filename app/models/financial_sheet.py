# chatup/backend/app/models/financial_sheet.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum, Text, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base


class SheetType(str, enum.Enum):
    BUDGET = "BUDGET"
    ANNUAL_ACCOUNTS = "ANNUAL_ACCOUNTS"
    PROJECT_LEDGER = "PROJECT_LEDGER"
    GENERAL_SPREADSHEET = "GENERAL_SPREADSHEET"


class SheetStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PUBLISHED = "PUBLISHED"
    VOIDED = "VOIDED"


class FinancialSheet(Base):
    __tablename__ = "financial_sheets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    creator_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    fiscal_year = Column(String(50), default="2026/2027", nullable=False)
    sheet_type = Column(Enum(SheetType), default=SheetType.BUDGET, nullable=False)
    status = Column(Enum(SheetStatus), default=SheetStatus.DRAFT, nullable=False, index=True)

    # Interactive Excel Grid data structure (columns, rows, cell values, formulas)
    grid_data = Column(JSONB, nullable=False)

    total_income = Column(Float, default=0.0, nullable=False)
    total_expense = Column(Float, default=0.0, nullable=False)
    net_balance = Column(Float, default=0.0, nullable=False)

    # Tamper-Proof Audit Soft-Delete Fields
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deletion_reason = Column(Text, nullable=True)

    submitted_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tenant = relationship("Tenant", backref="financial_sheets")
    creator = relationship("User", foreign_keys=[creator_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    deleted_by = relationship("User", foreign_keys=[deleted_by_id])
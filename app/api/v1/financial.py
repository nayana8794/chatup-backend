# chatup/backend/app/api/v1/financial.py
import uuid
import random
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import aliased
from sqlalchemy.orm.attributes import flag_modified
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.tenant import Tenant
from app.models.financial import DuesInvoice, ClubTransaction, DuesStatus, TransactionType, BillingCycle
from app.models.user import User, UserRole
from app.schemas.financial import (
    BatchGenerateDuesRequest,
    ReportPaymentRequest,
    ConfirmPaymentRequest,
    DuesInvoiceResponse,
    FinancialStatsResponse,
    BankDetailsUpdate,
    TransactionCreateRequest,
    TransactionResponse,
)

router = APIRouter(prefix="/financial", tags=["Financials & Dues"])

TREASURY_MANAGERS = [
    UserRole.SUPER_ADMIN,
    UserRole.TREASURER,
    UserRole.VICE_TREASURER,
]

PRESIDENT_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
]

ALL_FINANCIAL_OFFICERS = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
    UserRole.TREASURER,
    UserRole.VICE_TREASURER,
]

class AmendBatchRequest(BaseModel):
    current_title: str
    new_title: Optional[str] = None
    amount: Optional[float] = None
    due_date: Optional[datetime] = None
    billing_cycle: Optional[BillingCycle] = None

class BatchSummaryResponse(BaseModel):
    title: str
    billing_cycle: BillingCycle
    amount: float
    due_date: datetime
    is_approved: bool
    created_at: datetime
    total_invoices: int
    paid_count: int
    total_expected: float
    total_collected: float

def generate_reference_number(user_id: uuid.UUID) -> str:
    seed = int(str(user_id.int)[:6])
    random_part = random.randint(100, 999)
    base_ref = f"{seed}{random_part}"
    
    multipliers = [7, 3, 1]
    total = 0
    for i, digit in enumerate(reversed(base_ref)):
        total += int(digit) * multipliers[i % 3]
    check_digit = (10 - (total % 10)) % 10
    return f"{base_ref}{check_digit}"

@router.get("/dues", response_model=List[DuesInvoiceResponse])
async def list_dues_invoices(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    VerifierUser = aliased(User)
    query = (
        select(DuesInvoice, User.full_name, User.role, User.email, VerifierUser.full_name.label("verifier_name"))
        .join(User, DuesInvoice.user_id == User.id)
        .outerjoin(VerifierUser, DuesInvoice.verified_by == VerifierUser.id)
        .where(DuesInvoice.tenant_id == current_user.tenant_id)
    )

    if current_user.role not in ALL_FINANCIAL_OFFICERS:
        query = query.where(DuesInvoice.user_id == current_user.id, DuesInvoice.is_approved == True)

    query = query.order_by(DuesInvoice.created_at.desc())
    result = await db.execute(query)

    response_list = []
    for invoice, name, role, email, verifier_name in result.all():
        response_list.append(
            DuesInvoiceResponse(
                id=invoice.id,
                tenant_id=invoice.tenant_id,
                user_id=invoice.user_id,
                member_name=name,
                member_role=role.value,
                member_email=email,
                title=invoice.title,
                billing_cycle=invoice.billing_cycle,
                amount=float(invoice.amount),
                currency=invoice.currency,
                due_date=invoice.due_date,
                status=invoice.status,
                is_approved=invoice.is_approved,
                reported_at=invoice.reported_at,
                reported_method=invoice.reported_method,
                paid_at=invoice.paid_at,
                payment_method=invoice.payment_method,
                verified_by=invoice.verified_by,
                verifier_name=verifier_name,
                reference_number=invoice.reference_number,
                notes=invoice.notes,
                created_at=invoice.created_at,
            )
        )
    return response_list

@router.get("/stats", response_model=FinancialStatsResponse)
async def get_financial_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    invoices = (
        await db.execute(
            select(DuesInvoice).where(DuesInvoice.tenant_id == current_user.tenant_id)
        )
    ).scalars().all()

    total_collected = sum(float(i.amount) for i in invoices if i.status == DuesStatus.PAID)
    total_pending = sum(float(i.amount) for i in invoices if i.status in [DuesStatus.PENDING, DuesStatus.OVERDUE] and i.is_approved)
    total_awaiting = sum(float(i.amount) for i in invoices if i.status == DuesStatus.AWAITING_VERIFICATION)
    
    paid_count = sum(1 for i in invoices if i.status == DuesStatus.PAID)
    pending_count = sum(1 for i in invoices if i.status in [DuesStatus.PENDING, DuesStatus.OVERDUE] and i.is_approved)
    awaiting_count = sum(1 for i in invoices if i.status == DuesStatus.AWAITING_VERIFICATION)
    pending_approval_count = sum(1 for i in invoices if not i.is_approved)

    tenant = (await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))).scalar_one_or_none()
    bank = tenant.bank_details if tenant and tenant.bank_details else {}

    return FinancialStatsResponse(
        total_collected=total_collected,
        total_pending=total_pending,
        total_awaiting_verification=total_awaiting,
        paid_members_count=paid_count,
        pending_members_count=pending_count,
        awaiting_verification_count=awaiting_count,
        pending_approval_count=pending_approval_count,
        recipient_name=bank.get("recipient_name", "Lions Club Helsinki Ry"),
        club_iban=bank.get("iban", "FI42 1234 5678 9012 34"),
        club_bic=bank.get("bic", "DABAFIHH"),
        mobilepay_number=bank.get("mobilepay_number", "12345"),
    )

@router.put("/bank-details")
async def update_club_bank_details(
    req: BankDetailsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in TREASURY_MANAGERS:
        raise HTTPException(status_code=403, detail="Permission denied: Only Treasurer can edit bank details")

    tenant = (await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Club not found")

    tenant.bank_details = {
        "recipient_name": req.recipient_name,
        "iban": req.iban,
        "bic": req.bic,
        "mobilepay_number": req.mobilepay_number or "",
    }
    flag_modified(tenant, "bank_details")
    await db.commit()

    return {"status": "success", "message": "Bank details updated successfully"}

# Treasurer issues batch -> Live Real-Time broadcast with sound to President
@router.post("/dues/batch")
async def batch_generate_dues(
    req: BatchGenerateDuesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in TREASURY_MANAGERS:
        raise HTTPException(status_code=403, detail="Only Treasurer can generate dues invoices")

    members = (
        await db.execute(
            select(User).where(
                User.tenant_id == current_user.tenant_id,
                User.is_active == True,
            )
        )
    ).scalars().all()

    due_date_clean = req.due_date.replace(tzinfo=None) if req.due_date.tzinfo else req.due_date

    count = 0
    for member in members:
        ref_no = generate_reference_number(member.id)
        invoice = DuesInvoice(
            tenant_id=current_user.tenant_id,
            user_id=member.id,
            title=req.title,
            billing_cycle=req.billing_cycle,
            amount=req.amount,
            currency="EUR",
            due_date=due_date_clean,
            status=DuesStatus.PENDING,
            is_approved=False,
            reference_number=ref_no,
        )
        db.add(invoice)
        count += 1

    await db.commit()

    # Broadcast event with sender_id to trigger live chime on President's screen
    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "DUES_APPROVAL_NEEDED",
                "title": req.title,
                "amount": req.amount,
                "billing_cycle": req.billing_cycle.value,
                "sender_id": str(current_user.id),
            },
        )

    return {"status": "success", "invoices_created": count, "is_approved": False}

@router.put("/dues/batch")
async def amend_dues_batch(
    req: AmendBatchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in TREASURY_MANAGERS:
        raise HTTPException(status_code=403, detail="Only Treasurer can amend dues batches")

    invoices = (
        await db.execute(
            select(DuesInvoice).where(
                DuesInvoice.tenant_id == current_user.tenant_id,
                DuesInvoice.title == req.current_title,
                DuesInvoice.is_approved == False,
            )
        )
    ).scalars().all()

    if not invoices:
        raise HTTPException(status_code=404, detail="No unapproved batch found with this title")

    due_date_clean = req.due_date.replace(tzinfo=None) if req.due_date and req.due_date.tzinfo else req.due_date

    for inv in invoices:
        if req.new_title:
            inv.title = req.new_title
        if req.amount is not None:
            inv.amount = req.amount
        if due_date_clean:
            inv.due_date = due_date_clean
        if req.billing_cycle:
            inv.billing_cycle = req.billing_cycle

    await db.commit()

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "DUES_BATCH_AMENDED",
                "title": req.new_title or req.current_title,
                "sender_id": str(current_user.id),
            },
        )

    return {"status": "success", "amended_count": len(invoices)}

@router.delete("/dues/batch")
async def cancel_dues_batch(
    title: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in TREASURY_MANAGERS:
        raise HTTPException(status_code=403, detail="Only Treasurer can cancel dues batches")

    invoices = (
        await db.execute(
            select(DuesInvoice).where(
                DuesInvoice.tenant_id == current_user.tenant_id,
                DuesInvoice.title == title,
                DuesInvoice.is_approved == False,
            )
        )
    ).scalars().all()

    if not invoices:
        raise HTTPException(status_code=404, detail="No unapproved batch found to cancel")

    for inv in invoices:
        await db.delete(inv)

    await db.commit()

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "DUES_BATCH_CANCELLED",
                "title": title,
                "sender_id": str(current_user.id),
            },
        )

    return {"status": "success", "deleted_count": len(invoices)}

# President approves batch -> Live Real-Time broadcast with sound to Treasurer & Members
@router.post("/dues/batch/approve")
async def approve_dues_batch(
    title: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in PRESIDENT_ROLES:
        raise HTTPException(status_code=403, detail="Only President or Vice President can approve dues batches")

    query = select(DuesInvoice).where(
        DuesInvoice.title == title,
        DuesInvoice.is_approved == False,
    )
    if current_user.role != UserRole.SUPER_ADMIN:
        query = query.where(DuesInvoice.tenant_id == current_user.tenant_id)

    invoices = (await db.execute(query)).scalars().all()

    if not invoices:
        raise HTTPException(status_code=404, detail="No pending dues batch found with this title")

    tenant_id_to_broadcast = invoices[0].tenant_id

    for inv in invoices:
        inv.is_approved = True

    await db.commit()

    # Live broadcast to Treasurer and Members with sound chime
    await manager.broadcast_to_tenant(
        str(tenant_id_to_broadcast).lower(),
        {
            "type": "DUES_BATCH_APPROVED",
            "title": title,
            "amount": float(invoices[0].amount),
            "billing_cycle": invoices[0].billing_cycle.value,
            "sender_id": str(current_user.id),
            "approved_by": current_user.full_name,
        },
    )

    return {"status": "success", "approved_count": len(invoices)}

@router.get("/batches/history", response_model=List[BatchSummaryResponse])
async def list_batches_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in ALL_FINANCIAL_OFFICERS:
        raise HTTPException(status_code=403, detail="Access denied")

    invoices = (
        await db.execute(
            select(DuesInvoice)
            .where(DuesInvoice.tenant_id == current_user.tenant_id)
            .order_by(DuesInvoice.created_at.desc())
        )
    ).scalars().all()

    grouped = {}
    for inv in invoices:
        if inv.title not in grouped:
            grouped[inv.title] = []
        grouped[inv.title].append(inv)

    summaries = []
    for title, inv_list in grouped.items():
        sample = inv_list[0]
        total_inv = len(inv_list)
        paid_inv = sum(1 for i in inv_list if i.status == DuesStatus.PAID)
        total_expected = sum(float(i.amount) for i in inv_list)
        total_collected = sum(float(i.amount) for i in inv_list if i.status == DuesStatus.PAID)

        summaries.append(
            BatchSummaryResponse(
                title=title,
                billing_cycle=sample.billing_cycle,
                amount=float(sample.amount),
                due_date=sample.due_date,
                is_approved=sample.is_approved,
                created_at=sample.created_at,
                total_invoices=total_inv,
                paid_count=paid_inv,
                total_expected=total_expected,
                total_collected=total_collected,
            )
        )

    return summaries

@router.post("/dues/{invoice_id}/report-payment")
async def report_payment(
    invoice_id: uuid.UUID,
    req: ReportPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    invoice = (
        await db.execute(
            select(DuesInvoice).where(DuesInvoice.id == invoice_id, DuesInvoice.user_id == current_user.id)
        )
    ).scalar_one_or_none()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice.status = DuesStatus.AWAITING_VERIFICATION
    invoice.reported_at = datetime.utcnow()
    invoice.reported_method = req.payment_method
    invoice.transaction_reference = req.transaction_reference
    if req.notes:
        invoice.notes = req.notes

    await db.commit()

    if invoice.tenant_id:
        await manager.broadcast_to_tenant(
            str(invoice.tenant_id).lower(),
            {
                "type": "PAYMENT_REPORTED",
                "invoice_id": str(invoice.id),
                "member_name": current_user.full_name,
                "amount": float(invoice.amount),
                "sender_id": str(current_user.id),
            },
        )

    return {"status": "success", "message": "Payment reported. Awaiting Treasurer confirmation."}

@router.patch("/dues/{invoice_id}/confirm")
async def confirm_payment_received(
    invoice_id: uuid.UUID,
    req: ConfirmPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.TREASURER, UserRole.VICE_TREASURER]
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Only Treasurer can confirm member payments")

    invoice = (
        await db.execute(select(DuesInvoice).where(DuesInvoice.id == invoice_id))
    ).scalar_one_or_none()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    now = datetime.utcnow()
    invoice.status = DuesStatus.PAID
    invoice.paid_at = now
    invoice.payment_method = req.payment_method or invoice.reported_method or "SEPA Bank Transfer"
    invoice.verified_by = current_user.id
    if req.notes:
        invoice.notes = f"{invoice.notes or ''} | Verified: {req.notes}".strip()

    ledger_entry = ClubTransaction(
        tenant_id=invoice.tenant_id,
        recorded_by=current_user.id,
        title=f"Dues: {invoice.title}",
        transaction_type=TransactionType.INCOME,
        category="DUES",
        amount=invoice.amount,
        notes=f"Ref: {invoice.reference_number} | Verified by {current_user.full_name}",
    )
    db.add(ledger_entry)

    await db.commit()

    if invoice.tenant_id:
        await manager.broadcast_to_tenant(
            str(invoice.tenant_id).lower(),
            {
                "type": "DUES_PAID",
                "invoice_id": str(invoice.id),
                "user_id": str(invoice.user_id),
                "sender_id": str(current_user.id),
            },
        )

    return {"status": "success", "message": "Payment confirmed and verified successfully"}
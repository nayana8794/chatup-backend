# chatup/backend/app/api/v1/bookkeeping.py
import io
import uuid
import logging
from decimal import Decimal
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_
from sqlalchemy.orm import aliased
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.user import User, UserRole
from app.models.tenant import Tenant
from app.models.financial import ClubTransaction, DuesInvoice, TransactionType, DuesStatus
from app.models.financial_sheet import FinancialSheet, SheetStatus
from app.schemas.bookkeeping import (
    TransactionCreateRequest,
    TransactionResponse,
    BookkeepingSummaryResponse,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/bookkeeping", tags=["Financial Bookkeeping & Audit Package"])

TREASURY_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
    UserRole.TREASURER,
    UserRole.VICE_TREASURER,
]


def to_float(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (Decimal, int, float, str)):
        try:
            return float(val)
        except Exception:
            return 0.0
    return 0.0


@router.get("/summary", response_model=BookkeepingSummaryResponse)
async def get_bookkeeping_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User does not belong to a club")

    if current_user.role not in TREASURY_ROLES and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Permission denied to access financial summary")

    t_id = current_user.tenant_id

    try:
        raw_dues_collected = (
            await db.execute(
                select(func.coalesce(func.sum(DuesInvoice.amount), 0)).where(
                    DuesInvoice.tenant_id == t_id,
                    DuesInvoice.status == DuesStatus.PAID,
                    DuesInvoice.is_approved == True,
                )
            )
        ).scalar_one()

        raw_dues_expected = (
            await db.execute(
                select(func.coalesce(func.sum(DuesInvoice.amount), 0)).where(
                    DuesInvoice.tenant_id == t_id,
                    DuesInvoice.is_approved == True,
                )
            )
        ).scalar_one()

        dues_collected = to_float(raw_dues_collected)
        dues_expected = to_float(raw_dues_expected)

        raw_other_income = (
            await db.execute(
                select(func.coalesce(func.sum(ClubTransaction.amount), 0)).where(
                    ClubTransaction.tenant_id == t_id,
                    ClubTransaction.transaction_type == TransactionType.INCOME,
                    ClubTransaction.category != "DUES",
                )
            )
        ).scalar_one()

        raw_total_expenses = (
            await db.execute(
                select(func.coalesce(func.sum(ClubTransaction.amount), 0)).where(
                    ClubTransaction.tenant_id == t_id,
                    ClubTransaction.transaction_type == TransactionType.EXPENSE,
                )
            )
        ).scalar_one()

        other_income = to_float(raw_other_income)
        total_expenses = to_float(raw_total_expenses)

        published_sheets = (
            await db.execute(
                select(FinancialSheet).where(
                    FinancialSheet.tenant_id == t_id,
                    FinancialSheet.status == SheetStatus.PUBLISHED,
                    FinancialSheet.is_deleted == False,
                )
            )
        ).scalars().all()

        sheet_income = sum(float(s.total_income) for s in published_sheets)
        sheet_exp = sum(float(s.total_expense) for s in published_sheets)

        final_total_inflows = dues_collected + other_income + sheet_income
        final_total_expenses = total_expenses + sheet_exp
        net_balance = final_total_inflows - final_total_expenses
        collection_rate = round((dues_collected / dues_expected * 100), 1) if dues_expected > 0 else 100.0

        cat_query = (
            select(
                ClubTransaction.category,
                func.coalesce(func.sum(ClubTransaction.amount), 0).label("total_amount"),
                func.count(ClubTransaction.id).label("count"),
            )
            .where(
                ClubTransaction.tenant_id == t_id,
                ClubTransaction.transaction_type == TransactionType.EXPENSE,
            )
            .group_by(ClubTransaction.category)
            .order_by(desc("total_amount"))
        )
        cat_results = (await db.execute(cat_query)).all()
        categories_list = [
            {"category": str(cat), "amount": to_float(amt), "count": int(count)}
            for cat, amt, count in cat_results
        ]

        tx_count = (
            await db.execute(
                select(func.count(ClubTransaction.id)).where(ClubTransaction.tenant_id == t_id)
            )
        ).scalar_one()

        return BookkeepingSummaryResponse(
            total_dues_income=dues_collected,
            total_other_income=other_income,
            total_expenses=final_total_expenses,
            net_treasury_balance=net_balance,
            dues_collection_rate=collection_rate,
            total_transactions_count=int(tx_count or 0),
            category_breakdown=categories_list,
        )
    except Exception as e:
        logger.error("Bookkeeping summary calculation error: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database ledger error: {str(e)}")


@router.get("/transactions", response_model=List[TransactionResponse])
async def list_transactions(
    tx_type: Optional[TransactionType] = Query(None),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        return []

    if current_user.role not in TREASURY_ROLES and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Permission denied to access general ledger")

    try:
        query = (
            select(ClubTransaction, User.full_name)
            .outerjoin(User, ClubTransaction.recorded_by == User.id)
            .where(ClubTransaction.tenant_id == current_user.tenant_id)
        )

        if tx_type:
            query = query.where(ClubTransaction.transaction_type == tx_type)

        if category and category != "ALL":
            query = query.where(ClubTransaction.category == category)

        if search:
            s = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    ClubTransaction.title.ilike(s),
                    ClubTransaction.category.ilike(s),
                    ClubTransaction.notes.ilike(s),
                )
            )

        query = query.order_by(desc(ClubTransaction.created_at))
        rows = (await db.execute(query)).all()

        return [
            TransactionResponse(
                id=tx.id,
                tenant_id=tx.tenant_id,
                transaction_type=tx.transaction_type,
                category=tx.category,
                description=tx.title,
                amount=to_float(tx.amount),
                currency="EUR",
                reference_number=tx.notes,
                transaction_date=tx.created_at,
                recorded_by=tx.recorded_by,
                recorder_name=recorder_name or "Club Treasurer",
                created_at=tx.created_at,
            )
            for tx, recorder_name in rows
        ]
    except Exception as e:
        logger.error("Error listing transactions: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transaction fetch error: {str(e)}")


@router.post("/transactions", response_model=TransactionResponse)
async def record_transaction(
    req: TransactionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.TREASURER, UserRole.VICE_TREASURER, UserRole.PRESIDENT]:
        raise HTTPException(status_code=403, detail="Only Treasurer or President can record ledger entries")

    try:
        now = datetime.utcnow().replace(tzinfo=None)
        tx_date = req.transaction_date.replace(tzinfo=None) if req.transaction_date else now

        tx = ClubTransaction(
            tenant_id=current_user.tenant_id,
            recorded_by=current_user.id,
            title=req.description.strip(),
            transaction_type=req.transaction_type,
            category=req.category.strip(),
            amount=req.amount,
            notes=req.reference_number.strip() if req.reference_number else None,
            created_at=tx_date,
        )
        db.add(tx)
        await db.commit()
        await db.refresh(tx)

        if current_user.tenant_id:
            await manager.broadcast_to_tenant(
                str(current_user.tenant_id).lower(),
                {
                    "type": "FINANCIAL_SHEET_SUBMITTED",
                    "title": tx.title,
                    "sender_id": str(current_user.id),
                },
            )

        return TransactionResponse(
            id=tx.id,
            tenant_id=tx.tenant_id,
            transaction_type=tx.transaction_type,
            category=tx.category,
            description=tx.title,
            amount=to_float(tx.amount),
            currency="EUR",
            reference_number=tx.notes,
            transaction_date=tx.created_at,
            recorded_by=current_user.id,
            recorder_name=current_user.full_name,
            created_at=tx.created_at,
        )
    except Exception as e:
        logger.error("Error creating transaction: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to record transaction: {str(e)}")


@router.delete("/transactions/{transaction_id}")
async def delete_transaction(
    transaction_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.TREASURER, UserRole.PRESIDENT]:
        raise HTTPException(status_code=403, detail="Only Treasurer or President can void ledger entries")

    tx = (
        await db.execute(
            select(ClubTransaction).where(
                ClubTransaction.id == transaction_id,
                ClubTransaction.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if not tx:
        raise HTTPException(status_code=404, detail="Transaction entry not found")

    await db.delete(tx)
    await db.commit()
    return {"status": "success", "message": "Transaction voided and deleted"}


@router.get("/export/excel")
async def export_excel_audit_package(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User does not belong to a club")

    t_id = current_user.tenant_id
    tenant = (await db.execute(select(Tenant).where(Tenant.id == t_id))).scalar_one_or_none()
    club_name = tenant.name if tenant else "Community Club"

    wb = Workbook()
    header_fill = PatternFill(start_color="002F6C", end_color="002F6C", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    gold_fill = PatternFill(start_color="F2A900", end_color="F2A900", fill_type="solid")
    gold_font = Font(name="Calibri", size=12, bold=True, color="002F6C")
    title_font = Font(name="Calibri", size=16, bold=True, color="002F6C")
    regular_font = Font(name="Calibri", size=11)
    bold_font = Font(name="Calibri", size=11, bold=True)
    thin_border = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )

    ws1 = wb.active
    ws1.title = "Executive Summary"
    ws1.views.sheetView[0].showGridLines = True

    ws1["A1"] = f"{club_name} — Master Financial Audit & Treasury Report"
    ws1["A1"].font = title_font
    ws1["A2"] = f"Certified Audit Package Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} by {current_user.full_name}"
    ws1["A2"].font = Font(name="Calibri", size=10, italic=True, color="555555")

    raw_dues = (
        await db.execute(
            select(func.coalesce(func.sum(DuesInvoice.amount), 0)).where(
                DuesInvoice.tenant_id == t_id,
                DuesInvoice.status == DuesStatus.PAID,
                DuesInvoice.is_approved == True,
            )
        )
    ).scalar_one()

    raw_income = (
        await db.execute(
            select(func.coalesce(func.sum(ClubTransaction.amount), 0)).where(
                ClubTransaction.tenant_id == t_id,
                ClubTransaction.transaction_type == TransactionType.INCOME,
                ClubTransaction.category != "DUES",
            )
        )
    ).scalar_one()

    raw_exp = (
        await db.execute(
            select(func.coalesce(func.sum(ClubTransaction.amount), 0)).where(
                ClubTransaction.tenant_id == t_id,
                ClubTransaction.transaction_type == TransactionType.EXPENSE,
            )
        )
    ).scalar_one()

    published_sheets = (
        await db.execute(
            select(FinancialSheet).where(
                FinancialSheet.tenant_id == t_id,
                FinancialSheet.status == SheetStatus.PUBLISHED,
                FinancialSheet.is_deleted == False,
            )
        )
    ).scalars().all()

    sheet_income = sum(float(s.total_income) for s in published_sheets)
    sheet_exp = sum(float(s.total_expense) for s in published_sheets)

    dues_collected = to_float(raw_dues)
    other_income = to_float(raw_income) + sheet_income
    total_expenses = to_float(raw_exp) + sheet_exp
    net_balance = (dues_collected + other_income) - total_expenses

    metrics = [
        ("Official Club Workspace", club_name),
        ("District Jurisdiction", tenant.district if tenant and tenant.district else "Nordic & European District"),
        ("Reporting Currency", "EUR (€)"),
        ("Membership Dues Revenue", f"€{dues_collected:,.2f}"),
        ("Charity & Ledger Inflows", f"€{other_income:,.2f}"),
        ("Total Club Inflow", f"€{(dues_collected + other_income):,.2f}"),
        ("Total Disbursed Expenses", f"€{total_expenses:,.2f}"),
        ("Current Net Treasury Reserve", f"€{net_balance:,.2f}"),
    ]

    ws1["A4"] = "Master Audit Key Performance Indicators"
    ws1["A4"].font = gold_font
    ws1["A4"].fill = gold_fill

    row_idx = 5
    for label, val in metrics:
        ws1[f"A{row_idx}"] = label
        ws1[f"B{row_idx}"] = val
        ws1[f"A{row_idx}"].font = bold_font
        ws1[f"B{row_idx}"].font = regular_font
        ws1[f"A{row_idx}"].border = thin_border
        ws1[f"B{row_idx}"].border = thin_border
        row_idx += 1

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    safe_name = club_name.replace(" ", "_")
    filename = f"Master_Financial_Audit_Package_{safe_name}_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"

    return Response(
        content=stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
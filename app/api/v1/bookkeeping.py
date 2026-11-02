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
        # Regular members can view published summary if permitted, or restricted
        pass

    t_id = current_user.tenant_id

    try:
        # 1. Dues Collected
        raw_dues = (
            await db.execute(
                select(func.coalesce(func.sum(DuesInvoice.amount), 0)).where(
                    DuesInvoice.tenant_id == t_id,
                    DuesInvoice.status == DuesStatus.PAID,
                    DuesInvoice.is_approved == True,
                )
            )
        ).scalar_one()

        raw_expected = (
            await db.execute(
                select(func.coalesce(func.sum(DuesInvoice.amount), 0)).where(
                    DuesInvoice.tenant_id == t_id,
                    DuesInvoice.is_approved == True,
                )
            )
        ).scalar_one()

        dues_collected = to_float(raw_dues)
        dues_expected = to_float(raw_expected)

        # 2. General Ledger Transactions
        raw_other_income = (
            await db.execute(
                select(func.coalesce(func.sum(ClubTransaction.amount), 0)).where(
                    ClubTransaction.tenant_id == t_id,
                    ClubTransaction.transaction_type == TransactionType.INCOME,
                    ClubTransaction.category != "DUES",
                )
            )
        ).scalar_one()

        raw_expenses = (
            await db.execute(
                select(func.coalesce(func.sum(ClubTransaction.amount), 0)).where(
                    ClubTransaction.tenant_id == t_id,
                    ClubTransaction.transaction_type == TransactionType.EXPENSE,
                )
            )
        ).scalar_one()

        other_income = to_float(raw_other_income)
        total_expenses = to_float(raw_expenses)

        # 3. Add Published Financial Sheets (Budgets & Accounts) into Unified Summary
        published_sheets = (
            await db.execute(
                select(FinancialSheet).where(
                    FinancialSheet.tenant_id == t_id,
                    FinancialSheet.status == SheetStatus.PUBLISHED,
                    FinancialSheet.is_deleted == False,
                )
            )
        ).scalars().all()

        sheet_income_extra = sum(float(s.total_income) for s in published_sheets)
        sheet_expense_extra = sum(float(s.total_expense) for s in published_sheets)

        final_total_inflows = dues_collected + other_income + sheet_income_extra
        final_total_expenses = total_expenses + sheet_expense_extra
        net_balance = final_total_inflows - final_total_expenses
        collection_rate = round((dues_collected / dues_expected * 100), 1) if dues_expected > 0 else 100.0

        # Category Breakdown
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
            total_other_income=other_income + sheet_income_extra,
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


@router.post("/transactions", response_model=TransactionResponse)
async def record_transaction(
    req: TransactionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.TREASURER, UserRole.VICE_TREASURER, UserRole.PRESIDENT]:
        raise HTTPException(status_code=403, detail="Only Treasurer or President can record ledger entries")

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


# ================= COMBINED MASTER EXCEL (.XLSX) AUDIT PACKAGE EXPORTER =================
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

    # ----------------------------------------------------
    # SHEET 1: Executive Summary
    # ----------------------------------------------------
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

    # ----------------------------------------------------
    # SHEET 2: Membership Dues Ledger
    # ----------------------------------------------------
    ws2 = wb.create_sheet(title="Membership Dues Ledger")
    ws2.views.sheetView[0].showGridLines = True

    VerifierUser = aliased(User)
    dues_query = (
        select(
            DuesInvoice,
            User.full_name.label("member_name"),
            User.role.label("member_role"),
            VerifierUser.full_name.label("verifier_name"),
        )
        .join(User, DuesInvoice.user_id == User.id)
        .outerjoin(VerifierUser, DuesInvoice.verified_by == VerifierUser.id)
        .where(DuesInvoice.tenant_id == t_id, DuesInvoice.is_approved == True)
        .order_by(User.full_name.asc(), DuesInvoice.created_at.desc())
    )
    invoice_rows = (await db.execute(dues_query)).all()

    dues_headers = [
        "Member Full Name", "Role", "Billing Title", "Cycle",
        "Amount (€)", "Due Date", "Status", "Viitenumero (Ref No)",
        "Paid Date", "Payment Channel", "Verified By"
    ]
    ws2.append(dues_headers)

    for col_idx in range(1, len(dues_headers) + 1):
        cell = ws2.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for inv, member_name, member_role, verifier_name in invoice_rows:
        paid_date = inv.paid_at.strftime("%Y-%m-%d") if inv.paid_at else "-"
        cycle_str = inv.billing_cycle.value if hasattr(inv.billing_cycle, 'value') else str(inv.billing_cycle)
        status_str = inv.status.value if hasattr(inv.status, 'value') else str(inv.status)
        role_str = member_role.value if hasattr(member_role, 'value') else str(member_role or "MEMBER")

        ws2.append([
            member_name or "Club Member",
            role_str,
            inv.title,
            cycle_str,
            to_float(inv.amount),
            inv.due_date.strftime("%Y-%m-%d") if inv.due_date else "-",
            status_str,
            inv.reference_number or "-",
            paid_date,
            inv.payment_method or "-",
            verifier_name or "-",
        ])

    # ----------------------------------------------------
    # SHEET 3: Income & Expense Journal
    # ----------------------------------------------------
    ws3 = wb.create_sheet(title="Income & Expense Journal")
    ws3.views.sheetView[0].showGridLines = True

    txs = (
        await db.execute(
            select(ClubTransaction)
            .where(ClubTransaction.tenant_id == t_id)
            .order_by(ClubTransaction.created_at.asc())
        )
    ).scalars().all()

    journal_headers = ["Date", "Entry Type", "Category", "Description", "Reference No", "Amount (€)"]
    ws3.append(journal_headers)

    for col_idx in range(1, len(journal_headers) + 1):
        cell = ws3.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for tx in txs:
        val = to_float(tx.amount)
        type_str = tx.transaction_type.value if hasattr(tx.transaction_type, 'value') else str(tx.transaction_type)
        ws3.append([
            tx.created_at.strftime("%Y-%m-%d") if tx.created_at else "-",
            type_str,
            tx.category or "General",
            tx.title or "-",
            tx.notes or "-",
            val if tx.transaction_type == TransactionType.INCOME else -val,
        ])

    # ----------------------------------------------------
    # SHEETS 4+: Published Financial Sheets (Budgets & Accounts)
    # ----------------------------------------------------
    for sheet in published_sheets:
        safe_title = sheet.title[:25].replace("/", "-")
        ws_sheet = wb.create_sheet(title=safe_title)
        ws_sheet.views.sheetView[0].showGridLines = True

        ws_sheet["A1"] = f"{sheet.title} ({sheet.fiscal_year})"
        ws_sheet["A1"].font = title_font
        ws_sheet["A2"] = f"Type: {sheet.sheet_type.value} | Status: PUBLISHED"
        ws_sheet["A2"].font = Font(name="Calibri", size=10, italic=True)

        grid = sheet.grid_data or {}
        rows = grid.get("rows", [])

        sheet_headers = ["Category / Cost Center", "Description", "Type", "Amount (€)"]
        ws_sheet.append([]) # row 3 blank
        ws_sheet.append(sheet_headers) # row 4

        for c_idx in range(1, len(sheet_headers) + 1):
            cell = ws_sheet.cell(row=4, column=c_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        r_idx = 5
        for r in rows:
            ws_sheet.cell(row=r_idx, column=1, value=str(r.get("category", ""))).border = thin_border
            ws_sheet.cell(row=r_idx, column=2, value=str(r.get("description", ""))).border = thin_border
            ws_sheet.cell(row=r_idx, column=3, value=str(r.get("type", "")).upper()).border = thin_border
            amt_c = ws_sheet.cell(row=r_idx, column=4, value=float(r.get("amount", 0.0)))
            amt_c.number_format = "€#,##0.00"
            amt_c.border = thin_border
            r_idx += 1

        ws_sheet.cell(row=r_idx, column=1, value="NET BALANCE").font = bold_font
        net_c = ws_sheet.cell(row=r_idx, column=4, value=sheet.net_balance)
        net_c.font = bold_font
        net_c.number_format = "€#,##0.00"

    # ----------------------------------------------------
    # FINAL SHEET: Auditor Compliance & Sign-off Certificate
    # ----------------------------------------------------
    ws_cert = wb.create_sheet(title="Sign-off Certificate")
    ws_cert.views.sheetView[0].showGridLines = True

    ws_cert["A1"] = f"{club_name} — Statutory Audit & Executive Sign-off"
    ws_cert["A1"].font = title_font
    ws_cert["A3"] = "Finnish Non-Profit Association Act Compliance (Yhdistyslaki / Toiminnantarkastuskertomus)"
    ws_cert["A3"].font = bold_font

    ws_cert["A5"] = f"We have examined the books, ledgers, and financial spreadsheets of {club_name}."
    ws_cert["A6"] = "The financial statements present a true and fair view of the association's fiscal position."

    ws_cert["A9"] = "Prepared & Verified by Treasurer:"
    ws_cert["A10"] = "Name: _____________________________"
    ws_cert["A11"] = "Signature: __________________________"
    ws_cert["A12"] = f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}"

    ws_cert["D9"] = "Approved & Certified by President:"
    ws_cert["D10"] = "Name: _____________________________"
    ws_cert["D11"] = "Signature: __________________________"
    ws_cert["D12"] = f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}"

    for s_item in wb.worksheets:
        for col in s_item.columns:
            max_len = 0
            for cell in col:
                val_str = str(cell.value or "")
                if "\n" in val_str:
                    val_str = max(val_str.split("\n"), key=len)
                if len(val_str) > max_len:
                    max_len = len(val_str)
            col_letter = get_column_letter(col[0].column)
            s_item.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

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
# chatup/backend/app/api/v1/financial_sheets.py
import io
import uuid
import logging
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import aliased
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.ws_manager import manager
from app.models.user import User, UserRole
from app.models.financial_sheet import FinancialSheet, SheetType, SheetStatus
from app.schemas.financial_sheet import (
    SheetCreateRequest,
    SheetUpdateRequest,
    SheetVoidRequest,
    SheetResponse,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/financial-sheets", tags=["Excel Spreadsheets & Ledger Governance"])

OFFICER_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.PRESIDENT,
    UserRole.VICE_PRESIDENT,
    UserRole.TREASURER,
    UserRole.VICE_TREASURER,
]


def calculate_grid_totals(grid_data: dict):
    rows = grid_data.get("rows", [])
    total_income = 0.0
    total_expense = 0.0

    for r in rows:
        r_type = str(r.get("type", "")).upper()
        try:
            amt = float(r.get("amount", 0.0))
        except (ValueError, TypeError):
            amt = 0.0

        if r_type == "INCOME":
            total_income += amt
        elif r_type == "EXPENSE":
            total_expense += amt

    return total_income, total_expense, (total_income - total_expense)


@router.get("", response_model=List[SheetResponse])
async def list_financial_sheets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.tenant_id:
        return []

    is_officer = current_user.role in OFFICER_ROLES

    ApproverUser = aliased(User)
    DeleterUser = aliased(User)

    query = (
        select(
            FinancialSheet,
            User.full_name.label("creator_name"),
            ApproverUser.full_name.label("approver_name"),
            DeleterUser.full_name.label("deleted_by_name"),
        )
        .outerjoin(User, FinancialSheet.creator_id == User.id)
        .outerjoin(ApproverUser, FinancialSheet.approved_by_id == ApproverUser.id)
        .outerjoin(DeleterUser, FinancialSheet.deleted_by_id == DeleterUser.id)
        .where(FinancialSheet.tenant_id == current_user.tenant_id)
    )

    # Regular members only see approved/published sheets that are not voided
    if not is_officer:
        query = query.where(
            FinancialSheet.status == SheetStatus.PUBLISHED,
            FinancialSheet.is_deleted == False,
        )

    query = query.order_by(desc(FinancialSheet.created_at))
    rows = (await db.execute(query)).all()

    return [
        SheetResponse(
            id=sheet.id,
            tenant_id=sheet.tenant_id,
            creator_id=sheet.creator_id,
            creator_name=c_name or "Club Treasurer",
            approved_by_id=sheet.approved_by_id,
            approver_name=a_name,
            title=sheet.title,
            fiscal_year=sheet.fiscal_year,
            sheet_type=sheet.sheet_type,
            status=sheet.status,
            grid_data=sheet.grid_data,
            total_income=sheet.total_income,
            total_expense=sheet.total_expense,
            net_balance=sheet.net_balance,
            is_deleted=sheet.is_deleted,
            deleted_at=sheet.deleted_at,
            deleted_by_name=d_name,
            deletion_reason=sheet.deletion_reason,
            submitted_at=sheet.submitted_at,
            approved_at=sheet.approved_at,
            created_at=sheet.created_at,
            updated_at=sheet.updated_at,
        )
        for sheet, c_name, a_name, d_name in rows
    ]


@router.post("", response_model=SheetResponse)
async def create_financial_sheet(
    req: SheetCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.TREASURER, UserRole.VICE_TREASURER, UserRole.PRESIDENT]:
        raise HTTPException(status_code=403, detail="Permission denied to draft financial spreadsheets")

    now = datetime.utcnow().replace(tzinfo=None)
    tot_inc, tot_exp, net_bal = calculate_grid_totals(req.grid_data)

    sheet = FinancialSheet(
        tenant_id=current_user.tenant_id,
        creator_id=current_user.id,
        title=req.title.strip(),
        fiscal_year=req.fiscal_year.strip(),
        sheet_type=req.sheet_type,
        status=SheetStatus.DRAFT,
        grid_data=req.grid_data,
        total_income=tot_inc,
        total_expense=tot_exp,
        net_balance=net_bal,
        is_deleted=False,
        created_at=now,
        updated_at=now,
    )
    db.add(sheet)
    await db.commit()
    await db.refresh(sheet)

    return SheetResponse(
        id=sheet.id,
        tenant_id=sheet.tenant_id,
        creator_id=sheet.creator_id,
        creator_name=current_user.full_name,
        title=sheet.title,
        fiscal_year=sheet.fiscal_year,
        sheet_type=sheet.sheet_type,
        status=sheet.status,
        grid_data=sheet.grid_data,
        total_income=sheet.total_income,
        total_expense=sheet.total_expense,
        net_balance=sheet.net_balance,
        is_deleted=False,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


@router.put("/{sheet_id}", response_model=SheetResponse)
async def update_financial_sheet(
    sheet_id: uuid.UUID,
    req: SheetUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sheet = (
        await db.execute(
            select(FinancialSheet).where(
                FinancialSheet.id == sheet_id,
                FinancialSheet.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if not sheet:
        raise HTTPException(status_code=404, detail="Financial spreadsheet not found")

    # STRICT SECURITY RULE: Once submitted or published, Treasurer CANNOT edit!
    if sheet.status in [SheetStatus.SUBMITTED, SheetStatus.PUBLISHED, SheetStatus.VOIDED]:
        raise HTTPException(
            status_code=400,
            detail=f"This sheet is locked in '{sheet.status.value}' state. Approved records cannot be edited.",
        )

    if req.title:
        sheet.title = req.title.strip()
    if req.fiscal_year:
        sheet.fiscal_year = req.fiscal_year.strip()
    if req.sheet_type:
        sheet.sheet_type = req.sheet_type
    if req.grid_data is not None:
        sheet.grid_data = req.grid_data
        tot_inc, tot_exp, net_bal = calculate_grid_totals(req.grid_data)
        sheet.total_income = tot_inc
        sheet.total_expense = tot_exp
        sheet.net_balance = net_bal

    sheet.updated_at = datetime.utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(sheet)

    return SheetResponse(
        id=sheet.id,
        tenant_id=sheet.tenant_id,
        creator_id=sheet.creator_id,
        creator_name=current_user.full_name,
        title=sheet.title,
        fiscal_year=sheet.fiscal_year,
        sheet_type=sheet.sheet_type,
        status=sheet.status,
        grid_data=sheet.grid_data,
        total_income=sheet.total_income,
        total_expense=sheet.total_expense,
        net_balance=sheet.net_balance,
        is_deleted=sheet.is_deleted,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


# Treasurer submits sheet for President approval
@router.post("/{sheet_id}/submit", response_model=SheetResponse)
async def submit_sheet_for_approval(
    sheet_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.TREASURER, UserRole.VICE_TREASURER]:
        raise HTTPException(status_code=403, detail="Only Treasurer can submit financial sheets for approval")

    sheet = (
        await db.execute(
            select(FinancialSheet).where(
                FinancialSheet.id == sheet_id,
                FinancialSheet.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if not sheet:
        raise HTTPException(status_code=404, detail="Sheet not found")

    if sheet.status != SheetStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only draft sheets can be submitted")

    now = datetime.utcnow().replace(tzinfo=None)
    sheet.status = SheetStatus.SUBMITTED
    sheet.submitted_at = now
    sheet.updated_at = now
    await db.commit()
    await db.refresh(sheet)

    # Realtime notification to President with sound chime
    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "FINANCIAL_SHEET_SUBMITTED",
                "sheet_id": str(sheet.id),
                "title": sheet.title,
                "submitted_by": current_user.full_name,
            },
        )

    return SheetResponse(
        id=sheet.id,
        tenant_id=sheet.tenant_id,
        creator_id=sheet.creator_id,
        creator_name=current_user.full_name,
        title=sheet.title,
        fiscal_year=sheet.fiscal_year,
        sheet_type=sheet.sheet_type,
        status=sheet.status,
        grid_data=sheet.grid_data,
        total_income=sheet.total_income,
        total_expense=sheet.total_expense,
        net_balance=sheet.net_balance,
        is_deleted=False,
        submitted_at=sheet.submitted_at,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


# President approves and publishes sheet
@router.post("/{sheet_id}/approve", response_model=SheetResponse)
async def approve_financial_sheet(
    sheet_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.PRESIDENT, UserRole.VICE_PRESIDENT]:
        raise HTTPException(status_code=403, detail="Only President can approve financial ledgers")

    sheet = (
        await db.execute(
            select(FinancialSheet).where(
                FinancialSheet.id == sheet_id,
                FinancialSheet.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if not sheet:
        raise HTTPException(status_code=404, detail="Sheet not found")

    now = datetime.utcnow().replace(tzinfo=None)
    sheet.status = SheetStatus.PUBLISHED
    sheet.approved_by_id = current_user.id
    sheet.approved_at = now
    sheet.updated_at = now
    await db.commit()
    await db.refresh(sheet)

    # Broadcast to entire club with chime
    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "FINANCIAL_SHEET_APPROVED",
                "sheet_id": str(sheet.id),
                "title": sheet.title,
                "approved_by": current_user.full_name,
            },
        )

    return SheetResponse(
        id=sheet.id,
        tenant_id=sheet.tenant_id,
        creator_id=sheet.creator_id,
        approved_by_id=sheet.approved_by_id,
        approver_name=current_user.full_name,
        title=sheet.title,
        fiscal_year=sheet.fiscal_year,
        sheet_type=sheet.sheet_type,
        status=sheet.status,
        grid_data=sheet.grid_data,
        total_income=sheet.total_income,
        total_expense=sheet.total_expense,
        net_balance=sheet.net_balance,
        is_deleted=False,
        approved_at=sheet.approved_at,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


# President-only Soft Delete / Void with Mandatory Reason
@router.post("/{sheet_id}/void", response_model=SheetResponse)
async def void_financial_sheet(
    sheet_id: uuid.UUID,
    req: SheetVoidRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.PRESIDENT]:
        raise HTTPException(status_code=403, detail="Only President can void statutory financial sheets")

    sheet = (
        await db.execute(
            select(FinancialSheet).where(
                FinancialSheet.id == sheet_id,
                FinancialSheet.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if not sheet:
        raise HTTPException(status_code=404, detail="Sheet not found")

    now = datetime.utcnow().replace(tzinfo=None)
    sheet.is_deleted = True
    sheet.status = SheetStatus.VOIDED
    sheet.deleted_at = now
    sheet.deleted_by_id = current_user.id
    sheet.deletion_reason = req.deletion_reason.strip()
    sheet.updated_at = now

    await db.commit()
    await db.refresh(sheet)

    if current_user.tenant_id:
        await manager.broadcast_to_tenant(
            str(current_user.tenant_id).lower(),
            {
                "type": "FINANCIAL_SHEET_VOIDED",
                "sheet_id": str(sheet.id),
                "title": sheet.title,
                "voided_by": current_user.full_name,
            },
        )

    return SheetResponse(
        id=sheet.id,
        tenant_id=sheet.tenant_id,
        creator_id=sheet.creator_id,
        approved_by_id=sheet.approved_by_id,
        title=sheet.title,
        fiscal_year=sheet.fiscal_year,
        sheet_type=sheet.sheet_type,
        status=sheet.status,
        grid_data=sheet.grid_data,
        total_income=sheet.total_income,
        total_expense=sheet.total_expense,
        net_balance=sheet.net_balance,
        is_deleted=True,
        deleted_at=sheet.deleted_at,
        deleted_by_name=current_user.full_name,
        deletion_reason=sheet.deletion_reason,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


# 1-Click Direct Microsoft Excel (.xlsx) Download from Live Grid
@router.get("/{sheet_id}/export-excel")
async def export_single_sheet_excel(
    sheet_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sheet = (
        await db.execute(
            select(FinancialSheet).where(
                FinancialSheet.id == sheet_id,
                FinancialSheet.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if not sheet:
        raise HTTPException(status_code=404, detail="Sheet not found")

    wb = Workbook()
    ws = wb.active
    ws.title = "Spreadsheet"
    ws.views.sheetView[0].showGridLines = True

    header_fill = PatternFill(start_color="002F6C", end_color="002F6C", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=15, bold=True, color="002F6C")
    thin_border = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )

    ws["A1"] = f"{sheet.title} ({sheet.fiscal_year})"
    ws["A1"].font = title_font
    ws["A2"] = f"Type: {sheet.sheet_type.value} | Status: {sheet.status.value}"
    ws["A2"].font = Font(name="Calibri", size=10, italic=True)

    grid = sheet.grid_data or {}
    columns = grid.get("columns", ["Category", "Description", "Type", "Amount (€)"])
    rows = grid.get("rows", [])

    # Headers at row 4
    for c_idx, col_name in enumerate(columns, start=1):
        cell = ws.cell(row=4, column=c_idx, value=col_name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    curr_row = 5
    for r in rows:
        ws.cell(row=curr_row, column=1, value=str(r.get("category", ""))).border = thin_border
        ws.cell(row=curr_row, column=2, value=str(r.get("description", ""))).border = thin_border
        ws.cell(row=curr_row, column=3, value=str(r.get("type", "")).upper()).border = thin_border
        amt_cell = ws.cell(row=curr_row, column=4, value=float(r.get("amount", 0.0)))
        amt_cell.number_format = "€#,##0.00"
        amt_cell.border = thin_border
        curr_row += 1

    # Totals row
    ws.cell(row=curr_row, column=1, value="TOTAL NET BALANCE").font = Font(bold=True)
    tot_cell = ws.cell(row=curr_row, column=4, value=sheet.net_balance)
    tot_cell.font = Font(bold=True, color="002F6C")
    tot_cell.number_format = "€#,##0.00"

    for col in ws.columns:
        max_len = max(len(str(c.value or "")) for c in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 14), 45)

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    clean_filename = f"{sheet.title.replace(' ', '_')}_{sheet.fiscal_year.replace('/', '-')}.xlsx"
    return Response(
        content=stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{clean_filename}"'},
    )
import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES
from app.core.pagination import Page, PageParams, page_params, paginate_rows
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability
from app.modules.fees.setup_service import MAX_AMOUNT_KOBO
from app.modules.payments.cashbook import today_range

from . import service
from .models import Expense, ExpenseStatus, PaidFrom

router = APIRouter(tags=["expenses"], responses=ERROR_RESPONSES)
VIEW = Depends(require(Capability.PAYMENTS_VIEW))
RECORD = Depends(require(Capability.PAYMENTS_RECORD))
APPROVE = Depends(require(Capability.PAYMENTS_APPROVE))
REPORTS = Depends(require(Capability.REPORTS_VIEW))
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool


class CategoryIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    is_active: bool = True


class ExpenseIn(BaseModel):
    category_id: uuid.UUID
    amount_kobo: Annotated[int, Field(gt=0, le=MAX_AMOUNT_KOBO)]
    spent_on: date
    payee: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=150)]
    description: Reason = ""
    paid_from: PaidFrom
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""


class ExpenseOut(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID
    category: str
    amount_kobo: int
    spent_on: date
    payee: str
    description: str
    paid_from: PaidFrom
    reference: str
    status: ExpenseStatus
    recorded_by: uuid.UUID
    rejection_reason: str
    void_reason: str
    created_at: datetime


class DecisionIn(BaseModel):
    reason: Reason = ""


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    amount_kobo: int
    count: int


class IncomeExpenditureOut(BaseModel):
    start: date
    end: date
    income: list[LineOut] = Field(description="Money received, by how it came in (net of reversals)")
    expenditure: list[LineOut] = Field(description="Approved spending, by category")
    income_kobo: int
    expenditure_kobo: int
    surplus_kobo: int = Field(description="Income less expenditure (negative: a deficit)")
    awaiting_approval_kobo: int = Field(description="Spending recorded but not yet approved")
    billed_kobo: int = Field(description="Bills issued in the period, for comparison")


def _out(expense: Expense, category: str) -> ExpenseOut:
    fields = ExpenseOut.model_fields.keys() - {"category"}
    return ExpenseOut.model_validate({**{f: getattr(expense, f) for f in fields}, "category": category})


def _detail(db: Session, expense_id: uuid.UUID) -> ExpenseOut:
    expense, category = db.execute(service.expenses_query().where(Expense.id == expense_id)).one()
    return _out(expense, category)


@router.get("/expense-categories", response_model=list[CategoryOut], summary="The school's spending categories")
def list_categories(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> list[CategoryOut]:
    return [CategoryOut.model_validate(c) for c in service.categories(db, ctx.school_id)]


@router.post(
    "/expense-categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED, summary="Add a category"
)
def add_category(body: CategoryIn, ctx: StaffContext = APPROVE, db: Session = Depends(get_db)) -> CategoryOut:
    return CategoryOut.model_validate(service.save_category(db, ctx, None, name=body.name, is_active=body.is_active))


@router.put("/expense-categories/{category_id}", response_model=CategoryOut, summary="Rename or retire a category")
def change_category(
    category_id: uuid.UUID, body: CategoryIn, ctx: StaffContext = APPROVE, db: Session = Depends(get_db)
) -> CategoryOut:
    saved = service.save_category(db, ctx, category_id, name=body.name, is_active=body.is_active)
    return CategoryOut.model_validate(saved)


@router.get("/expenses", response_model=Page[ExpenseOut], summary="Spending, newest first")
def list_expenses(
    start: date | None = None,
    end: date | None = None,
    expense_status: ExpenseStatus | None = Query(default=None, alias="status"),
    category_id: uuid.UUID | None = None,
    params: PageParams = Depends(page_params),
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
) -> Page[ExpenseOut]:
    stmt = service.expenses_query(start=start, end=end, status=expense_status, category_id=category_id)
    rows, total = paginate_rows(db, stmt, params)
    return Page(items=[_out(e, name) for e, name in rows], total=total, limit=params.limit, offset=params.offset)


@router.post(
    "/expenses",
    response_model=ExpenseOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record spending (someone else approves it)",
)
def record_expense(body: ExpenseIn, ctx: StaffContext = RECORD, db: Session = Depends(get_db)) -> ExpenseOut:
    expense = service.record_expense(db, ctx, **body.model_dump())
    return _detail(db, expense.id)


@router.post("/expenses/{expense_id}/approve", response_model=ExpenseOut, summary="Approve spending (not your own)")
def approve_expense(expense_id: uuid.UUID, ctx: StaffContext = APPROVE, db: Session = Depends(get_db)) -> ExpenseOut:
    service.decide(db, ctx, expense_id, approve=True)
    return _detail(db, expense_id)


@router.post("/expenses/{expense_id}/reject", response_model=ExpenseOut, summary="Reject spending, with a reason")
def reject_expense(
    expense_id: uuid.UUID, body: DecisionIn, ctx: StaffContext = APPROVE, db: Session = Depends(get_db)
) -> ExpenseOut:
    service.decide(db, ctx, expense_id, approve=False, reason=body.reason)
    return _detail(db, expense_id)


@router.post(
    "/expenses/{expense_id}/void",
    response_model=ExpenseOut,
    summary="Void approved spending that was wrong (it stays on record)",
)
def void_expense(
    expense_id: uuid.UUID, body: DecisionIn, ctx: StaffContext = APPROVE, db: Session = Depends(get_db)
) -> ExpenseOut:
    service.void(db, ctx, expense_id, body.reason)
    return _detail(db, expense_id)


@router.get(
    "/reports/income-expenditure",
    response_model=IncomeExpenditureOut,
    summary="Money received against approved spending for a period",
)
def income_expenditure(
    start: date | None = None, end: date | None = None, ctx: StaffContext = REPORTS, db: Session = Depends(get_db)
) -> IncomeExpenditureOut:
    default_start, default_end = today_range(30)
    report = service.income_expenditure(db, start or default_start, end or default_end)
    return IncomeExpenditureOut(
        start=report.start,
        end=report.end,
        income=[LineOut.model_validate(i) for i in report.income],
        expenditure=[LineOut.model_validate(e) for e in report.expenditure],
        income_kobo=report.income_kobo,
        expenditure_kobo=report.expenditure_kobo,
        surplus_kobo=report.surplus_kobo,
        awaiting_approval_kobo=report.awaiting_approval_kobo,
        billed_kobo=report.billed_kobo,
    )

import uuid
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES
from app.core.pagination import PageParams, page_params
from app.core.ratelimit import get_limiter
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.payments.models import PaymentMethod
from app.modules.schools.models import ClassLevel, School
from app.modules.students.models import StudentStatus

from . import exports, service
from .service import AgingBucket, DebtorFilters, DebtorSort

router = APIRouter(prefix="/reports", tags=["reports"], responses=ERROR_RESPONSES)
VIEW = Depends(require(Capability.REPORTS_VIEW))
EXPORT_RATE = "60/hour"


class ClassRowOut(BaseModel):
    class_level_id: uuid.UUID | None
    class_name: str
    invoices: int
    billed_kobo: int
    collected_kobo: int
    outstanding_kobo: int
    students_owing: int


class DayTotalOut(BaseModel):
    day: date
    amount_kobo: int
    payments: int


class MethodTotalOut(BaseModel):
    method: PaymentMethod
    amount_kobo: int
    payments: int


class AgingRowOut(BaseModel):
    bucket: AgingBucket
    amount_kobo: int
    invoices: int


class OverviewOut(BaseModel):
    term_id: uuid.UUID
    term_label: str
    term_start: date
    term_end: date
    invoices: int
    billed_kobo: int
    collected_kobo: int
    outstanding_kobo: int
    total_outstanding_kobo: int
    older_outstanding_kobo: int
    students_owing: int
    credit_kobo: int
    received_today_kobo: int
    received_7_days_kobo: int
    received_this_term_kobo: int
    pending_approval: int
    pending_approval_kobo: int
    by_class: list[ClassRowOut]
    by_day: list[DayTotalOut]
    by_method: list[MethodTotalOut]
    aging: list[AgingRowOut]


class DebtorOut(BaseModel):
    student_id: uuid.UUID
    admission_number: str
    full_name: str
    status: StudentStatus
    class_name: str
    owed_kobo: int
    oldest_due: date | None
    days_overdue: int
    guardian_name: str
    guardian_phone: str
    reminders_off: bool
    reminders_sent: int
    last_paid_at: datetime | None
    on_plan: bool


class DebtorsOut(BaseModel):
    items: list[DebtorOut]
    total: int
    limit: int
    offset: int
    total_owed_kobo: int


class CollectionsOut(BaseModel):
    start: date
    end: date
    received_kobo: int
    reversed_kobo: int
    net_kobo: int
    payments: int
    by_day: list[DayTotalOut]
    by_method: list[MethodTotalOut]


def debtor_filters(
    class_level_id: uuid.UUID | None = None,
    min_owed_kobo: int = Query(default=1, ge=1, le=10**13),
    overdue_only: bool = False,
    search: str = Query(default="", max_length=100),
    sort: DebtorSort = DebtorSort.OWED,
) -> DebtorFilters:
    return DebtorFilters(class_level_id, min_owed_kobo, overdue_only, search, sort)


def date_range(
    start: date | None = Query(default=None, description="Lagos date; default: 30 days ago"),
    end: date | None = Query(default=None, description="Lagos date, inclusive; default: today"),
) -> tuple[date, date]:
    end = end or service.lagos_today()
    return start or end - timedelta(days=29), end


@router.get("/overview", response_model=OverviewOut, summary="Billed, collected and outstanding for a term")
def get_overview(
    term_id: uuid.UUID | None = Query(default=None, description="Default: the current term"),
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
) -> OverviewOut:
    return OverviewOut.model_validate(service.overview(db, term_id), from_attributes=True)


@router.get("/debtors", response_model=DebtorsOut, summary="Students who owe, most first")
def get_debtors(
    filters: DebtorFilters = Depends(debtor_filters),
    params: PageParams = Depends(page_params),
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
) -> DebtorsOut:
    page = service.debtors(db, filters, params)
    return DebtorsOut(
        items=[DebtorOut.model_validate(d, from_attributes=True) for d in page.items],
        total=page.total,
        limit=params.limit,
        offset=params.offset,
        total_owed_kobo=page.total_owed_kobo,
    )


@router.get("/collections", response_model=CollectionsOut, summary="Money received over a period")
def get_collections(
    period: tuple[date, date] = Depends(date_range), ctx: StaffContext = VIEW, db: Session = Depends(get_db)
) -> CollectionsOut:
    return CollectionsOut.model_validate(service.collections(db, *period), from_attributes=True)


def _download(content: bytes, kind: Literal["xlsx", "pdf"], name: str) -> Response:
    return Response(
        content,
        media_type=exports.XLSX if kind == "xlsx" else exports.PDF,
        headers={"Content-Disposition": f'attachment; filename="{name}.{kind}"', "Cache-Control": "no-store"},
    )


def _school_name(db: Session, ctx: StaffContext) -> str:
    school = db.get(School, ctx.school_id)
    return school.name if school else ""


@router.get(
    "/debtors/export",
    summary="Download the debtors list (Excel or PDF)",
    response_class=Response,
    responses={200: {"content": {exports.XLSX: {}, exports.PDF: {}}}},
)
def export_debtors(
    format: Literal["xlsx", "pdf"] = "xlsx",
    filters: DebtorFilters = Depends(debtor_filters),
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
) -> Response:
    get_limiter().hit("reports-export:school", EXPORT_RATE, str(ctx.school_id))
    scope = "All classes"
    if filters.class_level_id:
        scope = get_or_404(db, ClassLevel, filters.class_level_id, label="Class", field="class_level_id").name
    if filters.overdue_only:
        scope += ", overdue only"
    rows = service.all_debtors(db, filters)
    school = _school_name(db, ctx)
    content = (exports.debtors_xlsx if format == "xlsx" else exports.debtors_pdf)(school, scope, rows)
    # The list carries parents' names and phone numbers: who downloaded it is recorded
    record(
        db,
        AuditAction.REPORT_EXPORTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        changes={"report": "debtors", "format": format, "rows": len(rows), "scope": scope},
    )
    db.commit()
    return _download(content, format, f"debtors-{service.lagos_today().isoformat()}")


@router.get(
    "/collections/export",
    summary="Download money received over a period (Excel or PDF)",
    response_class=Response,
    responses={200: {"content": {exports.XLSX: {}, exports.PDF: {}}}},
)
def export_collections(
    format: Literal["xlsx", "pdf"] = "xlsx",
    period: tuple[date, date] = Depends(date_range),
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
) -> Response:
    get_limiter().hit("reports-export:school", EXPORT_RATE, str(ctx.school_id))
    start, end = period
    summary = service.collections(db, start, end)
    school = _school_name(db, ctx)
    if format == "xlsx":
        payments = service.collected_payments(db, start, end)
        content, rows = exports.collections_xlsx(school, summary, payments), len(payments)
    else:
        content, rows = exports.collections_pdf(school, summary), summary.payments
    record(
        db,
        AuditAction.REPORT_EXPORTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        changes={"report": "collections", "format": format, "rows": rows, "from": start, "to": end},
    )
    db.commit()
    return _download(content, format, f"money-received-{start.isoformat()}-to-{end.isoformat()}")

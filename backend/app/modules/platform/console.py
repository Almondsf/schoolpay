"""
The platform admin's view across schools: totals, the schools list with what
matters for support, one school's detail, and support lookups.

The platform admin sees **figures** for every school (pupils, money through the
platform, subscription balance) but not children's or parents' personal data:
lookups return a student's initials and admission number at most, and every
lookup is recorded in the platform's audit trail. Every function is a fixed
number of queries; the session is platform-wide.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.core.money import format_naira
from app.modules.accounts.deps import Principal
from app.modules.accounts.models import Role, User
from app.modules.audit.models import AuditAction, AuditLog
from app.modules.audit.service import record
from app.modules.payments.models import Payment, PaymentKind, SchoolPaymentAccount, VirtualAccount
from app.modules.receipts.models import Receipt
from app.modules.receipts.service import initials
from app.modules.schools.models import School, SchoolOrigin, SchoolStatus, Term
from app.modules.students.models import Enrollment, Student, StudentStatus

from . import billing
from .models import Plan, SubscriptionInvoice

LAGOS = ZoneInfo("Africa/Lagos")


def _pupils_by_school() -> Any:
    """school_id -> active pupils enrolled in the school's current term."""
    return (
        select(Enrollment.school_id.label("school_id"), func.count().label("pupils"))
        .join(Term, and_(Term.id == Enrollment.term_id, Term.is_current.is_(True)))
        .join(Student, Student.id == Enrollment.student_id)
        .where(Student.status == StudentStatus.ACTIVE)
        .group_by(Enrollment.school_id)
        .subquery("pupils")
    )


# --- Overview ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlatformOverview:
    schools_active: int
    schools_pending: int
    schools_suspended: int
    signups_30_days: int
    pupils: int
    processed_this_month_kobo: int  # money parents paid schools through SchoolPay (net of reversals)
    processed_last_month_kobo: int
    payments_this_month: int
    subscription_billed_kobo: int
    subscription_paid_kobo: int
    subscription_outstanding_kobo: int
    subscription_overdue_kobo: int


def overview(session: Session, today: date | None = None) -> PlatformOverview:
    today = today or datetime.now(LAGOS).date()
    month_start = today.replace(day=1)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    since = datetime.now(UTC) - timedelta(days=30)
    active, pending, suspended, signups = session.execute(
        select(
            func.count(case((School.status == SchoolStatus.ACTIVE, 1))),
            func.count(case((School.status == SchoolStatus.PENDING, 1))),
            func.count(case((School.status == SchoolStatus.SUSPENDED, 1))),
            func.count(case((and_(School.origin == SchoolOrigin.SIGNUP, School.created_at >= since), 1))),
        )
    ).one()
    pupils_q = _pupils_by_school()
    pupils = session.scalar(
        select(func.coalesce(func.sum(pupils_q.c.pupils), 0))
        .select_from(pupils_q)
        .join(School, and_(School.id == pupils_q.c.school_id, School.status == SchoolStatus.ACTIVE))
    )
    day = func.timezone("Africa/Lagos", Payment.received_at)
    this_month, last_month, count = session.execute(
        select(
            func.coalesce(func.sum(case((day >= month_start, Payment.amount_kobo), else_=0)), 0),
            func.coalesce(
                func.sum(case((and_(day >= last_month_start, day < month_start), Payment.amount_kobo), else_=0)), 0
            ),
            func.count(case((and_(day >= month_start, Payment.kind == PaymentKind.RECEIPT), 1))),
        ).where(day >= last_month_start)
    ).one()
    totals, paid = billing._amounts()
    total = func.coalesce(totals.c.total, 0)
    paid_col = func.coalesce(paid.c.paid, 0)
    balance = case((total > paid_col, total - paid_col), else_=0)
    billed, received, owed, overdue = session.execute(
        select(
            func.coalesce(func.sum(total), 0),
            func.coalesce(func.sum(paid_col), 0),
            func.coalesce(func.sum(balance), 0),
            func.coalesce(func.sum(case((SubscriptionInvoice.due_date < today, balance), else_=0)), 0),
        )
        .outerjoin(totals, totals.c.invoice_id == SubscriptionInvoice.id)
        .outerjoin(paid, paid.c.invoice_id == SubscriptionInvoice.id)
        .where(SubscriptionInvoice.voided_at.is_(None))
    ).one()
    return PlatformOverview(
        schools_active=int(active),
        schools_pending=int(pending),
        schools_suspended=int(suspended),
        signups_30_days=int(signups),
        pupils=int(pupils or 0),
        processed_this_month_kobo=int(this_month),
        processed_last_month_kobo=int(last_month),
        payments_this_month=int(count),
        subscription_billed_kobo=int(billed),
        subscription_paid_kobo=int(received),
        subscription_outstanding_kobo=int(owed),
        subscription_overdue_kobo=int(overdue),
    )


# --- Schools -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SchoolRow:
    school: School
    plan_name: str
    pupils: int
    proprietor_email: str
    payments_connected: bool
    subscription_owed_kobo: int
    last_sign_in_at: datetime | None


def schools(
    session: Session, *, search: str | None, status: SchoolStatus | None, limit: int, offset: int
) -> tuple[list[SchoolRow], int]:
    """A page of schools with what support needs at a glance. Four queries for any page."""
    stmt = select(School).order_by(School.created_at.desc(), School.name)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(or_(School.name.ilike(pattern), School.slug.ilike(pattern), School.email.ilike(pattern)))
    if status:
        stmt = stmt.where(School.status == status)
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    page = list(session.scalars(stmt.limit(limit).offset(offset)))
    return _rows(session, page), int(total)


def _rows(session: Session, page: list[School]) -> list[SchoolRow]:
    """Plan, pupils, proprietor, payments set-up, subscription balance and last sign-in. Three queries."""
    ids = [s.id for s in page]
    if not ids:
        return []
    pupils_q = _pupils_by_school()
    default = billing.default_plan(session)
    facts = {
        sid: (plan, pupils, email, connected, last_seen)
        for sid, plan, pupils, email, connected, last_seen in session.execute(
            select(
                School.id,
                Plan.name,
                func.coalesce(pupils_q.c.pupils, 0),
                select(func.min(User.email))
                .where(User.school_id == School.id, User.role == Role.PROPRIETOR)
                .correlate(School)
                .scalar_subquery(),
                select(func.count())
                .where(SchoolPaymentAccount.school_id == School.id)
                .correlate(School)
                .scalar_subquery(),
                select(func.max(User.last_login_at))
                .where(User.school_id == School.id)
                .correlate(School)
                .scalar_subquery(),
            )
            .outerjoin(Plan, Plan.id == School.plan_id)
            .outerjoin(pupils_q, pupils_q.c.school_id == School.id)
            .where(School.id.in_(ids))
        ).all()
    }
    owed = billing.outstanding_by_school(session, ids)
    rows = []
    for school in page:
        plan, pupils, email, connected, last_seen = facts[school.id]
        rows.append(
            SchoolRow(
                school=school,
                plan_name=plan or f"{default.name} (default)",
                pupils=int(pupils),
                proprietor_email=email or "",
                payments_connected=bool(connected),
                subscription_owed_kobo=owed.get(school.id, 0),
                last_sign_in_at=last_seen,
            )
        )
    return rows


@dataclass(frozen=True)
class StaffRow:
    full_name: str
    email: str
    role: Role
    is_active: bool
    mfa_enabled: bool
    invited: bool  # hasn't set a password yet
    last_login_at: datetime | None


@dataclass(frozen=True)
class SchoolDetail:
    row: SchoolRow
    plan_id: uuid.UUID | None
    staff: list[StaffRow]
    students_active: int
    invoices_this_term: int
    collected_this_term_kobo: int
    collected_30_days_kobo: int
    last_activity_at: datetime | None


def school_detail(session: Session, school_id: uuid.UUID) -> SchoolDetail:
    school = session.get(School, school_id)
    if school is None:
        raise NotFound("School not found.")
    [row] = _rows(session, [school])
    staff = [
        StaffRow(u.full_name, u.email, u.role, u.is_active, u.mfa_enabled, u.password_hash is None, u.last_login_at)
        for u in session.scalars(select(User).where(User.school_id == school_id).order_by(User.role, User.full_name))
    ]
    since = datetime.now(UTC) - timedelta(days=30)
    current_term = select(Term.id).where(Term.school_id == school_id, Term.is_current.is_(True)).scalar_subquery()
    students, collected_30, last_activity = session.execute(
        select(
            select(func.count())
            .where(Student.school_id == school_id, Student.status == StudentStatus.ACTIVE)
            .scalar_subquery(),
            select(func.coalesce(func.sum(Payment.amount_kobo), 0))
            .where(Payment.school_id == school_id, Payment.received_at >= since)
            .scalar_subquery(),
            select(func.max(AuditLog.created_at)).where(AuditLog.school_id == school_id).scalar_subquery(),
        )
    ).one()
    from app.modules.fees.invoicing import totals_subquery  # fees doesn't depend on platform
    from app.modules.fees.ledger import paid_subquery
    from app.modules.fees.models import Invoice

    totals, paid = totals_subquery(), paid_subquery()
    invoices, collected = session.execute(
        select(func.count(Invoice.id), func.coalesce(func.sum(paid.c.paid_kobo), 0))
        .outerjoin(totals, totals.c.invoice_id == Invoice.id)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .where(Invoice.school_id == school_id, Invoice.term_id == current_term, Invoice.voided_at.is_(None))
    ).one()
    return SchoolDetail(
        row=row,
        plan_id=school.plan_id,
        staff=staff,
        students_active=int(students),
        invoices_this_term=int(invoices),
        collected_this_term_kobo=int(collected),
        collected_30_days_kobo=int(collected_30),
        last_activity_at=last_activity,
    )


# --- Support lookup ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LookupHit:
    kind: str  # school | staff | payment | account_number | receipt
    school_id: uuid.UUID | None
    school_name: str
    title: str
    detail: str


def lookup(session: Session, admin: Principal, query: str) -> list[LookupHit]:
    """
    Find what a support call is about: a school (name, short name, email), a staff
    member (exact email), a payment (gateway or bank reference), a student's account
    number, or a receipt's verification code. Students show as initials only.
    """
    q = query.strip()
    hits: list[LookupHit] = []
    if len(q) < 3:
        return hits
    for school in session.scalars(
        select(School)
        .where(or_(School.name.ilike(f"%{q}%"), School.slug.ilike(f"%{q}%"), School.email == q.lower()))
        .order_by(School.name)
        .limit(10)
    ):
        hits.append(LookupHit("school", school.id, school.name, school.name, f"{school.slug} · {school.status}"))
    staff = session.execute(
        select(User, School.name).outerjoin(School, School.id == User.school_id).where(User.email == q.lower())
    ).first()
    if staff:
        user, school_name = staff
        state = (
            "invited, no password yet"
            if user.password_hash is None
            else ("active" if user.is_active else "deactivated")
        )
        hits.append(
            LookupHit(
                "staff",
                user.school_id,
                school_name or "Platform",
                f"{user.full_name} ({user.role})",
                f"{state} · two-factor {'on' if user.mfa_enabled else 'off'}",
            )
        )
    for payment, receipt_no, first, last, admission, school_name, reversed_ in session.execute(
        select(
            Payment,
            Receipt.number,
            Student.first_name,
            Student.last_name,
            Student.admission_number,
            School.name,
            select(func.count()).where(Payment.reverses_payment_id == Payment.id).correlate(Payment).scalar_subquery(),
        )
        .join(Student, Student.id == Payment.student_id)
        .join(School, School.id == Payment.school_id)
        .outerjoin(Receipt, Receipt.payment_id == Payment.id)
        .where(or_(Payment.gateway_reference == q, Payment.reference == q))
        .limit(10)
    ).all():
        hits.append(
            LookupHit(
                "payment",
                payment.school_id,
                school_name,
                f"{format_naira(payment.amount_kobo)} for {initials(first, last)} ({admission})",
                f"{payment.method} on {payment.received_at.astimezone(LAGOS):%d %b %Y %H:%M}"
                + (f" · receipt {receipt_no}" if receipt_no else "")
                + (" · reversed" if reversed_ else ""),
            )
        )
    for account, first, last, admission, school_name in session.execute(
        select(VirtualAccount, Student.first_name, Student.last_name, Student.admission_number, School.name)
        .join(Student, Student.id == VirtualAccount.student_id)
        .join(School, School.id == VirtualAccount.school_id)
        .where(VirtualAccount.account_number == q)
        .limit(5)
    ).all():
        hits.append(
            LookupHit(
                "account_number",
                account.school_id,
                school_name,
                f"Account {account.account_number}: {initials(first, last)} ({admission})",
                f"{account.bank_name} · {account.status}",
            )
        )
    for receipt, school_name in session.execute(
        select(Receipt, School.name).join(School, School.id == Receipt.school_id).where(Receipt.verification_code == q)
    ).all():
        hits.append(
            LookupHit(
                "receipt",
                receipt.school_id,
                school_name,
                f"Receipt {receipt.number}",
                f"{format_naira(receipt.amount_kobo)} · issued {receipt.issued_at.astimezone(LAGOS):%d %b %Y}",
            )
        )
    record(
        session,
        AuditAction.SUPPORT_LOOKUP,
        school_id=None,
        actor=admin.actor,
        meta=admin.meta,
        changes={"query": q[:100], "results": [{"kind": h.kind, "school": str(h.school_id)} for h in hits]},
    )
    session.commit()
    return hits

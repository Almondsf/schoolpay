"""
Spending, and income against spending.

- Categories: every school starts with `DEFAULT_CATEGORIES` (made on first look, one statement) and can
  add, rename and retire its own. A retired category keeps its history but takes no new spending.
- An expense is recorded by one person (`payments.record`) and approved or rejected by another
  (`payments.approve`, maker-checker by service and constraint). An approved one that was wrong is
  voided with a reason: it stops counting but stays on record. Nothing is deleted (trigger).
- Income and expenditure for a period: money received (payments net of reversals, by how it came in)
  against approved spending by category, and what's still waiting for approval. Fixed queries.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import Date, Select, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import lagos_today
from app.core.errors import Conflict, DomainError, NotFound, PermissionDenied
from app.modules.accounting import periods
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.fees.models import Invoice, InvoiceLine
from app.modules.payments.models import Payment, PaymentMethod

from .models import DEFAULT_CATEGORIES, Expense, ExpenseCategory, ExpenseStatus, PaidFrom

MAX_DAYS = 400  # a session and a little


# --- Categories ---------------------------------------------------------------------------------------


def categories(session: Session, school_id: uuid.UUID) -> list[ExpenseCategory]:
    """The school's categories, creating the defaults the first time. Two statements."""
    session.execute(
        insert(ExpenseCategory)
        .values([{"id": uuid.uuid4(), "school_id": school_id, "name": name} for name in DEFAULT_CATEGORIES])
        .on_conflict_do_nothing(index_elements=["school_id", "name"])
    )
    session.commit()
    return list(
        session.scalars(select(ExpenseCategory).order_by(ExpenseCategory.is_active.desc(), ExpenseCategory.name))
    )


def save_category(
    session: Session, ctx: StaffContext, category_id: uuid.UUID | None, *, name: str, is_active: bool = True
) -> ExpenseCategory:
    name = name.strip()
    if len(name) < 2:
        raise DomainError("Give the category a name.", field="name")
    clash = session.scalar(
        select(ExpenseCategory.id).where(
            func.lower(ExpenseCategory.name) == name.lower(), ExpenseCategory.id != category_id
        )
    )
    if clash is not None:
        raise Conflict("There's already a category with that name.", field="name")
    if category_id is None:
        category = ExpenseCategory(id=uuid.uuid4(), school_id=ctx.school_id, name=name, is_active=is_active)
        session.add(category)
    else:
        found = session.scalar(select(ExpenseCategory).where(ExpenseCategory.id == category_id).with_for_update())
        if found is None:
            raise NotFound("Category not found.")
        category = found
        category.name, category.is_active = name, is_active
    session.commit()
    return category


# --- Spending ------------------------------------------------------------------------------------------


def record_expense(
    session: Session,
    ctx: StaffContext,
    *,
    category_id: uuid.UUID,
    amount_kobo: int,
    spent_on: date,
    payee: str,
    description: str,
    paid_from: PaidFrom,
    reference: str,
) -> Expense:
    category = session.scalar(select(ExpenseCategory).where(ExpenseCategory.id == category_id))
    if category is None:
        raise NotFound("Category not found.", field="category_id")
    if not category.is_active:
        raise DomainError("That category is retired. Choose another.", field="category_id")
    if amount_kobo <= 0:
        raise DomainError("Enter the amount spent.", field="amount_kobo")
    if spent_on > lagos_today():
        raise DomainError("That date is in the future.", field="spent_on")
    periods.ensure_open(session, ctx.school_id, spent_on, field="spent_on")
    if len(payee.strip()) < 2:
        raise DomainError("Say who was paid.", field="payee")
    expense = Expense(
        id=uuid.uuid4(),
        school_id=ctx.school_id,
        category_id=category.id,
        amount_kobo=amount_kobo,
        spent_on=spent_on,
        payee=payee.strip()[:150],
        description=description.strip()[:300],
        paid_from=paid_from,
        reference=reference.strip()[:100],
        recorded_by=ctx.user.id,
    )
    session.add(expense)
    record(
        session,
        AuditAction.EXPENSE_RECORDED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=expense,
        changes={"amount_kobo": amount_kobo, "category": category.name, "payee": expense.payee, "paid_from": paid_from},
    )
    session.commit()
    return expense


def _locked(session: Session, expense_id: uuid.UUID) -> Expense:
    expense = session.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if expense is None:
        raise NotFound("Expense not found.")
    return expense


def decide(session: Session, ctx: StaffContext, expense_id: uuid.UUID, *, approve: bool, reason: str = "") -> Expense:
    """A second person approves or rejects a recorded expense."""
    expense = _locked(session, expense_id)
    if expense.status != ExpenseStatus.PENDING:
        raise Conflict(f"This expense was already {expense.status.value}.")
    if expense.recorded_by == ctx.user.id:
        raise PermissionDenied("Someone other than the person who recorded it must approve it.", code="maker_checker")
    if not approve and len(reason.strip()) < 3:
        raise DomainError("Say why it's rejected.", field="reason")
    periods.ensure_open(session, ctx.school_id, expense.spent_on)
    expense.status = ExpenseStatus.APPROVED if approve else ExpenseStatus.REJECTED
    expense.decided_by, expense.decided_at = ctx.user.id, datetime.now(UTC)
    expense.rejection_reason = "" if approve else reason.strip()[:300]
    record(
        session,
        AuditAction.EXPENSE_APPROVED if approve else AuditAction.EXPENSE_REJECTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=expense,
        changes={"amount_kobo": expense.amount_kobo, "reason": reason.strip()[:300]},
    )
    session.commit()
    return expense


def void(session: Session, ctx: StaffContext, expense_id: uuid.UUID, reason: str) -> Expense:
    """An approved expense that was wrong stops counting; it stays on record with who voided it and why."""
    expense = _locked(session, expense_id)
    if expense.status != ExpenseStatus.APPROVED:
        raise Conflict("Only an approved expense can be voided.")
    if len(reason.strip()) < 3:
        raise DomainError("Say why it's being voided.", field="reason")
    expense.status = ExpenseStatus.VOIDED
    expense.voided_by, expense.voided_at, expense.void_reason = ctx.user.id, datetime.now(UTC), reason.strip()[:300]
    record(
        session,
        AuditAction.EXPENSE_VOIDED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=expense,
        changes={"amount_kobo": expense.amount_kobo, "reason": expense.void_reason},
    )
    session.commit()
    return expense


def expenses_query(
    *,
    start: date | None = None,
    end: date | None = None,
    status: ExpenseStatus | None = None,
    category_id: uuid.UUID | None = None,
) -> Select[Expense, str]:
    stmt = (
        select(Expense, ExpenseCategory.name)
        .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .order_by(Expense.spent_on.desc(), Expense.created_at.desc())
    )
    if start:
        stmt = stmt.where(Expense.spent_on >= start)
    if end:
        stmt = stmt.where(Expense.spent_on <= end)
    if status:
        stmt = stmt.where(Expense.status == status)
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
    return stmt


# --- Income and expenditure ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    label: str
    amount_kobo: int
    count: int = 0


@dataclass
class IncomeExpenditure:
    start: date
    end: date
    income: list[Line] = field(default_factory=list)  # money received, by how it came in
    expenditure: list[Line] = field(default_factory=list)  # approved spending, by category
    awaiting_approval_kobo: int = 0
    billed_kobo: int = 0  # bills issued in the period, for comparison

    @property
    def income_kobo(self) -> int:
        return sum(i.amount_kobo for i in self.income)

    @property
    def expenditure_kobo(self) -> int:
        return sum(e.amount_kobo for e in self.expenditure)

    @property
    def surplus_kobo(self) -> int:
        return self.income_kobo - self.expenditure_kobo


METHOD_LABELS = {
    PaymentMethod.VIRTUAL_ACCOUNT: "Transfers to pupils' account numbers",
    PaymentMethod.CARD: "Card",
    PaymentMethod.CHECKOUT_TRANSFER: "Transfer at checkout",
    PaymentMethod.USSD: "USSD",
    PaymentMethod.CASH: "Cash",
    PaymentMethod.TELLER: "Bank teller",
    PaymentMethod.POS: "POS",
    PaymentMethod.BANK_TRANSFER: "Transfer to the school's account",
}


def income_expenditure(session: Session, start: date, end: date) -> IncomeExpenditure:
    """Money in against money out for [start, end] (Lagos days). Four queries. The session is scoped."""
    if end < start:
        raise DomainError("The end date is before the start date.", field="end")
    if (end - start).days >= MAX_DAYS:
        raise DomainError("Choose a shorter period (a session or less).", field="start")
    report = IncomeExpenditure(start, end)
    paid_on = cast(func.timezone("Africa/Lagos", Payment.received_at), Date)
    report.income = [
        Line(METHOD_LABELS.get(method, str(method)), int(amount), int(count))
        for method, amount, count in session.execute(
            select(Payment.method, func.sum(Payment.amount_kobo), func.count())
            .where(paid_on.between(start, end))
            .group_by(Payment.method)
            .order_by(func.sum(Payment.amount_kobo).desc())
        )
        if amount
    ]
    report.expenditure = [
        Line(name, int(amount), int(count))
        for name, amount, count in session.execute(
            select(ExpenseCategory.name, func.sum(Expense.amount_kobo), func.count())
            .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
            .where(Expense.status == ExpenseStatus.APPROVED, Expense.spent_on.between(start, end))
            .group_by(ExpenseCategory.name)
            .order_by(func.sum(Expense.amount_kobo).desc())
        )
    ]
    report.awaiting_approval_kobo = int(
        session.scalar(
            select(func.coalesce(func.sum(Expense.amount_kobo), 0)).where(
                Expense.status == ExpenseStatus.PENDING, Expense.spent_on.between(start, end)
            )
        )
        or 0
    )
    issued_on = cast(func.timezone("Africa/Lagos", Invoice.issued_at), Date)
    report.billed_kobo = int(
        session.scalar(
            select(func.coalesce(func.sum(InvoiceLine.amount_kobo), 0))
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(Invoice.voided_at.is_(None), issued_on.between(start, end))
        )
        or 0
    )
    return report

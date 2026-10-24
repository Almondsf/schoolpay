"""
The general journal for the school's accountant: everything WardPay knows about the school's money over
a period, as balanced double entries against a simple chart of accounts.

Entries (each dated in Lagos time):
- **Bill issued** (or a later line on it): Dr fees receivable, Cr income by fee type. Discounts are
  Dr discounts / Cr receivable; adjustments go to "bill adjustments"; an opening-balance bill (what a
  pupil owed before the school joined) credits opening balances, not income.
- **Bill voided**: the bill's lines reversed on the day it was voided.
- **Payment received**: Dr cash (cash payments) or bank (everything else: transfers, card, teller,
  POS), Cr receivable for what went on bills that day, Cr parents' credit for the rest.
  A reversal is the same with the signs turned.
- **Credit applied later** (a bill on a later day uses money paid ahead): Dr parents' credit, Cr receivable.
  "That day" and "later" compare the Lagos days each was recorded, not timestamps.
- **Cash banked**, once a second person has checked it against the bank: Dr bank, Cr cash.
- **Spending approved**: Dr the spending category, Cr cash or bank (card counts as bank). Voided
  spending is reversed on the day it was voided.

Card and online payments are recorded gross: the provider's fee comes off before the money reaches the
bank, so the accountant sees it on the bank statement (settlement reconciliation comes later).
Pupils are identified by admission number only; no names leave in this file.

A fixed number of queries whatever the period; the session is scoped to the school.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, Date, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, NotFound
from app.modules.accounts.deps import StaffContext
from app.modules.expenses.models import Expense, ExpenseCategory, ExpenseStatus, PaidFrom
from app.modules.fees.models import FeeCategory, FeeItem, Invoice, InvoiceKind, InvoiceLine, LineKind
from app.modules.payments.models import (
    Allocation,
    CashDeposit,
    CashDepositStatus,
    Payment,
    PaymentKind,
    PaymentMethod,
)
from app.modules.receipts.models import Receipt
from app.modules.students.models import Student

from .models import JournalAccount

MAX_DAYS = 400  # a session and a little over

# key: (default code, default name, type)
DEFAULT_ACCOUNTS: dict[str, tuple[str, str, str]] = {
    "cash": ("1000", "Cash in hand", "Asset"),
    "bank": ("1010", "Bank", "Asset"),
    "receivable": ("1100", "School fees receivable", "Asset"),
    "parent_credit": ("2100", "Fees paid in advance", "Liability"),
    "opening": ("3000", "Opening balances brought forward", "Equity"),
    "fee:tuition": ("4000", "Tuition fees", "Income"),
    "fee:levy": ("4010", "Levies", "Income"),
    "fee:service": ("4020", "Services (bus, lunch, boarding)", "Income"),
    "fee:materials": ("4030", "Books and materials", "Income"),
    "fee:other": ("4090", "Other fees", "Income"),
    "discounts": ("4900", "Discounts and scholarships", "Income"),
    "adjustments": ("4950", "Bill adjustments", "Income"),
}
EXPENSE_CODE_START = 5000
EXPENSE_CODE_STEP = 10


@dataclass(frozen=True)
class Account:
    key: str
    code: str
    name: str
    type: str
    default_code: str
    default_name: str


@dataclass
class Entry:
    day: date
    order: int  # within a day: bills, then payments, then credit, banking, spending
    reference: str
    description: str
    amounts: dict[str, int] = field(default_factory=lambda: defaultdict(int))  # + debit, - credit
    number: str = ""

    def post(self, debit: str, credit: str, kobo: int) -> None:
        """Debit one account and credit another; a negative amount turns them round."""
        self.amounts[debit] += kobo
        self.amounts[credit] -= kobo


@dataclass(frozen=True)
class AccountTotal:
    account: Account
    debit_kobo: int
    credit_kobo: int


@dataclass
class Journal:
    start: date
    end: date
    accounts: list[Account]
    entries: list[Entry]
    totals: list[AccountTotal]
    debit_kobo: int
    credit_kobo: int
    unchecked_deposits: int  # banking a second person hasn't checked yet: left out


def _lagos_day(column: Any) -> ColumnElement[date]:
    return cast(func.timezone("Africa/Lagos", column), Date)


def chart(session: Session) -> list[Account]:
    """The default chart with the school's own names and codes. Two queries."""
    saved = {a.key: a for a in session.scalars(select(JournalAccount))}
    categories = session.execute(
        select(ExpenseCategory.id, ExpenseCategory.name).order_by(ExpenseCategory.created_at, ExpenseCategory.name)
    ).all()
    defaults = list(DEFAULT_ACCOUNTS.items()) + [
        (f"expense:{cid}", (str(EXPENSE_CODE_START + EXPENSE_CODE_STEP * i), name, "Expense"))
        for i, (cid, name) in enumerate(categories)
    ]
    accounts = []
    for key, (code, name, kind) in defaults:
        own = saved.get(key)
        accounts.append(Account(key, own.code if own else code, own.name if own else name, kind, code, name))
    return accounts


def rename(session: Session, ctx: StaffContext, key: str, *, code: str, name: str) -> Account:
    """Give an account the school's own code and name (blank both to go back to the default)."""
    account = next((a for a in chart(session) if a.key == key), None)
    if account is None:
        raise NotFound("No such account.")
    code, name = code.strip(), name.strip()
    taken = {a.code for a in chart(session) if a.key != key}
    if (code or account.default_code) in taken:
        raise Conflict("Another account already has that code.", field="code")
    session.execute(
        JournalAccount.__table__.delete().where(  # type: ignore[attr-defined]
            JournalAccount.school_id == ctx.school_id, JournalAccount.key == key
        )
    )
    if code or name:
        session.add(
            JournalAccount(
                id=uuid.uuid4(),
                school_id=ctx.school_id,
                key=key,
                code=code or account.default_code,
                name=name or account.default_name,
            )
        )
    session.commit()
    return next(a for a in chart(session) if a.key == key)


def _income_account(kind: LineKind, invoice_kind: InvoiceKind, category: FeeCategory | None) -> str:
    if invoice_kind == InvoiceKind.OPENING_BALANCE:
        return "opening"
    if kind == LineKind.DISCOUNT:
        return "discounts"
    if kind == LineKind.ADJUSTMENT:
        return "adjustments"
    return f"fee:{category or FeeCategory.OTHER}"


def _bills(session: Session, start: date, end: date) -> list[Entry]:
    """Bill lines added, and bills voided, in the period. One query."""
    added = _lagos_day(InvoiceLine.created_at)
    voided = _lagos_day(Invoice.voided_at)
    rows = session.execute(
        select(
            InvoiceLine.invoice_id,
            InvoiceLine.kind,
            InvoiceLine.amount_kobo,
            added.label("added_on"),
            voided.label("voided_on"),
            Invoice.number,
            Invoice.kind,
            FeeItem.category,
            Student.admission_number,
        )
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .join(Student, Student.id == Invoice.student_id)
        .outerjoin(FeeItem, FeeItem.id == InvoiceLine.fee_item_id)
        .where(or_(added.between(start, end), voided.between(start, end)))
        .order_by(Invoice.number, InvoiceLine.position)
    ).all()
    entries: dict[tuple[uuid.UUID, date, bool], Entry] = {}
    for invoice_id, kind, amount, added_on, voided_on, number, invoice_kind, category, admission in rows:
        account = _income_account(kind, invoice_kind, category)
        what = "Opening balance" if invoice_kind == InvoiceKind.OPENING_BALANCE else "Bill"
        if start <= added_on <= end:
            entry = entries.setdefault(
                (invoice_id, added_on, False), Entry(added_on, 0, f"Bill {number}", f"{what} · {admission}")
            )
            entry.post("receivable", account, amount)
        if voided_on is not None and start <= voided_on <= end:
            entry = entries.setdefault(
                (invoice_id, voided_on, True), Entry(voided_on, 0, f"Bill {number}", f"{what} voided · {admission}")
            )
            entry.post("receivable", account, -amount)
    return list(entries.values())


def _payments(session: Session, start: date, end: date) -> list[Entry]:
    """Payments and reversals received in the period, with what went on bills at the time. One query."""
    received = _lagos_day(Payment.received_at)
    at_the_time = (
        select(func.coalesce(func.sum(Allocation.amount_kobo), 0))
        .where(Allocation.payment_id == Payment.id, _lagos_day(Allocation.created_at) == _lagos_day(Payment.created_at))
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            received,
            Payment.kind,
            Payment.method,
            Payment.amount_kobo,
            Payment.reference,
            at_the_time,
            Receipt.number,
            Student.admission_number,
        )
        .join(Student, Student.id == Payment.student_id)
        .outerjoin(Receipt, Receipt.payment_id == func.coalesce(Payment.reverses_payment_id, Payment.id))
        .where(received.between(start, end))
        .order_by(Payment.received_at)
    ).all()
    entries = []
    for day, kind, method, amount, reference, on_bills, receipt, admission in rows:
        reversal = kind == PaymentKind.REVERSAL
        entry = Entry(
            day,
            1,
            f"Receipt {receipt}" if receipt else (reference or ""),
            f"{'Payment reversed' if reversal else 'Payment received'} · {method.replace('_', ' ')} · {admission}",
        )
        to = "cash" if method == PaymentMethod.CASH else "bank"
        entry.post(to, "receivable", int(on_bills))
        entry.post(to, "parent_credit", amount - int(on_bills))
        entries.append(entry)
    return entries


def _credit_applied(session: Session, start: date, end: date) -> list[Entry]:
    """Money paid ahead put on a bill later. One query."""
    applied = _lagos_day(Allocation.created_at)
    rows = session.execute(
        select(applied, Allocation.amount_kobo, Invoice.number, Student.admission_number)
        .join(Payment, Payment.id == Allocation.payment_id)
        .join(Invoice, Invoice.id == Allocation.invoice_id)
        .join(Student, Student.id == Invoice.student_id)
        .where(applied != _lagos_day(Payment.created_at), applied.between(start, end))
        .order_by(Allocation.created_at)
    ).all()
    entries = []
    for day, amount, number, admission in rows:
        entry = Entry(day, 2, f"Bill {number}", f"Credit applied to bill · {admission}")
        entry.post("parent_credit", "receivable", amount)
        entries.append(entry)
    return entries


def _banking(session: Session, start: date, end: date) -> tuple[list[Entry], int]:
    """Cash taken to the bank and checked by a second person; and how many await the check. One query."""
    rows = session.execute(
        select(CashDeposit.banked_on, CashDeposit.status, CashDeposit.amount_kobo, CashDeposit.bank_reference)
        .where(CashDeposit.banked_on.between(start, end), CashDeposit.status != CashDepositStatus.REJECTED)
        .order_by(CashDeposit.banked_on, CashDeposit.created_at)
    ).all()
    entries, unchecked = [], 0
    for day, status, amount, reference in rows:
        if status == CashDepositStatus.PENDING:
            unchecked += 1
            continue
        entry = Entry(day, 3, reference, "Cash banked")
        entry.post("bank", "cash", amount)
        entries.append(entry)
    return entries, unchecked


def _spending(session: Session, start: date, end: date) -> list[Entry]:
    """Approved spending, and spending voided, in the period. One query."""
    voided = _lagos_day(Expense.voided_at)
    rows = session.execute(
        select(
            Expense.category_id,
            Expense.spent_on,
            voided,
            Expense.amount_kobo,
            Expense.paid_from,
            Expense.payee,
            Expense.reference,
        )
        .where(
            Expense.status.in_((ExpenseStatus.APPROVED, ExpenseStatus.VOIDED)),
            or_(
                Expense.spent_on.between(start, end),
                and_(Expense.status == ExpenseStatus.VOIDED, voided.between(start, end)),
            ),
        )
        .order_by(Expense.spent_on, Expense.created_at)
    ).all()
    entries = []
    for category_id, spent_on, voided_on, amount, paid_from, payee, reference in rows:
        account, source = f"expense:{category_id}", "cash" if paid_from == PaidFrom.CASH else "bank"
        if start <= spent_on <= end:
            entry = Entry(spent_on, 4, reference, f"Paid to {payee}")
            entry.post(account, source, amount)
            entries.append(entry)
        if voided_on is not None and start <= voided_on <= end:
            entry = Entry(voided_on, 4, reference, f"Voided: paid to {payee}")
            entry.post(account, source, -amount)
            entries.append(entry)
    return entries


def journal(session: Session, start: date, end: date) -> Journal:
    if end < start:
        raise DomainError("The end date is before the start date.", field="end")
    if (end - start).days >= MAX_DAYS:
        raise DomainError(f"Choose at most {MAX_DAYS} days at a time.", field="start")
    accounts = chart(session)
    banked, unchecked = _banking(session, start, end)
    entries = [
        *_bills(session, start, end),
        *_payments(session, start, end),
        *_credit_applied(session, start, end),
        *banked,
        *_spending(session, start, end),
    ]
    entries = [e for e in entries if any(e.amounts.values())]
    entries.sort(key=lambda e: (e.day, e.order))
    for i, entry in enumerate(entries, start=1):
        entry.number = f"J{i:05d}"
    debit: dict[str, int] = defaultdict(int)
    credit: dict[str, int] = defaultdict(int)
    for entry in entries:
        for key, kobo in entry.amounts.items():
            if kobo > 0:
                debit[key] += kobo
            elif kobo < 0:
                credit[key] -= kobo
    totals = [AccountTotal(a, debit[a.key], credit[a.key]) for a in accounts if debit[a.key] or credit[a.key]]
    return Journal(
        start=start,
        end=end,
        accounts=accounts,
        entries=entries,
        totals=totals,
        debit_kobo=sum(debit.values()),
        credit_kobo=sum(credit.values()),
        unchecked_deposits=unchecked,
    )


def lines(j: Journal) -> list[tuple[Entry, Account, int, int]]:
    """(entry, account, debit, credit) rows in chart order within each entry, for the downloads."""
    by_key = {a.key: a for a in j.accounts}
    position = {a.key: i for i, a in enumerate(j.accounts)}
    out = []
    for entry in j.entries:
        for key in sorted(
            (k for k, v in entry.amounts.items() if v), key=lambda k: (entry.amounts[k] < 0, position[k])
        ):
            kobo = entry.amounts[key]
            out.append((entry, by_key[key], max(kobo, 0), max(-kobo, 0)))
    return out

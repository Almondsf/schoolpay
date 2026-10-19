"""
A "lived-in" demo school: what the first half of a term looks like.

- Last session (2025/2026) exists, and about one student in eight carries a
  debt from it (opening balance).
- Some students take the school bus; a few have a staff-child discount or a
  scholarship.
- The first-term bills were generated on 7 September, due 30 September.
- Payments since then: some families paid in full, some in instalments, a few
  paid too much (credit), and about a quarter haven't paid yet. Transfers into
  children's account numbers, card payments, cash, tellers and POS.
- Every payment has its numbered receipt and an audit entry; parents were sent
  receipts, the fee notice and reminders (message log).
- One teller was reversed (it bounced), and two cash/teller entries are
  waiting for the bursar's approval.

Payments are applied with the real allocation code (`payments.allocation`),
so balances, credit and receipts reconcile exactly as live ones do. Everything
is built in bulk statements: no query per student.
"""

import random
import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.http import RequestMeta
from app.core.money import format_naira
from app.core.sequences import reserve
from app.demo import DemoSchool
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction, AuditLog
from app.modules.fees.invoicing import INVOICE_SEQUENCE, account_summaries, generate_term_invoices
from app.modules.fees.models import (
    Discount,
    DiscountKind,
    Invoice,
    InvoiceKind,
    InvoiceLine,
    LineKind,
    StudentDiscount,
    StudentFeeChoice,
)
from app.modules.notifications import templates
from app.modules.notifications.models import Channel, MessageKind, MessageLog, MessageStatus
from app.modules.payments.allocation import allocate
from app.modules.payments.gateways import GatewayCode
from app.modules.payments.models import (
    ManualPayment,
    ManualPaymentStatus,
    Payment,
    PaymentKind,
    PaymentMethod,
    VirtualAccount,
)
from app.modules.payments.service import reverse_payment
from app.modules.receipts.models import Receipt
from app.modules.receipts.service import RECEIPT_SEQUENCE, verification_url
from app.modules.schools.models import AcademicSession, Term
from app.modules.students.models import Guardian

LAGOS = ZoneInfo("Africa/Lagos")
BILLED_ON = datetime(2026, 9, 7, 8, 0, tzinfo=LAGOS)
DUE = date(2026, 9, 30)
REMINDER_DAYS = [DUE - timedelta(days=7), DUE - timedelta(days=1), DUE + timedelta(days=3), DUE + timedelta(days=14)]
METHODS = [
    (PaymentMethod.VIRTUAL_ACCOUNT, 55),
    (PaymentMethod.CARD, 12),
    (PaymentMethod.TELLER, 15),
    (PaymentMethod.CASH, 10),
    (PaymentMethod.POS, 5),
    (PaymentMethod.BANK_TRANSFER, 3),
]
MANUAL = {PaymentMethod.CASH, PaymentMethod.TELLER, PaymentMethod.POS, PaymentMethod.BANK_TRANSFER}


def add_activity(session: Session, demo: DemoSchool, *, transfers_by_account: bool) -> None:
    """`session` is scoped to the demo school."""
    rng = random.Random(1960)  # noqa: S311  (demo data, not security)
    school, students = demo.school, demo.students
    ids = [s.id for s in students]
    ctx = StaffContext(user=demo.bursar, session_id=uuid.uuid4(), meta=RequestMeta(ip="127.0.0.1"), school_id=school.id)

    _last_session(session, school.id)
    _choices_and_discounts(session, demo, rng)
    opening = _opening_balances(session, demo, rng)
    session.flush()

    # The term's bills, through the real generator (discounts, bus, sibling rule), dated 7 September
    generate_term_invoices(
        session, ctx, term_id=demo.term.id, due_date=DUE, class_level_ids=None, dry_run=False, notify_parents=False
    )
    session.execute(
        update(Invoice)
        .where(Invoice.school_id == school.id, Invoice.kind == InvoiceKind.TERM)
        .values(issued_at=BILLED_ON)
    )
    billed = {sid: s.billed_kobo for sid, s in account_summaries(session, ids).items()}
    term_invoices = dict(
        session.execute(
            select(Invoice.student_id, Invoice.id).where(
                Invoice.school_id == school.id, Invoice.kind == InvoiceKind.TERM
            )
        ).all()
    )

    today = datetime.now(LAGOS).date()
    last_day = max(date(2026, 9, 8), min(today - timedelta(days=1), demo.term.end_date))
    payments = _payments(demo, rng, billed, last_day, transfers_by_account)
    if demo.term.start_date <= today <= demo.term.end_date:
        payments += _this_morning(demo, rng, billed, payments, transfers_by_account)
    session.add_all(payments)
    session.flush()
    allocations = allocate(session, ids)
    receipts = _receipts(session, demo, payments, allocations, billed)
    approved = _manual_records(demo, payments)
    session.add_all([*receipts, *approved, *_pending(demo, rng, last_day)])

    accounts = dict(
        session.execute(
            select(VirtualAccount.student_id, VirtualAccount.account_number).where(
                VirtualAccount.school_id == school.id
            )
        ).all()
    )
    guardians = sorted({g.id: g for g in demo.primary_guardian.values()}.values(), key=lambda g: g.phone)
    opted_out = {g.id for g in rng.sample(guardians[1:], 6)}  # parents who asked for no reminders
    session.execute(
        update(Guardian)
        .where(Guardian.id.in_(opted_out))
        .values(reminders_opt_out_at=datetime(2026, 9, 10, 12, tzinfo=LAGOS))
    )
    session.add_all(
        _messages(demo, rng, payments, receipts, billed, opening, term_invoices, accounts, opted_out, today)
    )
    session.add_all(_audit(demo, payments, receipts))
    session.commit()

    # A teller that bounced, reversed through the real code path
    first_family = {s.id for s in students[:2]}  # the portal demo family stays tidy
    bounced = next(
        (
            p
            for p in payments
            if p.method == PaymentMethod.TELLER and p.received_at.date() < last_day and p.student_id not in first_family
        ),
        None,
    )
    if bounced is not None:
        # The bank returned it a few days later (not "today", which would skew today's takings)
        returned = min(bounced.received_at + timedelta(days=3), datetime.combine(last_day, time(16, 0), tzinfo=LAGOS))
        reverse_payment(session, ctx, bounced.id, "Teller returned unpaid by the bank", at=returned)


def _last_session(session: Session, school_id: uuid.UUID) -> None:
    academic = AcademicSession(
        id=uuid.uuid4(), school_id=school_id, name="2025/2026", start_date=date(2025, 9, 1), end_date=date(2026, 7, 31)
    )
    session.add(academic)
    session.flush()
    session.add_all(
        Term(id=uuid.uuid4(), school_id=school_id, session_id=academic.id, number=n, start_date=s, end_date=e)
        for n, (s, e) in enumerate(
            [
                (date(2025, 9, 8), date(2025, 12, 12)),
                (date(2026, 1, 5), date(2026, 4, 2)),
                (date(2026, 4, 20), date(2026, 7, 24)),
            ],
            start=1,
        )
    )


def _choices_and_discounts(session: Session, demo: DemoSchool, rng: random.Random) -> None:
    school_id, tuition = demo.school.id, demo.fee_items["Tuition"].id
    bus = rng.sample(demo.students, len(demo.students) * 35 // 100)
    session.add_all(
        StudentFeeChoice(
            school_id=school_id, student_id=s.id, term_id=demo.term.id, fee_item_id=demo.fee_items["School bus"].id
        )
        for s in bus
    )
    staff_child = Discount(
        id=uuid.uuid4(),
        school_id=school_id,
        name="Staff child (50% of tuition)",
        kind=DiscountKind.PERCENT,
        value=5000,
        fee_item_id=tuition,
    )
    scholarship = Discount(
        id=uuid.uuid4(),
        school_id=school_id,
        name="Academic scholarship",
        kind=DiscountKind.FIXED,
        value=100_000 * 100,
        fee_item_id=tuition,
    )
    session.add_all([staff_child, scholarship])
    session.flush()
    chosen = rng.sample(demo.students[2:], 7)  # not the first family, so the portal demo shows plain bills
    session.add_all(
        StudentDiscount(school_id=school_id, student_id=s.id, discount_id=(staff_child if i < 4 else scholarship).id)
        for i, s in enumerate(chosen)
    )


def _opening_balances(session: Session, demo: DemoSchool, rng: random.Random) -> dict[uuid.UUID, int]:
    """About one student in eight owes from last session, including the first family's younger child."""
    owing = [demo.students[1], *rng.sample(demo.students[2:], len(demo.students) // 8)]
    amounts = {s.id: rng.randrange(15_000, 120_000, 2_500) * 100 for s in owing}
    numbers = reserve(session, demo.school.id, INVOICE_SEQUENCE, len(owing))
    issued = datetime(2026, 9, 1, 9, 0, tzinfo=LAGOS)
    invoices = [
        Invoice(
            id=uuid.uuid4(),
            school_id=demo.school.id,
            number=number,
            student_id=student.id,
            kind=InvoiceKind.OPENING_BALANCE,
            issued_at=issued,
            created_by=demo.bursar.id,
        )
        for student, number in zip(owing, numbers, strict=True)
    ]
    session.add_all(invoices)
    session.flush()
    session.add_all(
        InvoiceLine(
            school_id=demo.school.id,
            invoice_id=invoice.id,
            position=1,
            kind=LineKind.FEE,
            description="Balance brought forward from 2025/2026 third term",
            amount_kobo=amounts[invoice.student_id],
            created_by=demo.bursar.id,
        )
        for invoice in invoices
    )
    return amounts


def _moment(rng: random.Random, start: date, end: date) -> datetime:
    """A weekday daytime moment between start and end (inclusive)."""
    day = start + timedelta(days=rng.randint(0, max((end - start).days, 0)))
    while day.weekday() >= 5 and day > start:
        day -= timedelta(days=1)
    return datetime.combine(day, time(rng.randint(7, 18), rng.randint(0, 59)), tzinfo=LAGOS)


def _payments(
    demo: DemoSchool, rng: random.Random, billed: dict[uuid.UUID, int], last_day: date, transfers_by_account: bool
) -> list[Payment]:
    """Full, instalment, over- and non-payers, by a realistic mix of methods."""
    term_start = date(2026, 9, 1)
    payments: list[Payment] = []
    for n, student in enumerate(demo.students):
        owed = billed[student.id]
        # The first family: one child paid in full, the other part paid
        roll = 0.1 if n == 0 else 0.5 if n == 1 else rng.random()
        if roll < 0.40:  # paid in full, mostly early, sometimes in two goes
            parts = [owed] if rng.random() < 0.7 else [owed // 2 // 100_000 * 100_000, 0]
            if len(parts) == 2:
                parts[1] = owed - parts[0]
        elif roll < 0.70:  # part paid
            first = max(owed * rng.randint(25, 60) // 100 // 500_000 * 500_000, 2_000_000)
            parts = [first] if rng.random() < 0.6 else [first, max(owed // 5 // 500_000 * 500_000, 1_000_000)]
            parts = [p for p in parts if p > 0]
            if sum(parts) >= owed:
                parts = [owed // 2]
        elif roll < 0.75:  # paid a little too much: the rest is credit for next term
            parts = [owed + rng.choice([5_000, 10_000, 20_000]) * 100]
        else:
            continue
        when = sorted(
            _moment(rng, term_start if i == 0 else DUE - timedelta(days=5), last_day if i else DUE + timedelta(days=4))
            for i in range(len(parts))
        )
        for amount, received_at in zip(parts, when, strict=True):
            method = rng.choices([m for m, _ in METHODS], weights=[w for _, w in METHODS])[0]
            if method == PaymentMethod.VIRTUAL_ACCOUNT and not transfers_by_account:
                method = PaymentMethod.BANK_TRANSFER
            payments.append(_payment(demo, rng, student.id, amount, method, min(received_at, _end_of(last_day))))
    return payments


def _this_morning(
    demo: DemoSchool,
    rng: random.Random,
    billed: dict[uuid.UUID, int],
    payments: list[Payment],
    transfers_by_account: bool,
) -> list[Payment]:
    """A few transfers from parents who still owe, earlier today, so "received today" isn't empty."""
    paid: dict[uuid.UUID, int] = {}
    for p in payments:
        paid[p.student_id] = paid.get(p.student_id, 0) + p.amount_kobo
    owing = [s for s in demo.students[2:] if billed[s.id] - paid.get(s.id, 0) >= 30_000 * 100]
    now = datetime.now(LAGOS)
    method = PaymentMethod.VIRTUAL_ACCOUNT if transfers_by_account else PaymentMethod.BANK_TRANSFER
    return [
        _payment(
            demo,
            rng,
            s.id,
            rng.choice([10_000, 15_000, 20_000, 25_000]) * 100,
            method,
            now - timedelta(minutes=17 * (n + 1)),
        )
        for n, s in enumerate(rng.sample(owing, min(5, len(owing))))
        if (now - timedelta(minutes=17 * (n + 1))).date() == now.date()  # only if it's still today in Lagos
    ]


def _end_of(day: date) -> datetime:
    return datetime.combine(day, time(17, 0), tzinfo=LAGOS)


def _payment(
    demo: DemoSchool, rng: random.Random, student_id: uuid.UUID, amount: int, method: PaymentMethod, at: datetime
) -> Payment:
    gateway = GatewayCode.FAKE if method in (PaymentMethod.VIRTUAL_ACCOUNT, PaymentMethod.CARD) else None
    reference = {
        PaymentMethod.TELLER: f"TLR{rng.randint(10_000_000, 99_999_999)}",
        PaymentMethod.POS: f"POS{rng.randint(100_000, 999_999)}",
        PaymentMethod.BANK_TRANSFER: f"NIP/{rng.randint(10**9, 10**10 - 1)}",
        PaymentMethod.CARD: f"SP-{secrets.token_hex(6).upper()}",
    }.get(method, "")
    manual = method in MANUAL
    return Payment(
        id=uuid.uuid4(),
        school_id=demo.school.id,
        student_id=student_id,
        kind=PaymentKind.RECEIPT,
        amount_kobo=amount,
        method=method,
        gateway=gateway,
        gateway_reference=f"FAKE_{secrets.token_hex(8).upper()}" if gateway else None,
        received_at=at,
        reference=reference,
        recorded_by=demo.admin.id if manual else None,
        approved_by=demo.bursar.id if manual else None,
        created_at=at + timedelta(minutes=2 if not manual else 90),
    )


def _receipts(
    session: Session,
    demo: DemoSchool,
    payments: list[Payment],
    allocations: list[Any],
    billed: dict[uuid.UUID, int],
) -> list[Receipt]:
    """Numbered in the order money arrived, with the same snapshot a live receipt has."""
    school = demo.school
    ordered = sorted(payments, key=lambda p: p.received_at)
    numbers = reserve(session, school.id, RECEIPT_SEQUENCE, len(ordered))
    invoice_numbers = dict(
        session.execute(
            select(Invoice.id, Invoice.number).where(Invoice.id.in_({a.invoice_id for a in allocations}))
        ).all()
    )
    applied: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for a in allocations:
        applied[a.payment_id].append(a)
    students = {s.id: s for s in demo.students}
    paid_so_far: dict[uuid.UUID, int] = defaultdict(int)
    receipts = []
    for payment, number in zip(ordered, numbers, strict=True):
        student = students[payment.student_id]
        paid_so_far[student.id] += payment.amount_kobo
        used = applied[payment.id]
        receipts.append(
            Receipt(
                id=uuid.uuid4(),
                school_id=school.id,
                number=number,
                payment_id=payment.id,
                student_id=student.id,
                amount_kobo=payment.amount_kobo,
                verification_code=secrets.token_urlsafe(16),
                issued_at=payment.created_at,
                snapshot={
                    "school": {
                        "name": school.name,
                        "address": school.address,
                        "phone": school.phone,
                        "email": school.email,
                    },
                    "student": {
                        "first_name": student.first_name,
                        "last_name": student.last_name,
                        "full_name": student.full_name,
                        "admission_number": student.admission_number,
                        "class": demo.placements[student.id],
                    },
                    "payment": {
                        "method": payment.method.value,
                        "reference": payment.reference or payment.gateway_reference or "",
                        "received_at": payment.received_at.isoformat(),
                    },
                    "applied": [
                        {"invoice_number": invoice_numbers.get(a.invoice_id), "amount_kobo": a.amount_kobo}
                        for a in used
                    ],
                    "credit_kobo": payment.amount_kobo - sum(a.amount_kobo for a in used),
                    "balance_after_kobo": max(billed[student.id] - paid_so_far[student.id], 0),
                },
            )
        )
    return receipts


def _manual_records(demo: DemoSchool, payments: list[Payment]) -> list[ManualPayment]:
    """The approved cash/teller/POS entries behind manual payments (recorded by the admin, approved by the bursar)."""
    return [
        ManualPayment(
            school_id=demo.school.id,
            student_id=p.student_id,
            amount_kobo=p.amount_kobo,
            method=p.method,
            received_on=p.received_at.date(),
            reference=p.reference,
            status=ManualPaymentStatus.APPROVED,
            recorded_by=demo.admin.id,
            decided_by=demo.bursar.id,
            decided_at=p.created_at,
            payment_id=p.id,
        )
        for p in payments
        if p.method in MANUAL
    ]


def _pending(demo: DemoSchool, rng: random.Random, last_day: date) -> list[ManualPayment]:
    """Two entries the bursar still has to approve."""
    picks = rng.sample(demo.students[2:], 2)
    return [
        ManualPayment(
            school_id=demo.school.id,
            student_id=picks[0].id,
            amount_kobo=50_000 * 100,
            method=PaymentMethod.CASH,
            received_on=last_day,
            note="Paid at the bursary by the father",
            recorded_by=demo.admin.id,
        ),
        ManualPayment(
            school_id=demo.school.id,
            student_id=picks[1].id,
            amount_kobo=85_000 * 100,
            method=PaymentMethod.TELLER,
            received_on=last_day,
            reference=f"TLR{rng.randint(10_000_000, 99_999_999)}",
            recorded_by=demo.admin.id,
        ),
    ]


def _messages(
    demo: DemoSchool,
    rng: random.Random,
    payments: list[Payment],
    receipts: list[Receipt],
    billed: dict[uuid.UUID, int],
    opening: dict[uuid.UUID, int],
    term_invoices: dict[uuid.UUID, uuid.UUID],
    accounts: dict[uuid.UUID, str],
    opted_out: set[uuid.UUID],
    today: date,
) -> list[MessageLog]:
    """What parents were sent: the bill on 7 September, receipts, and reminders while money was owed."""
    settings = get_settings()
    school = demo.school
    students = {s.id: s for s in demo.students}
    receipt_of = {r.payment_id: r for r in receipts}
    by_student: dict[uuid.UUID, list[Payment]] = defaultdict(list)
    for p in sorted(payments, key=lambda p: p.received_at):
        by_student[p.student_id].append(p)

    def base(student_id: uuid.UUID, owed: int) -> dict[str, str]:
        student = students[student_id]
        account = accounts.get(student_id)
        return {
            "parent_name": demo.primary_guardian[student_id].full_name,
            "student_name": student.full_name,
            "school_name": school.name,
            "balance": format_naira(max(owed, 0)),
            "term": "2026/2027 first term",
            "due_date": "30 Sep 2026",
            "portal_url": f"{settings.frontend_url}/parent",
            "pay_to": f"Pay by transfer to {account} (Fake Bank), {student.first_name}'s own account number."
            if account
            else "",
        }

    def channel(student_id: uuid.UUID) -> Channel:
        guardian = demo.primary_guardian[student_id]
        roll = rng.random()
        return Channel.EMAIL if guardian.email and roll < 0.05 else Channel.SMS if roll < 0.3 else Channel.WHATSAPP

    def message(
        student_id: uuid.UUID, kind: MessageKind, key: str, at: datetime, body: str, **links: uuid.UUID
    ) -> MessageLog:
        guardian = demo.primary_guardian[student_id]
        skipped = kind != MessageKind.RECEIPT and guardian.id in opted_out and at.date() > date(2026, 9, 10)
        return MessageLog(
            school_id=school.id,
            guardian_id=guardian.id,
            student_id=student_id,
            kind=kind,
            dedupe_key=key,
            status=MessageStatus.SKIPPED if skipped else MessageStatus.SENT,
            channel=None if skipped else channel(student_id),
            body="" if skipped else body,
            skip_reason="opted_out" if skipped else "",
            attempts=0 if skipped else 1,
            created_at=at,
            sent_at=None if skipped else at + timedelta(seconds=20),
            **links,
        )

    logs: list[MessageLog] = []
    notice_at = BILLED_ON + timedelta(hours=1)
    for student_id, invoice_id in term_invoices.items():
        values = base(student_id, billed[student_id])
        values["amount"] = format_naira(billed[student_id] - opening.get(student_id, 0))
        values["due_phrase"] = " (due 30 Sep 2026)"
        body = templates.render(templates.DEFAULTS[MessageKind.FEE_NOTICE], values)
        logs.append(message(student_id, MessageKind.FEE_NOTICE, f"notice:{invoice_id}", notice_at, body,
                            invoice_id=invoice_id))  # fmt: skip

    for payment in payments:
        receipt = receipt_of[payment.id]
        values = base(payment.student_id, receipt.snapshot["balance_after_kobo"])
        values |= {
            "amount": format_naira(payment.amount_kobo),
            "receipt_number": str(receipt.number),
            "verify_url": verification_url(receipt.verification_code),
        }
        body = templates.render(templates.DEFAULTS[MessageKind.RECEIPT], values)
        logs.append(message(payment.student_id, MessageKind.RECEIPT, f"receipt:{payment.id}",
                            payment.created_at + timedelta(seconds=5), body, payment_id=payment.id))  # fmt: skip

    for day in (d for d in REMINDER_DAYS if d < today):
        at = datetime.combine(day, time(9, 0), tzinfo=LAGOS)
        for student_id, invoice_id in term_invoices.items():
            paid = sum(p.amount_kobo for p in by_student[student_id] if p.received_at < at)
            owed = billed[student_id] - paid
            if owed <= 0:
                continue
            values = base(student_id, owed)
            values["due_phrase"] = f" ({'was due' if day > DUE else 'due'} 30 Sep 2026)"
            body = templates.render(templates.DEFAULTS[MessageKind.REMINDER], values)
            logs.append(message(student_id, MessageKind.REMINDER, f"reminder:{invoice_id}:{day.isoformat()}", at,
                                body, invoice_id=invoice_id))  # fmt: skip
    return logs


def _audit(demo: DemoSchool, payments: list[Payment], receipts: list[Receipt]) -> list[AuditLog]:
    number = {r.payment_id: r.number for r in receipts}
    return [
        AuditLog(
            school_id=demo.school.id,
            actor_user_id=demo.bursar.id if p.method in MANUAL else None,
            actor_label=demo.bursar.email if p.method in MANUAL else "system:gateway",
            action=AuditAction.PAYMENT_RECORDED.value,
            target_type="Payment",
            target_id=str(p.id),
            changes={
                "student": str(p.student_id),
                "amount_kobo": p.amount_kobo,
                "method": p.method.value,
                "gateway_reference": p.gateway_reference,
                "receipt_number": number[p.id],
            },
            created_at=p.created_at,
        )
        for p in payments
    ]

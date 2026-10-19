"""The demo seed builds a consistent school in a fixed number of statements."""

from sqlalchemy import func, select, text

from app import demo
from app.core.crypto import blind_index
from app.core.db import SessionLocal
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.fees.invoicing import account_summaries
from app.modules.fees.models import Invoice
from app.modules.notifications.models import MessageLog, MessageStatus
from app.modules.payments.models import ManualPayment, ManualPaymentStatus, Payment, PaymentKind
from app.modules.receipts.models import Receipt
from app.modules.schools.models import School
from app.modules.students.models import Guardian, GuardianStudent, Student
from app.modules.students.summary import roll_summary
from tests.factories import QueryCounter


def _demo_school(db) -> School:
    scope_platform_wide(db)
    return db.scalar(select(School).where(School.slug == demo.DEMO_SLUG))


def test_lived_in_demo_reconciles(db, connection):
    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # the ledger's deferred checks run on every statement
    with QueryCounter(connection) as counter:
        demo.seed_demo("JBSWY3DPEHPK3PXP", reset=False)
    assert counter.count < 120  # bulk statements, never one per student

    school = _demo_school(db)
    sid = school.id
    student_ids = list(db.scalars(select(Student.id).where(Student.school_id == sid)))
    assert len(student_ids) == 246  # 240 billed, plus the loose ends (not billed yet)

    receipts = list(db.scalars(select(Receipt.number).where(Receipt.school_id == sid).order_by(Receipt.number)))
    payments = db.scalar(select(func.count()).where(Payment.school_id == sid, Payment.kind == PaymentKind.RECEIPT))
    assert receipts == list(range(1, payments + 1))  # one per payment, no gaps
    assert db.scalar(select(func.count()).where(Payment.school_id == sid, Payment.kind == PaymentKind.REVERSAL)) == 1
    assert (
        db.scalar(
            select(func.count()).where(
                ManualPayment.school_id == sid, ManualPayment.status == ManualPaymentStatus.PENDING
            )
        )
        == 2
    )

    # Money in = money applied + credit, and every student's balance comes from the ledger
    summaries = account_summaries(db, student_ids)
    received = db.scalar(select(func.sum(Payment.amount_kobo)).where(Payment.school_id == sid))
    assert sum(s.paid_kobo for s in summaries.values()) <= received
    assert all(s.outstanding_kobo == s.billed_kobo - s.paid_kobo for s in summaries.values())
    assert db.scalar(select(func.count()).where(Invoice.school_id == sid)) > 240  # term bills + opening balances

    # The first family: one child paid up, the other still owing (with last session's debt)
    family = list(
        db.scalars(
            select(GuardianStudent.student_id)
            .join(Guardian, Guardian.id == GuardianStudent.guardian_id)
            .where(Guardian.school_id == sid, Guardian.phone_index == blind_index(demo.FIRST_PARENT_PHONE))
        )
    )
    assert sorted(summaries[s].outstanding_kobo == 0 for s in family) == [False, True]

    statuses = dict(
        db.execute(
            select(MessageLog.status, func.count()).where(MessageLog.school_id == sid).group_by(MessageLog.status)
        ).all()
    )
    assert statuses[MessageStatus.SENT] > 240 and statuses[MessageStatus.SKIPPED] > 0

    # The school overview has realistic loose ends to show
    with SessionLocal() as scoped:
        scope_to_school(scoped, sid)
        roll = roll_summary(scoped)
    assert (roll.on_roll, roll.unplaced, roll.left_this_term) == (245, 2, 1)
    assert (roll.no_parent, roll.no_consent, roll.no_account_number) == (1, 2, 5)
    assert 0 < roll.joined_this_term < 100  # the intakes and a few transfers, not everyone

    others = set(db.scalars(select(School.slug).where(School.slug.like("demo-%"))))
    assert {"demo-kings-queens", "demo-bright-futures", "demo-sunrise"} <= others


def test_bare_demo_for_browser_tests(db):
    demo.seed_demo("JBSWY3DPEHPK3PXP", reset=False, bare=True)
    school = _demo_school(db)
    assert db.scalar(select(func.count()).where(Student.school_id == school.id)) == 60
    assert db.scalar(select(func.count()).where(Invoice.school_id == school.id)) == 0

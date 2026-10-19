"""
A parent's family payment page and its card checkout (the link itself: `family_links.py`).

The page shows each child's first name, class, what's owed (or, on a payment plan,
what's needed up to the next instalment) and their account number for transfers.
For a card payment the parent sets the amount per child; one card charge becomes one
payment per child (`checkout.confirm`), each applied oldest bill first with its own
receipt. Nothing is split automatically, so the receipts match what the parent meant.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import DomainError, NotFound
from app.core.http import RequestMeta
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record
from app.modules.fees.invoicing import paid_subquery, totals_subquery
from app.modules.fees.models import Invoice
from app.modules.fees.plans import plan_views
from app.modules.fees.setup_service import MAX_AMOUNT_KOBO
from app.modules.schools.models import Arm, ClassLevel, School, Term
from app.modules.students.models import Enrollment, Guardian, GuardianStudent, Student, StudentStatus

from .accounts import payment_account, require_payment_account
from .checkout import checkout_email, new_reference
from .family_links import Resolved
from .gateways import get_gateway
from .models import FamilyPayLink, PaymentIntent, PaymentIntentItem, VirtualAccount, VirtualAccountStatus

MIN_PART_KOBO = 1_000 * 100  # smallest share per child, so card charges don't eat tiny payments


# --- The page -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyChild:
    student_id: uuid.UUID
    first_name: str
    class_name: str | None
    owed_kobo: int
    suggested_kobo: int  # everything owed, but only up to the next instalment on a payment plan
    on_plan: bool
    account: VirtualAccount | None


@dataclass(frozen=True)
class FamilyPage:
    school_name: str
    card_payments: bool
    children: list[FamilyChild]


def _children(session: Session, guardian_id: uuid.UUID) -> list[Student]:
    return list(
        session.scalars(
            select(Student)
            .join(GuardianStudent, GuardianStudent.student_id == Student.id)
            .where(GuardianStudent.guardian_id == guardian_id, Student.status == StudentStatus.ACTIVE)
            .order_by(Student.first_name, Student.id)
        )
    )


def _owed(session: Session, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int, bool]]:
    """student -> (owed, suggested, on a plan). A bill on a plan asks for what's needed up to its next instalment."""
    result = dict.fromkeys(student_ids, (0, 0, False))
    if not student_ids:
        return result
    totals, paid = totals_subquery(), paid_subquery()
    open_bills = [
        (invoice_id, student_id, int(total or 0) - int(done or 0))
        for invoice_id, student_id, total, done in session.execute(
            select(Invoice.id, Invoice.student_id, totals.c.total_kobo, paid.c.paid_kobo)
            .outerjoin(totals, totals.c.invoice_id == Invoice.id)
            .outerjoin(paid, paid.c.invoice_id == Invoice.id)
            .where(Invoice.student_id.in_(student_ids), Invoice.voided_at.is_(None))
        )
    ]
    open_bills = [bill for bill in open_bills if bill[2] > 0]
    plans = plan_views(session, [invoice_id for invoice_id, _, _ in open_bills])
    for invoice_id, student_id, left in open_bills:
        plan = plans.get(invoice_id)
        ask = min(left, plan.next_amount_kobo) if plan and plan.next_due else left
        owed, suggested, on_plan = result[student_id]
        result[student_id] = (owed + left, suggested + ask, on_plan or plan is not None)
    return result


def family_page(session: Session, found: Resolved) -> FamilyPage:
    """What the link shows, in a fixed number of queries. The session is scoped to the link's school."""
    guardian = session.get(Guardian, found.guardian_id)
    school = session.get(School, found.school_id)
    if guardian is None or school is None:
        raise NotFound("This link has expired.")
    session.execute(
        update(FamilyPayLink).where(FamilyPayLink.id == found.link_id).values(last_opened_at=datetime.now(UTC))
    )
    students = _children(session, guardian.id)
    ids = [s.id for s in students]
    owed = _owed(session, ids)
    classes = (
        dict(
            session.execute(
                select(Enrollment.student_id, ClassLevel.name + " " + Arm.name)
                .join(Term, and_(Term.id == Enrollment.term_id, Term.is_current.is_(True)))
                .join(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
                .join(Arm, Arm.id == Enrollment.arm_id)
                .where(Enrollment.student_id.in_(ids))
            ).all()
        )
        if ids
        else {}
    )
    accounts = (
        {
            va.student_id: va
            for va in session.scalars(
                select(VirtualAccount).where(
                    VirtualAccount.student_id.in_(ids), VirtualAccount.status == VirtualAccountStatus.ACTIVE
                )
            )
        }
        if ids
        else {}
    )
    page = FamilyPage(
        school_name=school.name,
        card_payments=payment_account(session) is not None,
        children=[
            FamilyChild(s.id, s.first_name, classes.get(s.id), *owed[s.id], accounts.get(s.id)) for s in students
        ],
    )
    session.commit()
    return page


def start_checkout(
    session: Session, found: Resolved, parts: list[tuple[uuid.UUID, int]], meta: RequestMeta
) -> PaymentIntent:
    """One card checkout for the amounts the parent chose per child (zero means not this time)."""
    account = require_payment_account(session)
    chosen = [(sid, amount) for sid, amount in parts if amount != 0]
    if not chosen:
        raise DomainError("Enter an amount for at least one child.", field="items")
    if len({sid for sid, _ in chosen}) != len(chosen):
        raise DomainError("Each child can only appear once.", field="items")
    children = {s.id: s for s in _children(session, found.guardian_id)}
    owed = _owed(session, list(children))
    for sid, amount in chosen:
        if sid not in children:
            raise NotFound("Child not found.")
        name, left = children[sid].first_name, owed[sid][0]
        if left <= 0:
            raise DomainError(f"{name} owes nothing at the moment.", field="items")
        if amount < 0 or amount > left:
            raise DomainError(f"For {name}, enter up to what's owed.", field="items")
        if amount < min(MIN_PART_KOBO, left):
            raise DomainError(f"For {name}, pay at least ₦1,000 (or everything owed).", field="items")
    total = sum(amount for _, amount in chosen)
    if total > MAX_AMOUNT_KOBO:
        raise DomainError("That's more than one card payment can take. Pay in two goes.", field="items")
    guardian = session.get(Guardian, found.guardian_id)
    school = session.get(School, found.school_id)
    if guardian is None or school is None:
        raise NotFound("This link has expired.")
    intent = PaymentIntent(
        id=uuid.uuid4(),
        reference=new_reference(),
        family_link_id=found.link_id,
        gateway=account.gateway,
        amount_kobo=total,
        email=checkout_email(guardian, school),
    )
    session.add(intent)
    session.flush()
    session.add_all(
        PaymentIntentItem(
            school_id=found.school_id, intent_id=intent.id, student_id=sid, position=n, amount_kobo=amount
        )
        for n, (sid, amount) in enumerate(chosen, start=1)
    )
    settings = get_settings()
    checkout = get_gateway(account.gateway).initialize_payment(
        reference=intent.reference,
        amount_kobo=total,
        email=intent.email,
        subaccount_code=account.subaccount_code,
        callback_url=f"{settings.frontend_url}{settings.payment_callback_path}",
        description=f"School fees at {school.name}: {len(chosen)} {'child' if len(chosen) == 1 else 'children'}",
    )
    intent.authorization_url = checkout.authorization_url
    record(
        session,
        AuditAction.PAYMENT_LINK_CREATED,
        school_id=found.school_id,
        actor=Actor.system("family-link"),
        meta=meta,
        target=intent,
        changes={
            "parent": found.guardian_id,
            "amount_kobo": total,
            "reference": intent.reference,
            "children": [{"student": sid, "amount_kobo": amount} for sid, amount in chosen],
        },
    )
    session.commit()
    return intent

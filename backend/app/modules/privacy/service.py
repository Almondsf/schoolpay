"""
Data protection (NDPA 2023): a parent's right to a copy of their data, the
right to erasure, and retention limits.

**Export.** Everything held about a parent and their children at one school,
as JSON: the parent's details and consent, each child's details and class
history, bills with their lines, payments with receipt numbers, and the
messages sent. A fixed number of queries. Parents download it from the portal;
staff can download it for a parent who asks in person. Every export is audited.

**Erasure.** A school must keep financial records for years (CAMA/FIRS: six),
so erasure *anonymises* rather than deletes. Names, phone numbers, emails,
dates of birth and message texts are removed. Bills, payments and allocations
stay, linked to a record named "Erased", so the school's accounts still add up.
Issued receipts are legal records and keep the name they were issued with
(append-only, see docs/PRIVACY.md). A parent can only be erased once none of
their children are current pupils (the school needs a contact for them). A
student can only be erased once they have left.

**Retention** (`apply_retention`, daily): message texts older than
`MESSAGE_BODY_RETENTION_DAYS` are cleared, and students who left more than
`RECORDS_RETENTION_YEARS` ago are erased, with their parents if those have no
other children still on record. Bulk statements only.
"""

import secrets
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Text, and_, cast, exists, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import Conflict
from app.core.repo import get_or_404
from app.core.tenancy import scope_platform_wide
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Acting, Actor, record
from app.modules.fees.models import Invoice, InvoiceLine
from app.modules.notifications.models import MessageLog
from app.modules.parents.models import ParentSession
from app.modules.payments.models import Payment, VirtualAccount, VirtualAccountStatus
from app.modules.receipts.models import Receipt
from app.modules.schools.models import AcademicSession, Arm, ClassLevel, School, Term
from app.modules.students.models import Enrollment, Guardian, GuardianStudent, Student, StudentStatus

ERASED_NAME = "Erased"


def _kobo(value: int) -> dict[str, Any]:
    return {"kobo": value, "naira": f"{value // 100}.{value % 100:02d}"}


# --- Export -------------------------------------------------------------------------------------------


def export_guardian(session: Session, actor: Acting, guardian_id: uuid.UUID) -> dict[str, Any]:
    """Everything the school holds about this parent and their children. Eight queries."""
    guardian = get_or_404(session, Guardian, guardian_id, label="Guardian")
    school = session.get(School, actor.school_id)
    links = list(session.scalars(select(GuardianStudent).where(GuardianStudent.guardian_id == guardian.id)))
    child_ids = [link.student_id for link in links]
    children = {s.id: s for s in session.scalars(select(Student).where(Student.id.in_(child_ids)))}
    history: dict[uuid.UUID, list[dict[str, str]]] = defaultdict(list)
    for student_id, session_name, term_number, level, arm in session.execute(
        select(Enrollment.student_id, AcademicSession.name, Term.number, ClassLevel.name, Arm.name)
        .join(Term, Term.id == Enrollment.term_id)
        .join(AcademicSession, AcademicSession.id == Term.session_id)
        .join(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
        .join(Arm, Arm.id == Enrollment.arm_id)
        .where(Enrollment.student_id.in_(child_ids))
        .order_by(AcademicSession.start_date, Term.number)
    ).all():
        history[student_id].append({"session": session_name, "term": str(term_number), "class": f"{level} {arm}"})
    invoices = list(
        session.scalars(select(Invoice).where(Invoice.student_id.in_(child_ids)).order_by(Invoice.issued_at))
    )
    lines: dict[uuid.UUID, list[InvoiceLine]] = defaultdict(list)
    for line in session.scalars(
        select(InvoiceLine)
        .where(InvoiceLine.invoice_id.in_([i.id for i in invoices]))
        .order_by(InvoiceLine.invoice_id, InvoiceLine.position)
    ):
        lines[line.invoice_id].append(line)
    payments = session.execute(
        select(Payment, Receipt.number)
        .outerjoin(Receipt, Receipt.payment_id == Payment.id)
        .where(Payment.student_id.in_(child_ids))
        .order_by(Payment.received_at)
    ).all()
    messages = list(
        session.scalars(select(MessageLog).where(MessageLog.guardian_id == guardian.id).order_by(MessageLog.created_at))
    )
    data = {
        "about": (
            f"Personal data {school.name if school else 'the school'} holds about you and your children, "
            f"as processed by {get_settings().app_name} for the school."
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "school": school.name if school else "",
        "you": {
            "name": guardian.full_name,
            "phone": guardian.phone,
            "email": guardian.email,
            "consent_given_at": guardian.consent_at.isoformat() if guardian.consent_at else None,
            "consent_method": guardian.consent_method,
            "reminders_turned_off_at": guardian.reminders_opt_out_at.isoformat()
            if guardian.reminders_opt_out_at
            else None,
        },
        "children": [
            {
                "name": child.full_name,
                "admission_number": child.admission_number,
                "gender": child.gender,
                "date_of_birth": child.date_of_birth.isoformat() if child.date_of_birth else None,
                "status": child.status,
                "left_on": child.left_on.isoformat() if child.left_on else None,
                "relationship": link.relationship_to_student,
                "main_contact": link.is_primary,
                "classes": history[child.id],
            }
            for link in links
            if (child := children.get(link.student_id)) is not None
        ],
        "bills": [
            {
                "number": inv.number,
                "child": children[inv.student_id].full_name,
                "kind": inv.kind,
                "issued_at": inv.issued_at.isoformat(),
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "voided": inv.voided_at is not None,
                "lines": [{"description": ln.description, "amount": _kobo(ln.amount_kobo)} for ln in lines[inv.id]],
            }
            for inv in invoices
        ],
        "payments": [
            {
                "child": children[payment.student_id].full_name,
                "received_at": payment.received_at.isoformat(),
                "amount": _kobo(payment.amount_kobo),
                "method": payment.method,
                "reference": payment.reference or payment.gateway_reference or "",
                "receipt_number": number,
                "kind": payment.kind,
            }
            for payment, number in payments
        ],
        "messages": [
            {
                "sent_at": (m.sent_at or m.created_at).isoformat(),
                "kind": m.kind,
                "status": m.status,
                "channel": m.channel,
                "text": m.body,
            }
            for m in messages
        ],
    }
    record(
        session,
        AuditAction.PRIVACY_EXPORTED,
        school_id=actor.school_id,
        actor=actor.actor,
        meta=actor.meta,
        target=guardian,
        changes={"children": len(children), "bills": len(invoices), "payments": len(payments)},
    )
    session.commit()
    return data


# --- Erasure ------------------------------------------------------------------------------------------


def _erased_index() -> str:
    """A unique, unusable phone index: nobody can sign in as an erased parent."""
    return f"erased-{secrets.token_hex(16)}"


def erase_guardian(session: Session, actor: Acting, guardian_id: uuid.UUID, reason: str) -> Guardian:
    guardian = get_or_404(session, Guardian, guardian_id, label="Guardian", lock=True)
    if guardian.erased_at is not None:
        raise Conflict("This parent's details were already erased.")
    current = session.scalar(
        select(func.count())
        .select_from(GuardianStudent)
        .join(Student, Student.id == GuardianStudent.student_id)
        .where(GuardianStudent.guardian_id == guardian.id, Student.status == StudentStatus.ACTIVE)
    )
    if current:
        raise Conflict(
            "This parent is still the contact for a current pupil. Add another contact and unlink them, "
            "or record the pupil as having left, first."
        )
    now = datetime.now(UTC)
    guardian.full_name, guardian.phone, guardian.phone_index, guardian.email = ERASED_NAME, "", _erased_index(), None
    guardian.reminders_opt_out_at, guardian.erased_at = guardian.reminders_opt_out_at or now, now
    session.execute(update(MessageLog).where(MessageLog.guardian_id == guardian.id).values(body=""))
    session.execute(
        update(ParentSession)
        .where(ParentSession.guardian_id == guardian.id, ParentSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    record(
        session,
        AuditAction.PRIVACY_ERASED,
        school_id=actor.school_id,
        actor=actor.actor,
        meta=actor.meta,
        target=guardian,
        changes={"who": "parent", "reason": reason.strip()[:300]},
    )
    session.commit()
    return guardian


def erase_student(session: Session, actor: Acting, student_id: uuid.UUID, reason: str) -> Student:
    student = get_or_404(session, Student, student_id, label="Student", lock=True)
    if student.erased_at is not None:
        raise Conflict("This pupil's details were already erased.")
    if student.status == StudentStatus.ACTIVE:
        raise Conflict("Record that the pupil has left before erasing their details.")
    _anonymise_students(session, [student.id])
    record(
        session,
        AuditAction.PRIVACY_ERASED,
        school_id=actor.school_id,
        actor=actor.actor,
        meta=actor.meta,
        target=student,
        changes={"who": "pupil", "admission_number": student.admission_number, "reason": reason.strip()[:300]},
    )
    session.commit()
    session.refresh(student)
    return student


def _anonymise_students(session: Session, student_ids: list[uuid.UUID]) -> None:
    """Three statements for any number of students. The admission number stays (it isn't personal on its own)."""
    if not student_ids:
        return
    now = datetime.now(UTC)
    session.execute(
        update(Student)
        .where(Student.id.in_(student_ids))
        .values(
            first_name=ERASED_NAME, middle_name="", last_name="pupil", gender=None, date_of_birth=None, erased_at=now
        )
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(VirtualAccount)
        .where(VirtualAccount.student_id.in_(student_ids))
        .values(account_name="ERASED", status=VirtualAccountStatus.INACTIVE)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(MessageLog)
        .where(MessageLog.student_id.in_(student_ids))
        .values(body="")
        .execution_options(synchronize_session=False)
    )


# --- Retention ----------------------------------------------------------------------------------------


def apply_retention(today: date | None = None) -> dict[str, int]:
    """Daily, across every school. A fixed number of bulk statements."""
    settings = get_settings()
    today = today or datetime.now(UTC).date()
    with SessionLocal() as session:
        scope_platform_wide(session)
        cleared = session.execute(
            update(MessageLog)
            .where(
                MessageLog.body != "",
                MessageLog.created_at < datetime.now(UTC) - timedelta(days=settings.message_body_retention_days),
            )
            .values(body="")
        ).rowcount  # type: ignore[attr-defined]
        cutoff = today.replace(year=today.year - settings.records_retention_years)
        expired = list(
            session.execute(
                select(Student.id, Student.school_id).where(
                    Student.erased_at.is_(None),
                    Student.status != StudentStatus.ACTIVE,
                    Student.left_on < cutoff,
                )
            ).all()
        )
        _anonymise_students(session, [sid for sid, _ in expired])
        # Parents with no child left on record who isn't erased
        still_needed = exists().where(
            and_(
                GuardianStudent.guardian_id == Guardian.id,
                GuardianStudent.student_id == Student.id,
                Student.erased_at.is_(None),
            )
        )
        now = datetime.now(UTC)
        parents = session.execute(
            update(Guardian)
            .where(
                Guardian.erased_at.is_(None),
                exists().where(GuardianStudent.guardian_id == Guardian.id),
                ~still_needed,
            )
            .values(
                full_name=ERASED_NAME,
                phone="",
                phone_index=func.concat("erased-", func.md5(cast(func.random(), Text))),
                email=None,
                erased_at=now,
            )
            .returning(Guardian.id, Guardian.school_id)
        ).all()
        if parents:
            session.execute(
                update(MessageLog).where(MessageLog.guardian_id.in_([g for g, _ in parents])).values(body="")
            )
        per_school: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: {"pupils": 0, "parents": 0})
        for _, school_id in expired:
            per_school[school_id]["pupils"] += 1
        for _, school_id in parents:
            per_school[school_id]["parents"] += 1
        actor = Actor.system("retention")
        for school_id, counts in per_school.items():  # one audit row each, flushed together
            record(
                session,
                AuditAction.PRIVACY_RETENTION_APPLIED,
                school_id=school_id,
                actor=actor,
                changes={**counts, "left_before": cutoff.isoformat()},
            )
        session.commit()
    return {"message_texts_cleared": int(cleared), "pupils_erased": len(expired), "parents_erased": len(parents)}

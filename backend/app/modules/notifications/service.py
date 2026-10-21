"""
Messages to parents.

Every message goes through the same pipeline:

1. `queue()` inserts MessageLog rows (a repeated `dedupe_key` is ignored, so a
   retry can't message a parent twice) and an outbox task per new row, in the
   caller's transaction. Fee notices and reminders queued during quiet hours
   wait until morning.
2. `deliver()` (background task) re-checks everything at sending time: still
   queued, not too old, parent hasn't opted out, the bill is still owed (for
   reminders), not quiet hours. It renders the message with today's figures
   and tries WhatsApp, then SMS, then email. The outcome, sent or skipped and
   why, is recorded on the row.
3. `schedule_reminders()` (hourly) finds the bills a reminder is due for, for
   every school at once, in a fixed number of queries.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.clock import LAGOS
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.email import OutgoingEmail
from app.core.email import deliver as deliver_email
from app.core.money import format_naira
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.fees.invoicing import account_summaries, amounts_for
from app.modules.fees.models import Invoice, InvoiceKind
from app.modules.fees.plans import plan_phrase, plan_views, progress_cte
from app.modules.payments.family_links import link_url, live_link
from app.modules.payments.models import Payment, VirtualAccount, VirtualAccountStatus
from app.modules.receipts.models import Receipt
from app.modules.receipts.service import verification_url
from app.modules.schools.models import AcademicSession, School, SchoolStatus, Term
from app.modules.students.models import Guardian, GuardianStudent, Student, StudentStatus

from . import templates, voice, whatsapp
from .models import Channel, MessageKind, MessageLog, MessageStatus, MessageTemplate, ReminderSettings
from .sms import Sms, SmsError, send_sms

logger = logging.getLogger(__name__)
DELIVER_TASK = "notifications.deliver"
MAX_ATTEMPTS = 3
CALL_KINDS = (MessageKind.CALL_REMINDER, MessageKind.CALL_RECEIPT)
DEFAULT_SETTINGS = ReminderSettings(
    enabled=True,
    days_before_due=[7, 1],
    days_after_due=[3, 14, 30],
    send_hour=9,
    max_per_invoice=4,
    min_gap_days=3,
    calls_enabled=False,
    max_calls_per_invoice=2,
)


@dataclass(frozen=True)
class NewMessage:
    guardian_id: uuid.UUID
    student_id: uuid.UUID
    kind: MessageKind
    dedupe_key: str
    invoice_id: uuid.UUID | None = None
    payment_id: uuid.UUID | None = None


# --- Quiet hours ------------------------------------------------------------------------------------


def _in_quiet_hours(moment: datetime) -> bool:
    settings = get_settings()
    hour = moment.astimezone(LAGOS).hour
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    return hour >= start or hour < end if start > end else start <= hour < end


def _outside_call_hours(moment: datetime) -> bool:
    settings = get_settings()
    return not settings.call_hours_start <= moment.astimezone(LAGOS).hour < settings.call_hours_end


def next_call_time(moment: datetime) -> datetime:
    """`moment` itself if it's within calling hours in Lagos, else when they next start."""
    if not _outside_call_hours(moment):
        return moment
    local = moment.astimezone(LAGOS)
    start = datetime.combine(local.date(), time(get_settings().call_hours_start), tzinfo=LAGOS)
    if local >= start:
        start += timedelta(days=1)
    return start.astimezone(UTC)


def next_daytime(moment: datetime) -> datetime:
    """`moment` itself if it's daytime in Lagos, else the end of the quiet hours."""
    if not _in_quiet_hours(moment):
        return moment
    local = moment.astimezone(LAGOS)
    morning = datetime.combine(local.date(), time(get_settings().quiet_hours_end), tzinfo=LAGOS)
    if local >= morning:
        morning += timedelta(days=1)
    return morning.astimezone(UTC)


# --- Queueing ---------------------------------------------------------------------------------------


def queue(session: Session, school_id: uuid.UUID, messages: list[NewMessage]) -> int:
    """Add messages to the caller's transaction. Returns how many were new. One insert for any number."""
    return _queue_many(session, [(school_id, m) for m in messages])


def _queue_many(session: Session, messages: list[tuple[uuid.UUID, NewMessage]]) -> int:
    if not messages:
        return 0
    rows = [
        {
            "id": uuid.uuid4(),
            "school_id": school_id,
            "guardian_id": m.guardian_id,
            "student_id": m.student_id,
            "invoice_id": m.invoice_id,
            "payment_id": m.payment_id,
            "kind": m.kind,
            "dedupe_key": m.dedupe_key[:120],
        }
        for school_id, m in messages
    ]
    inserted = session.execute(
        insert(MessageLog)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["school_id", "dedupe_key"])
        .returning(MessageLog.id, MessageLog.school_id, MessageLog.kind)
    ).all()
    now = datetime.now(UTC)
    for message_id, school_id, kind in inserted:
        not_before = (
            None if kind == MessageKind.RECEIPT else next_call_time(now) if kind in CALL_KINDS else next_daytime(now)
        )
        outbox.enqueue(
            session,
            DELIVER_TASK,
            {"school_id": str(school_id), "message_id": str(message_id)},
            not_before=not_before if not_before and not_before > now else None,
        )
    return len(inserted)


def _primary_guardians(session: Session, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not student_ids:
        return {}
    return dict(
        session.execute(
            select(GuardianStudent.student_id, GuardianStudent.guardian_id).where(
                GuardianStudent.student_id.in_(student_ids), GuardianStudent.is_primary.is_(True)
            )
        ).all()
    )


def _callers(session: Session, school_id: uuid.UUID, guardian_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Those of these parents who get phone calls: they prefer them and the school has calls switched on."""
    calls_on = session.scalar(select(ReminderSettings.calls_enabled).where(ReminderSettings.school_id == school_id))
    if not calls_on or not guardian_ids:
        return set()
    return set(
        session.scalars(
            select(Guardian.id).where(
                Guardian.id.in_(guardian_ids), Guardian.prefers_calls.is_(True), Guardian.erased_at.is_(None)
            )
        )
    )


def queue_receipts(session: Session, school_id: uuid.UUID, payments: list[Payment]) -> int:
    """
    A receipt message to each payment's primary parent, and a confirmation call to those who
    prefer calls. A fixed number of statements for any number of payments.
    """
    guardians = _primary_guardians(session, list({p.student_id for p in payments}))
    callers = _callers(session, school_id, set(guardians.values()))
    messages = []
    for p in payments:
        guardian = guardians.get(p.student_id)
        if guardian is None:
            continue
        messages.append(NewMessage(guardian, p.student_id, MessageKind.RECEIPT, f"receipt:{p.id}", payment_id=p.id))
        if guardian in callers:
            messages.append(
                NewMessage(guardian, p.student_id, MessageKind.CALL_RECEIPT, f"call-receipt:{p.id}", payment_id=p.id)
            )
    return queue(session, school_id, messages)


def queue_fee_notices(session: Session, school_id: uuid.UUID, invoices: list[Invoice]) -> int:
    guardians = _primary_guardians(session, [i.student_id for i in invoices])
    return queue(
        session,
        school_id,
        [
            NewMessage(guardians[i.student_id], i.student_id, MessageKind.FEE_NOTICE, f"notice:{i.id}", invoice_id=i.id)
            for i in invoices
            if i.student_id in guardians
        ],
    )


# --- Reminders --------------------------------------------------------------------------------------


def schedule_reminders(now: datetime | None = None) -> int:
    """
    Queue today's reminders for every school whose sending hour it is. A fixed
    number of queries however many schools and bills: candidates, guardians,
    past reminders, then one insert.
    """
    now = now or datetime.now(UTC)
    local = now.astimezone(LAGOS)
    today = local.date()
    with SessionLocal() as session:
        scope_platform_wide(session)
        settings_rows = {s.school_id: s for s in session.scalars(select(ReminderSettings))}
        schools = [
            sid
            for sid in session.scalars(select(School.id).where(School.status == SchoolStatus.ACTIVE))
            if _settings_for(settings_rows, sid).enabled and _settings_for(settings_rows, sid).send_hour == local.hour
        ]
        if not schools:
            return 0
        # A bill on a payment plan is reminded about around its next unpaid instalment
        plan = progress_cte(today)
        due_on = func.coalesce(plan.c.next_due, Invoice.due_date)
        candidates = session.execute(
            select(Invoice.id, Invoice.school_id, Invoice.student_id, due_on)
            .join(Student, Student.id == Invoice.student_id)
            .outerjoin(plan, plan.c.invoice_id == Invoice.id)
            .where(
                Invoice.school_id.in_(schools),
                Invoice.voided_at.is_(None),
                due_on.is_not(None),
                Invoice.kind != InvoiceKind.OPENING_BALANCE,
                Student.status == StudentStatus.ACTIVE,
            )
        ).all()
        due: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []
        overdue_ids: set[uuid.UUID] = set()
        for invoice_id, school_id, student_id, due_date in candidates:
            if due_date is None:  # filtered in SQL; keeps the type checker honest
                continue
            rules = _settings_for(settings_rows, school_id)
            offset = (today - due_date).days
            if (-offset in rules.days_before_due) or (offset in rules.days_after_due):
                due.append((invoice_id, school_id, student_id))
                if offset > 0:
                    overdue_ids.add(invoice_id)
        if not due:
            return 0
        owed = {inv: amounts.balance_kobo for inv, amounts in amounts_for(session, [d[0] for d in due]).items()}
        history = {
            inv: (count, last)
            for inv, count, last in session.execute(
                select(MessageLog.invoice_id, func.count(), func.max(MessageLog.created_at))
                .where(MessageLog.invoice_id.in_([d[0] for d in due]), MessageLog.kind == MessageKind.REMINDER)
                .group_by(MessageLog.invoice_id)
            ).all()
        }
        guardians = {
            student_id: (guardian_id, prefers_calls)
            for student_id, guardian_id, prefers_calls in session.execute(
                select(GuardianStudent.student_id, GuardianStudent.guardian_id, Guardian.prefers_calls)
                .join(Guardian, Guardian.id == GuardianStudent.guardian_id)
                .where(
                    GuardianStudent.student_id.in_({d[2] for d in due}),
                    GuardianStudent.is_primary.is_(True),
                    Guardian.reminders_opt_out_at.is_(None),
                )
            )
        }
        calls_made = dict(
            session.execute(
                select(MessageLog.invoice_id, func.count())
                .where(MessageLog.invoice_id.in_([d[0] for d in due]), MessageLog.kind == MessageKind.CALL_REMINDER)
                .group_by(MessageLog.invoice_id)
            ).all()
        )
        reminders: list[tuple[uuid.UUID, NewMessage]] = []
        for invoice_id, school_id, student_id in due:
            rules = _settings_for(settings_rows, school_id)
            count, last = history.get(invoice_id, (0, None))
            if owed.get(invoice_id, 0) <= 0 or student_id not in guardians or count >= rules.max_per_invoice:
                continue
            if last is not None and now - last < timedelta(days=rules.min_gap_days):
                continue
            guardian_id, prefers_calls = guardians[student_id]
            key = f"{invoice_id}:{today.isoformat()}"
            reminders.append(
                (
                    school_id,
                    NewMessage(guardian_id, student_id, MessageKind.REMINDER, f"reminder:{key}", invoice_id=invoice_id),
                )
            )
            # Overdue bills only, and only for parents who prefer calls at schools that have them on
            calls_left = calls_made.get(invoice_id, 0) < rules.max_calls_per_invoice
            if prefers_calls and invoice_id in overdue_ids and rules.calls_enabled and calls_left:
                reminders.append(
                    (
                        school_id,
                        NewMessage(
                            guardian_id, student_id, MessageKind.CALL_REMINDER, f"call:{key}", invoice_id=invoice_id
                        ),
                    )
                )
        queued = _queue_many(session, reminders)
        session.commit()
    return queued


def _settings_for(rows: dict[uuid.UUID, ReminderSettings], school_id: uuid.UUID) -> ReminderSettings:
    return rows.get(school_id, DEFAULT_SETTINGS)


# --- Delivery ---------------------------------------------------------------------------------------


def _skip(message: MessageLog, reason: str) -> MessageStatus:
    message.status, message.skip_reason = MessageStatus.SKIPPED, reason
    return MessageStatus.SKIPPED


def _values(session: Session, message: MessageLog, guardian: Guardian, student: Student) -> dict[str, str]:
    settings = get_settings()
    school = session.get(School, message.school_id)
    owed = account_summaries(session, [student.id])[student.id].outstanding_kobo
    account = session.scalar(
        select(VirtualAccount).where(
            VirtualAccount.student_id == student.id, VirtualAccount.status == VirtualAccountStatus.ACTIVE
        )
    )
    values = {
        "parent_name": guardian.full_name,
        "student_name": student.full_name,
        "school_name": school.name if school else "",
        "balance": format_naira(owed),
        "portal_url": f"{settings.frontend_url}/parent",
        # Made on first use, then the same link in every message until it expires (committed with the send)
        "pay_link": link_url(live_link(session, guardian).token)
        if message.kind in (MessageKind.FEE_NOTICE, MessageKind.REMINDER)
        else "",
        "pay_to": f"Pay by transfer to {account.account_number} ({account.bank_name}), {student.first_name}'s own "
        "account number."
        if account
        else "",
    }
    if message.payment_id:
        payment = session.get(Payment, message.payment_id)
        receipt = session.scalar(select(Receipt).where(Receipt.payment_id == message.payment_id))
        values["amount"] = format_naira(payment.amount_kobo) if payment else ""
        if receipt:
            values["receipt_number"] = str(receipt.number)
            values["verify_url"] = verification_url(receipt.verification_code)
    if message.invoice_id:
        invoice = session.get(Invoice, message.invoice_id)
        if invoice is not None:
            amounts = amounts_for(session, [invoice.id])[invoice.id]
            values["amount"] = format_naira(amounts.total_kobo)
            if invoice.term_id:
                term_row = session.execute(
                    select(AcademicSession.name, Term.number)
                    .join(Term, Term.session_id == AcademicSession.id)
                    .where(Term.id == invoice.term_id)
                ).first()
                if term_row:
                    values["term"] = f"{term_row[0]} {('first', 'second', 'third')[term_row[1] - 1]} term"
            if invoice.due_date:
                values["due_date"] = f"{invoice.due_date.day} {invoice.due_date.strftime('%b %Y')}"
                past = invoice.due_date < datetime.now(LAGOS).date()
                values["due_phrase"] = f" ({'was due' if past else 'due'} {values['due_date']})"
            plan = plan_views(session, [invoice.id]).get(invoice.id)
            if plan and plan.next_due and message.kind == MessageKind.REMINDER:
                # Ask for the next instalment, not the whole balance
                values["balance"] = format_naira(plan.next_amount_kobo)
                values["due_date"] = f"{plan.next_due.day} {plan.next_due:%b %Y}"
                values["due_phrase"] = plan_phrase(plan)
    return values


def deliver(school_id: uuid.UUID, message_id: uuid.UUID) -> MessageStatus:
    """Send one message, re-checking at sending time whether it should still go."""
    settings = get_settings()
    with SessionLocal() as session:
        scope_to_school(session, school_id)
        message = session.scalar(
            select(MessageLog).where(MessageLog.id == message_id).with_for_update(skip_locked=True)
        )
        if message is None or message.status != MessageStatus.QUEUED:
            return message.status if message else MessageStatus.SKIPPED
        now = datetime.now(UTC)
        if now - message.created_at > timedelta(hours=settings.message_max_age_hours):
            status = _skip(message, "too_old")
            session.commit()
            return status
        guardian = session.get(Guardian, message.guardian_id)
        student = session.get(Student, message.student_id) if message.student_id else None
        if guardian is None or student is None:
            status = _skip(message, "no_recipient")
            session.commit()
            return status
        if message.kind in CALL_KINDS:
            status = _deliver_call(session, message, guardian, student, now)
            session.commit()
            return status
        if message.kind != MessageKind.RECEIPT:
            if guardian.reminders_opt_out_at is not None:
                status = _skip(message, "opted_out")
                session.commit()
                return status
            if message.kind == MessageKind.REMINDER and _nothing_owed(session, message, student.id):
                status = _skip(message, "paid")
                session.commit()
                return status
            if _in_quiet_hours(now):  # requeue for the morning
                outbox.enqueue(
                    session,
                    DELIVER_TASK,
                    {"school_id": str(school_id), "message_id": str(message_id)},
                    not_before=next_daytime(now),
                )
                session.commit()
                return MessageStatus.QUEUED
        values = _values(session, message, guardian, student)
        custom = session.scalar(select(MessageTemplate.body).where(MessageTemplate.kind == message.kind))
        body = templates.render(custom or templates.DEFAULTS[message.kind], values)
        message.body, message.attempts = body, message.attempts + 1
        channel, provider_id, errors = _send(message.kind, guardian, values, body)
        if channel is not None:
            message.status, message.channel, message.sent_at = MessageStatus.SENT, channel, now
            message.provider_message_id, message.error = provider_id[:100], ""
        elif errors == ["no contact details"]:
            _skip(message, "no_contact")
        else:
            message.error = "; ".join(errors)[:300]
            if message.attempts >= MAX_ATTEMPTS:
                message.status = MessageStatus.FAILED
                logger.warning("Message %s failed on every channel: %s", message.id, message.error)
        session.commit()
        return message.status


def _call_lines(session: Session, message: MessageLog, guardian: Guardian, student: Student) -> list[str]:
    """What the call says, from the current figures."""
    school = session.get(School, message.school_id)
    owed = account_summaries(session, [student.id])[student.id].outstanding_kobo
    if message.kind == MessageKind.CALL_RECEIPT:
        payment = session.get(Payment, message.payment_id) if message.payment_id else None
        return voice.receipt_lines(
            guardian.language,
            school=school.name if school else "your school",
            child=student.first_name,
            paid_kobo=payment.amount_kobo if payment else 0,
            owed_kobo=owed,
        )
    account = session.scalar(
        select(VirtualAccount).where(
            VirtualAccount.student_id == student.id, VirtualAccount.status == VirtualAccountStatus.ACTIVE
        )
    )
    return voice.reminder_lines(
        guardian.language,
        school=school.name if school else "your school",
        child=student.first_name,
        owed_kobo=owed,
        account_number=account.account_number if account else None,
        bank=account.bank_name if account else None,
    )


def _deliver_call(
    session: Session, message: MessageLog, guardian: Guardian, student: Student, now: datetime
) -> MessageStatus:
    """Place one call, re-checking that the parent still wants calls, the school still has them on, and it's owed."""
    calls_on = session.scalar(select(ReminderSettings.calls_enabled))
    if not guardian.prefers_calls or not calls_on or not guardian.phone:
        return _skip(message, "calls_off")
    if message.kind == MessageKind.CALL_REMINDER:
        if guardian.reminders_opt_out_at is not None:
            return _skip(message, "opted_out")
        if _nothing_owed(session, message, student.id):
            return _skip(message, "paid")
    if _outside_call_hours(now):
        outbox.enqueue(
            session,
            DELIVER_TASK,
            {"school_id": str(message.school_id), "message_id": str(message.id)},
            not_before=next_call_time(now),
        )
        return MessageStatus.QUEUED
    lines = _call_lines(session, message, guardian, student)
    message.body, message.attempts = " ".join(lines), message.attempts + 1
    try:
        provider_id = voice.place_call(voice.Call(to=guardian.phone, lines=lines))
    except voice.VoiceError as e:
        message.error = f"voice: {e}"[:300]
        if message.attempts >= MAX_ATTEMPTS:
            message.status = MessageStatus.FAILED
            logger.warning("Call %s failed: %s", message.id, message.error)
        return message.status
    message.status, message.channel, message.sent_at = MessageStatus.SENT, Channel.VOICE, now
    message.provider_message_id, message.error = provider_id[:100], ""
    return message.status


def answer_call(session_id: str, destination: str) -> str | None:
    """
    The provider asks what to say on a call we placed (the parent answered). None for a
    call we don't know, or one whose number isn't the parent's.
    """
    if not session_id:
        return None
    with SessionLocal() as lookup:
        scope_platform_wide(lookup)
        school_id = lookup.scalar(
            select(MessageLog.school_id).where(
                MessageLog.provider_message_id == session_id[:100], MessageLog.channel == Channel.VOICE
            )
        )
    if school_id is None:
        return None
    with SessionLocal() as session:
        scope_to_school(session, school_id)
        message = session.scalar(select(MessageLog).where(MessageLog.provider_message_id == session_id[:100]))
        guardian = session.get(Guardian, message.guardian_id) if message else None
        student = session.get(Student, message.student_id) if message and message.student_id else None
        if message is None or guardian is None or student is None or guardian.phone != destination:
            return None
        return voice.call_xml(_call_lines(session, message, guardian, student))


def call_ended(session_id: str, status: str, seconds: int) -> None:
    """The provider's last word on a call: note it if the parent didn't pick up (the text still went)."""
    if not session_id or status.lower() in {"success", "completed"}:
        return
    with SessionLocal() as session:
        scope_platform_wide(session)
        message = session.scalar(
            select(MessageLog).where(
                MessageLog.provider_message_id == session_id[:100], MessageLog.channel == Channel.VOICE
            )
        )
        if message is not None:
            message.error = f"Call not completed ({status[:40]}, {seconds}s)"
            session.commit()


def _nothing_owed(session: Session, message: MessageLog, student_id: uuid.UUID) -> bool:
    """For a reminder about one bill, that bill; for a manual reminder, the child's whole account."""
    if message.invoice_id is None:
        return account_summaries(session, [student_id])[student_id].outstanding_kobo <= 0
    invoice = session.get(Invoice, message.invoice_id)
    return (
        invoice is None
        or invoice.voided_at is not None
        or amounts_for(session, [invoice.id])[invoice.id].balance_kobo <= 0
    )


def _send(
    kind: MessageKind, guardian: Guardian, values: dict[str, str], body: str
) -> tuple[Channel | None, str, list[str]]:
    """Try WhatsApp, then SMS, then email. Returns (channel used or None, provider id, errors)."""
    settings = get_settings()
    errors: list[str] = []
    if guardian.phone and whatsapp.enabled() and kind.value in settings.whatsapp_templates:
        try:
            provider_id = whatsapp.send_template(
                whatsapp.WhatsAppTemplateMessage(
                    to=guardian.phone,
                    template=settings.whatsapp_templates[kind.value],
                    parameters=[values.get(name, "") for name in templates.WHATSAPP_PARAMETERS[kind]],
                )
            )
            return Channel.WHATSAPP, provider_id, errors
        except whatsapp.WhatsAppError as e:
            errors.append(f"whatsapp: {e}")
    if guardian.phone:
        try:
            send_sms(Sms(to=guardian.phone, text=body))
            return Channel.SMS, "", errors
        except SmsError as e:
            errors.append(f"sms: {e}")
    if guardian.email:
        subject = {
            MessageKind.RECEIPT: "Payment received",
            MessageKind.FEE_NOTICE: "School fees",
            MessageKind.REMINDER: "School fees reminder",
        }[kind]
        try:
            deliver_email(
                OutgoingEmail(to=guardian.email, subject=f"{subject}: {values.get('student_name', '')}", body=body)
            )
            return Channel.EMAIL, "", errors
        except OSError as e:
            errors.append(f"email: {type(e).__name__}")
    return None, "", errors or ["no contact details"]


def retry_unsent(older_than: timedelta = timedelta(minutes=10)) -> int:
    """Hourly: messages that failed on every channel last time get another go (up to MAX_ATTEMPTS)."""
    with SessionLocal() as session:
        scope_platform_wide(session)
        rows = session.execute(
            select(MessageLog.id, MessageLog.school_id)
            .where(
                MessageLog.status == MessageStatus.QUEUED,
                MessageLog.attempts > 0,
                MessageLog.attempts < MAX_ATTEMPTS,
                MessageLog.created_at < datetime.now(UTC) - older_than,
            )
            .limit(500)
        ).all()
        for message_id, school_id in rows:
            outbox.enqueue(session, DELIVER_TASK, {"school_id": str(school_id), "message_id": str(message_id)})
        session.commit()
    return len(rows)

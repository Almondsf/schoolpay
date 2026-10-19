"""A school's messaging settings: wording, reminder schedule, parents' opt-outs, and reminders on demand."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError
from app.core.money import format_naira
from app.core.repo import get_or_404
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Acting, record
from app.modules.fees.invoicing import account_summaries
from app.modules.students.models import Guardian, Language, Student

from . import templates
from .models import MessageKind, MessageTemplate, ReminderSettings
from .service import DEFAULT_SETTINGS, NewMessage, _primary_guardians, queue

SAMPLE = {
    "parent_name": "Mrs Ngozi Okafor",
    "student_name": "Chiamaka Okafor",
    "school_name": "Your School",
    "amount": format_naira(15_500_000),
    "balance": format_naira(5_500_000),
    "term": "2026/2027 first term",
    "due_date": "30 Oct 2026",
    "due_phrase": " (due 30 Oct 2026)",
    "receipt_number": "1024",
    "verify_url": "https://app.example/verify/AbC123",
    "portal_url": "https://app.example/parent",
    "pay_link": "https://app.example/pay/f/Xy7…",
    "pay_to": "Pay by transfer to 9930000001 (Wema Bank), Chiamaka's own account number.",
}


@dataclass(frozen=True)
class TemplateView:
    kind: MessageKind
    body: str
    is_custom: bool
    default_body: str


def list_templates(session: Session) -> list[TemplateView]:
    custom = {t.kind: t.body for t in session.scalars(select(MessageTemplate))}
    return [
        TemplateView(kind, custom.get(kind, templates.DEFAULTS[kind]), kind in custom, templates.DEFAULTS[kind])
        for kind in templates.DEFAULTS  # calls have fixed wording
    ]


def save_template(session: Session, actor: Acting, kind: MessageKind, body: str | None) -> TemplateView:
    """Set the school's wording for a kind of message; None goes back to the default."""
    if kind not in templates.DEFAULTS:
        raise DomainError("Phone calls use fixed wording.", field="kind")
    existing = session.scalar(select(MessageTemplate).where(MessageTemplate.kind == kind))
    if body is None:
        session.execute(delete(MessageTemplate).where(MessageTemplate.kind == kind))
    else:
        body = templates.validate(body)
        if existing is None:
            session.add(MessageTemplate(kind=kind, body=body))
        else:
            existing.body = body
    record(
        session,
        AuditAction.MESSAGING_CHANGED,
        school_id=actor.school_id,
        actor=actor.actor,
        meta=actor.meta,
        changes={"template": kind, "reset": body is None},
    )
    session.commit()
    return next(t for t in list_templates(session) if t.kind == kind)


def preview(body: str) -> str:
    return templates.render(templates.validate(body), SAMPLE)


def reminder_settings(session: Session) -> ReminderSettings:
    return session.scalar(select(ReminderSettings)) or DEFAULT_SETTINGS


def save_reminder_settings(
    session: Session,
    actor: Acting,
    *,
    enabled: bool,
    days_before_due: list[int],
    days_after_due: list[int],
    send_hour: int,
    max_per_invoice: int,
    min_gap_days: int,
    calls_enabled: bool,
    max_calls_per_invoice: int,
) -> ReminderSettings:
    if any(not 0 < d <= 90 for d in days_before_due + days_after_due):
        raise DomainError("Days must be between 1 and 90.", field="days_before_due")
    settings = session.scalar(select(ReminderSettings)) or ReminderSettings(id=uuid.uuid4())
    settings.enabled, settings.send_hour = enabled, send_hour
    settings.days_before_due = sorted(set(days_before_due), reverse=True)
    settings.days_after_due = sorted(set(days_after_due))
    settings.max_per_invoice, settings.min_gap_days = max_per_invoice, min_gap_days
    settings.calls_enabled, settings.max_calls_per_invoice = calls_enabled, max_calls_per_invoice
    session.add(settings)
    record(
        session,
        AuditAction.MESSAGING_CHANGED,
        school_id=actor.school_id,
        actor=actor.actor,
        meta=actor.meta,
        changes={
            "reminders": {
                "enabled": enabled,
                "before": settings.days_before_due,
                "after": settings.days_after_due,
                "hour": send_hour,
                "max": max_per_invoice,
                "gap_days": min_gap_days,
                "calls": calls_enabled,
                "max_calls": max_calls_per_invoice,
            }
        },
    )
    session.commit()
    return settings


def set_call_preference(
    session: Session, actor: Acting, guardian_id: uuid.UUID, prefers_calls: bool, language: Language
) -> Guardian:
    """Also phone this parent (overdue reminders, payment confirmations), in their language."""
    guardian = get_or_404(session, Guardian, guardian_id, label="Guardian", lock=True)
    if guardian.erased_at is not None:
        raise DomainError("This parent's details were erased.")
    guardian.prefers_calls, guardian.language = prefers_calls, language
    record(
        session,
        AuditAction.REMINDERS_PREFERENCE_CHANGED,
        school_id=actor.school_id,
        actor=actor.actor,
        meta=actor.meta,
        target=guardian,
        changes={"calls": prefers_calls, "language": language},
    )
    session.commit()
    return guardian


def set_reminders_preference(session: Session, actor: Acting, guardian_id: uuid.UUID, wants: bool) -> Guardian:
    """Opt a parent in or out of fee notices and reminders (receipts always go)."""
    guardian = get_or_404(session, Guardian, guardian_id, label="Guardian", lock=True)
    guardian.reminders_opt_out_at = None if wants else (guardian.reminders_opt_out_at or datetime.now(UTC))
    record(
        session,
        AuditAction.REMINDERS_PREFERENCE_CHANGED,
        school_id=actor.school_id,
        actor=actor.actor,
        meta=actor.meta,
        target=guardian,
        changes={"reminders": wants},
    )
    session.commit()
    return guardian


def remind_now(session: Session, actor: Acting, student_id: uuid.UUID) -> int:
    """A reminder for a student's primary parent right away (once per day per student)."""
    student = get_or_404(session, Student, student_id, label="Student")
    owed = account_summaries(session, [student.id])[student.id].outstanding_kobo
    if owed <= 0:
        raise Conflict("Nothing is owed for this student.")
    guardian_id = _primary_guardians(session, [student.id]).get(student.id)
    if guardian_id is None:
        raise Conflict("This student has no parent contact to remind.")
    if get_or_404(session, Guardian, guardian_id, label="Guardian").reminders_opt_out_at is not None:
        raise Conflict("This parent has turned off fee reminders.")
    queued = queue(
        session,
        actor.school_id,
        [
            NewMessage(
                guardian_id,
                student.id,
                MessageKind.REMINDER,
                f"reminder-manual:{student.id}:{datetime.now(UTC).date().isoformat()}",
            )
        ],
    )
    if not queued:
        raise Conflict("A reminder was already sent to this parent today.")
    record(
        session,
        AuditAction.REMINDER_SENT_MANUALLY,
        school_id=actor.school_id,
        actor=actor.actor,
        meta=actor.meta,
        target=student,
        changes={"owed_kobo": owed},
    )
    session.commit()
    return queued

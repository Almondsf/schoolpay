import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES
from app.core.pagination import Page, PageParams, page_params, paginate_rows
from app.core.ratelimit import get_limiter
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability
from app.modules.parents.deps import ParentContext, get_parent
from app.modules.students.models import Guardian, Student

from . import admin, templates
from .models import Channel, MessageKind, MessageLog, MessageStatus

router = APIRouter(tags=["messaging"], responses=ERROR_RESPONSES)
parent_router = APIRouter(prefix="/parent", tags=["parent"], responses=ERROR_RESPONSES)
MANAGE = Depends(require(Capability.MESSAGING_MANAGE))
Body = Annotated[str, StringConstraints(max_length=templates.MAX_LENGTH + 50)]


class MessageOut(BaseModel):
    id: uuid.UUID
    kind: MessageKind
    status: MessageStatus
    channel: Channel | None
    student_id: uuid.UUID | None
    student_name: str | None
    guardian_name: str
    body: str
    skip_reason: str
    error: str
    created_at: datetime
    sent_at: datetime | None


class TemplateOut(BaseModel):
    kind: MessageKind
    body: str
    is_custom: bool
    default_body: str


class TemplatesOut(BaseModel):
    templates: list[TemplateOut]
    placeholders: dict[str, str]


class TemplateIn(BaseModel):
    body: Body | None = Field(default=None, description="null goes back to the default wording")


class PreviewIn(BaseModel):
    body: Body


class PreviewOut(BaseModel):
    text: str


class ReminderSettingsIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    days_before_due: list[int] = Field(max_length=5)
    days_after_due: list[int] = Field(max_length=6)
    send_hour: int = Field(ge=7, le=19)
    max_per_invoice: int = Field(ge=0, le=10)
    min_gap_days: int = Field(ge=1, le=30)


class PreferenceIn(BaseModel):
    reminders: bool


class PreferenceOut(BaseModel):
    reminders: bool


class QueuedOut(BaseModel):
    queued: int


@router.get("/messages", response_model=Page[MessageOut], summary="Messages to parents, newest first")
def list_messages(
    ctx: StaffContext = MANAGE,
    db: Session = Depends(get_db),
    params: PageParams = Depends(page_params),
    kind: MessageKind | None = None,
    status_filter: MessageStatus | None = Query(default=None, alias="status"),
    student_id: uuid.UUID | None = None,
) -> Page[MessageOut]:
    stmt = (
        select(MessageLog, Guardian.full_name, Student.first_name, Student.last_name)
        .join(Guardian, Guardian.id == MessageLog.guardian_id)
        .outerjoin(Student, Student.id == MessageLog.student_id)
        .order_by(MessageLog.created_at.desc())
    )
    if kind:
        stmt = stmt.where(MessageLog.kind == kind)
    if status_filter:
        stmt = stmt.where(MessageLog.status == status_filter)
    if student_id:
        stmt = stmt.where(MessageLog.student_id == student_id)
    rows, total = paginate_rows(db, stmt, params)
    return Page(
        items=[
            MessageOut(
                id=m.id,
                kind=m.kind,
                status=m.status,
                channel=m.channel,
                student_id=m.student_id,
                student_name=f"{first} {last}" if first else None,
                guardian_name=guardian,
                body=m.body,
                skip_reason=m.skip_reason,
                error=m.error,
                created_at=m.created_at,
                sent_at=m.sent_at,
            )
            for m, guardian, first, last in rows
        ],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/message-templates", response_model=TemplatesOut, summary="Wording of SMS and email messages")
def get_templates(ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> TemplatesOut:
    return TemplatesOut(
        templates=[TemplateOut(**t.__dict__) for t in admin.list_templates(db)], placeholders=templates.PLACEHOLDERS
    )


@router.put("/message-templates/{kind}", response_model=TemplateOut, summary="Change (or reset) the wording")
def put_template(
    kind: MessageKind, body: TemplateIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> TemplateOut:
    return TemplateOut(**admin.save_template(db, ctx, kind, body.body).__dict__)


@router.post("/message-templates/preview", response_model=PreviewOut, summary="See wording with sample details")
def preview(body: PreviewIn, ctx: StaffContext = MANAGE) -> PreviewOut:
    return PreviewOut(text=admin.preview(body.body))


@router.get("/reminder-settings", response_model=ReminderSettingsIO, summary="When fee reminders go out")
def get_reminder_settings(ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> ReminderSettingsIO:
    return ReminderSettingsIO.model_validate(admin.reminder_settings(db))


@router.put("/reminder-settings", response_model=ReminderSettingsIO, summary="Change when fee reminders go out")
def put_reminder_settings(
    body: ReminderSettingsIO, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> ReminderSettingsIO:
    return ReminderSettingsIO.model_validate(admin.save_reminder_settings(db, ctx, **body.model_dump()))


@router.put(
    "/guardians/{guardian_id}/reminders",
    response_model=PreferenceOut,
    summary="Turn a parent's fee notices and reminders on or off (receipts always go)",
)
def guardian_preference(
    guardian_id: uuid.UUID,
    body: PreferenceIn,
    ctx: StaffContext = Depends(require(Capability.STUDENTS_MANAGE)),
    db: Session = Depends(get_db),
) -> PreferenceOut:
    guardian = admin.set_reminders_preference(db, ctx, guardian_id, body.reminders)
    return PreferenceOut(reminders=guardian.reminders_opt_out_at is None)


@router.post(
    "/students/{student_id}/remind",
    response_model=QueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send the primary parent a reminder now (at most once a day)",
)
def remind(student_id: uuid.UUID, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> QueuedOut:
    get_limiter().hit("remind:school", "500/hour", str(ctx.school_id))
    return QueuedOut(queued=admin.remind_now(db, ctx, student_id))


@parent_router.get("/preferences", response_model=PreferenceOut, summary="The parent's message preferences")
def get_parent_preferences(ctx: ParentContext = Depends(get_parent), db: Session = Depends(get_db)) -> PreferenceOut:
    guardian = db.get(Guardian, ctx.guardian_id)
    return PreferenceOut(reminders=guardian is not None and guardian.reminders_opt_out_at is None)


@parent_router.put(
    "/preferences",
    response_model=PreferenceOut,
    summary="Stop or restart fee notices and reminders (receipts always come)",
)
def put_parent_preferences(
    body: PreferenceIn, ctx: ParentContext = Depends(get_parent), db: Session = Depends(get_db)
) -> PreferenceOut:
    guardian = admin.set_reminders_preference(db, ctx, ctx.guardian_id, body.reminders)
    return PreferenceOut(reminders=guardian.reminders_opt_out_at is None)

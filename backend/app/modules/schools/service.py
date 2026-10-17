"""Schools: a school's own profile, and platform-admin management of schools."""

import re
import uuid

from sqlalchemy import Select, func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, NotFound
from app.modules.accounts.auth_service import normalize_email, send_password_link
from app.modules.accounts.deps import Principal, StaffContext
from app.modules.accounts.models import AuthSession, Role, User
from app.modules.audit.models import AuditAction
from app.modules.audit.service import changed_fields, record

from .models import School, SchoolStatus

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PROFILE_FIELDS = ("name", "address", "phone", "email")


def current_school(session: Session, ctx: StaffContext) -> School:
    school = session.get(School, ctx.school_id)
    if school is None:  # can't happen while the FK holds; fail loudly if it does
        raise NotFound("School not found.")
    return school


def update_profile(session: Session, ctx: StaffContext, changes: dict[str, str]) -> School:
    school = current_school(session, ctx)
    before = {f: getattr(school, f) for f in PROFILE_FIELDS}
    for field, value in changes.items():
        if field in PROFILE_FIELDS:
            setattr(school, field, value.strip())
    diff = changed_fields(before, {f: getattr(school, f) for f in PROFILE_FIELDS})
    if diff:
        record(
            session,
            AuditAction.SCHOOL_UPDATED,
            school_id=school.id,
            actor=ctx.actor,
            meta=ctx.meta,
            target=school,
            changes=diff,
        )
    session.commit()
    return school


# --- Platform admin ------------------------------------------------------------------------------


def create_school(
    session: Session, admin: Principal, *, name: str, slug: str, proprietor_email: str, proprietor_name: str
) -> tuple[School, User]:
    """Create a school and invite its first proprietor (who sets a password from the emailed link)."""
    slug = slug.strip().lower()
    if not SLUG.match(slug):
        raise DomainError("Use lowercase letters, numbers and single hyphens.", field="slug")
    email = normalize_email(proprietor_email)
    if session.scalar(select(School.id).where(School.slug == slug)) is not None:
        raise Conflict("That short name is taken.", field="slug")
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise Conflict("Someone already has an account with that email.", field="proprietor_email")
    school = School(id=uuid.uuid4(), name=name.strip(), slug=slug)
    proprietor = User(
        id=uuid.uuid4(), school_id=school.id, email=email, full_name=proprietor_name.strip(), role=Role.PROPRIETOR
    )
    session.add(school)
    session.flush()
    session.add(proprietor)
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise Conflict("That school or email already exists.") from e
    send_password_link(session, proprietor, "invite")
    record(
        session,
        AuditAction.SCHOOL_CREATED,
        school_id=None,
        actor=admin.actor,
        meta=admin.meta,
        target=school,
        changes={"name": school.name, "slug": slug},
    )
    record(
        session,
        AuditAction.SCHOOL_CREATED,
        school_id=school.id,
        actor=admin.actor,
        meta=admin.meta,
        target=school,
        changes={"name": school.name, "slug": slug, "proprietor": email},
    )
    session.commit()
    return school, proprietor


def list_schools(search: str | None = None) -> Select[School]:
    stmt = select(School).order_by(School.name)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(or_(School.name.ilike(pattern), School.slug.ilike(pattern)))
    return stmt


def set_status(session: Session, admin: Principal, school_id: uuid.UUID, status: SchoolStatus, reason: str) -> School:
    school = session.get(School, school_id, with_for_update=True)
    if school is None:
        raise NotFound("School not found.")
    if school.status == status:
        return school
    school.status = status
    if status == SchoolStatus.SUSPENDED:
        session.execute(
            sa_update(AuthSession)
            .where(
                AuthSession.user_id.in_(select(User.id).where(User.school_id == school.id)),
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=func.now(), revoke_reason="school_suspended")
        )
    action = AuditAction.SCHOOL_SUSPENDED if status == SchoolStatus.SUSPENDED else AuditAction.SCHOOL_REACTIVATED
    for target_school in (None, school.id):
        record(
            session,
            action,
            school_id=target_school,
            actor=admin.actor,
            meta=admin.meta,
            target=school,
            changes={"reason": reason},
        )
    session.commit()
    return school

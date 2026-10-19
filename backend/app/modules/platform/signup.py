"""
Schools signing themselves up.

`POST /public/signup` creates the school as **pending** and its proprietor
without a password, then emails the proprietor a link to choose one. Choosing
it proves the email address and makes the school **active** (see
`accounts.auth_service.complete_password_link`). Until then nobody can sign
in to the school.

The answer is the same whether or not the email already has an account (an
existing user gets a "you already have an account" email instead), so signup
can't be used to find out who uses SchoolPay. Requests are rate limited per
IP, and the form has a honeypot field bots fill in.

Pending schools whose proprietor never confirms are deleted after
`PENDING_SCHOOL_DAYS` (`prune_pending`): they hold nothing but the form's
details, and keeping them would keep personal data for no purpose.
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.http import RequestMeta
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.auth_service import normalize_email, send_password_link
from app.modules.accounts.models import Role, User
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record
from app.modules.schools.models import School, SchoolOrigin, SchoolStatus

EXISTING_ACCOUNT_TASK = "platform.signup_existing_account"


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:50].strip("-") or "school"


def _free_slug(session: Session, base: str) -> str:
    """`base`, or `base-2`, `base-3`... whichever is free. One query."""
    taken = set(session.scalars(select(School.slug).where(School.slug.like(f"{base}%"))))
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def sign_up(
    session: Session,
    meta: RequestMeta,
    *,
    school_name: str,
    proprietor_name: str,
    email: str,
    phone: str,
    address: str,
) -> None:
    email = normalize_email(email)
    actor = Actor.system("signup")
    existing = session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        outbox.enqueue(session, EXISTING_ACCOUNT_TASK, {"user_id": str(existing)})
        record(
            session,
            AuditAction.SCHOOL_SIGNED_UP,
            school_id=None,
            actor=actor,
            meta=meta,
            changes={"school": school_name.strip(), "email": _mask(email), "outcome": "email already registered"},
        )
        session.commit()
        return
    school = School(
        id=uuid.uuid4(),
        name=school_name.strip(),
        slug=_free_slug(session, slugify(school_name)),
        status=SchoolStatus.PENDING,
        origin=SchoolOrigin.SIGNUP,
        phone=phone.strip(),
        address=address.strip(),
        email=email,
    )
    proprietor = User(
        id=uuid.uuid4(), school_id=school.id, email=email, full_name=proprietor_name.strip(), role=Role.PROPRIETOR
    )
    session.add(school)
    session.flush()
    session.add(proprietor)
    session.flush()
    send_password_link(session, proprietor, "signup")
    # Only the platform's trail for now: the school's own trail starts when it's confirmed (a pending school
    # that's never confirmed is deleted, and school audit entries can't be)
    record(
        session,
        AuditAction.SCHOOL_SIGNED_UP,
        school_id=None,
        actor=actor,
        meta=meta,
        target=school,
        changes={"school": school.name, "slug": school.slug, "email": _mask(email), "outcome": "pending"},
    )
    session.commit()


def activate_if_pending(session: Session, user: User) -> None:
    """Called when a proprietor first sets their password: a pending school becomes active."""
    if user.school_id is None or user.role is not Role.PROPRIETOR:
        return
    school = session.get(School, user.school_id, with_for_update=True)
    if school is None or school.status != SchoolStatus.PENDING:
        return
    school.status = SchoolStatus.ACTIVE
    actor = Actor(user_id=user.id, label=user.email)
    for target_school in (None, school.id):
        record(
            session,
            AuditAction.SCHOOL_ACTIVATED,
            school_id=target_school,
            actor=actor,
            target=school,
            changes={"name": school.name, "slug": school.slug, "origin": school.origin},
        )


def prune_pending(now: datetime | None = None) -> int:
    """Delete signed-up schools nobody confirmed within PENDING_SCHOOL_DAYS. Three statements."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=get_settings().pending_school_days)
    with SessionLocal() as session:
        scope_platform_wide(session)
        stale = select(School.id).where(School.status == SchoolStatus.PENDING, School.created_at < cutoff)
        session.execute(delete(User).where(User.school_id.in_(stale), User.password_hash.is_(None)))
        removed = session.execute(
            delete(School)
            .where(School.id.in_(stale), ~School.id.in_(select(User.school_id).where(User.school_id.is_not(None))))
            .returning(School.id)
        ).all()
        session.commit()
    return len(removed)

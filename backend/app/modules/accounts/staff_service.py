"""
Managing a school's staff accounts.

`User` isn't a TenantMixin model (sign-in must find users before knowing the
school), so every query here is pinned to `ctx.school_id` explicitly via
`_staff_query`. Tests cover cross-school access for each operation.
"""

import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, NotFound
from app.modules.audit.models import AuditAction
from app.modules.audit.service import changed_fields, record

from . import mfa
from .auth_service import normalize_email, revoke_sessions, send_password_link
from .deps import StaffContext
from .models import STAFF_ROLES, Role, User


class SelfActionNotAllowed(Conflict):
    code = "self_action_not_allowed"


def _staff_query(school_id: uuid.UUID) -> Select[User]:
    return select(User).where(User.school_id == school_id)


def list_staff(school_id: uuid.UUID) -> Select[User]:
    return _staff_query(school_id).order_by(User.is_active.desc(), User.full_name)


def get_staff(session: Session, ctx: StaffContext, user_id: uuid.UUID, *, lock: bool = False) -> User:
    stmt = _staff_query(ctx.school_id).where(User.id == user_id)
    user = session.scalar(stmt.with_for_update() if lock else stmt)
    if user is None:
        raise NotFound("Staff member not found.")
    return user


def _check_role(role: Role) -> None:
    if role not in STAFF_ROLES:
        raise DomainError("Choose a school staff role.", field="role")


def invite(session: Session, ctx: StaffContext, *, email: str, full_name: str, role: Role) -> User:
    _check_role(role)
    email = normalize_email(email)
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise Conflict("Someone already has an account with that email.", field="email")
    user = User(id=uuid.uuid4(), school_id=ctx.school_id, email=email, full_name=full_name.strip(), role=role)
    session.add(user)
    try:
        session.flush()
    except IntegrityError as e:  # a concurrent invite for the same email
        session.rollback()
        raise Conflict("Someone already has an account with that email.", field="email") from e
    send_password_link(session, user, "invite")
    record(
        session,
        AuditAction.STAFF_INVITED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=user,
        changes={"email": email, "role": role},
    )
    session.commit()
    return user


def _active_proprietors(session: Session, school_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.school_id == school_id, User.role == Role.PROPRIETOR, User.is_active.is_(True))
        )
        or 0
    )


def _guard_last_proprietor(session: Session, ctx: StaffContext, user: User) -> None:
    if user.role is Role.PROPRIETOR and user.is_active and _active_proprietors(session, ctx.school_id) <= 1:
        raise Conflict("A school needs at least one active proprietor.")


def update(
    session: Session, ctx: StaffContext, user_id: uuid.UUID, *, full_name: str | None, role: Role | None
) -> User:
    user = get_staff(session, ctx, user_id, lock=True)
    before = {"full_name": user.full_name, "role": user.role}
    if role is not None and role != user.role:
        _check_role(role)
        if user.id == ctx.user.id:
            raise SelfActionNotAllowed("You can't change your own role.")
        _guard_last_proprietor(session, ctx, user)
        user.role = role
        # Permissions changed: make them sign in again so tokens carry the new role
        revoke_sessions(session, user.id, "role_changed")
    if full_name is not None:
        user.full_name = full_name.strip()
    changes = changed_fields(before, {"full_name": user.full_name, "role": user.role})
    if changes:
        record(
            session,
            AuditAction.STAFF_UPDATED,
            school_id=ctx.school_id,
            actor=ctx.actor,
            meta=ctx.meta,
            target=user,
            changes=changes,
        )
    session.commit()
    return user


def deactivate(session: Session, ctx: StaffContext, user_id: uuid.UUID) -> User:
    user = get_staff(session, ctx, user_id, lock=True)
    if user.id == ctx.user.id:
        raise SelfActionNotAllowed("You can't deactivate your own account.")
    if not user.is_active:
        return user
    _guard_last_proprietor(session, ctx, user)
    user.is_active = False
    revoke_sessions(session, user.id, "deactivated")
    record(session, AuditAction.STAFF_DEACTIVATED, school_id=ctx.school_id, actor=ctx.actor, meta=ctx.meta, target=user)
    session.commit()
    return user


def reactivate(session: Session, ctx: StaffContext, user_id: uuid.UUID) -> User:
    user = get_staff(session, ctx, user_id, lock=True)
    if user.is_active:
        return user
    user.is_active = True
    record(session, AuditAction.STAFF_REACTIVATED, school_id=ctx.school_id, actor=ctx.actor, meta=ctx.meta, target=user)
    session.commit()
    return user


def reset_mfa(session: Session, ctx: StaffContext, user_id: uuid.UUID) -> User:
    """For a lost phone: turn off someone's two-factor sign-in so they can enrol again."""
    user = get_staff(session, ctx, user_id, lock=True)
    if user.id == ctx.user.id:
        raise SelfActionNotAllowed("Use your recovery codes to reset your own two-factor sign-in.")
    mfa.disable(session, user)
    revoke_sessions(session, user.id, "mfa_reset")
    record(session, AuditAction.STAFF_MFA_RESET, school_id=ctx.school_id, actor=ctx.actor, meta=ctx.meta, target=user)
    session.commit()
    return user


def resend_invite(session: Session, ctx: StaffContext, user_id: uuid.UUID) -> User:
    user = get_staff(session, ctx, user_id)
    if user.password_hash is not None:
        raise Conflict("This person has already set a password. They can use 'Forgot password'.")
    if not user.is_active:
        raise Conflict("Reactivate this account first.")
    send_password_link(session, user, "invite")
    record(
        session,
        AuditAction.STAFF_INVITED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=user,
        changes={"resent": True},
    )
    session.commit()
    return user

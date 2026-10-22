"""
Helping a school's staff when they're locked out and the school can't help itself (owner, 2026-10-22):
typically the proprietor, who is the only one who can manage staff.

- **Send a password link**: the invite link again if they never set a password, else a reset link.
  It goes to their own email, like "Forgot password", so the platform admin never sees a password.
- **Reset two-factor sign-in**: for a lost phone. Their sessions are signed out and they enrol a new
  authenticator at their next sign-in (proprietors and bursars must).

Both are recorded in the school's activity log and the platform's trail.
"""

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.modules.accounts import mfa
from app.modules.accounts.auth_service import revoke_sessions, send_password_link
from app.modules.accounts.deps import Principal
from app.modules.accounts.models import STAFF_ROLES, User
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record


def _staff_member(session: Session, school_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = session.scalar(
        select(User)
        .where(User.id == user_id, User.school_id == school_id, User.role.in_(STAFF_ROLES))
        .with_for_update()
    )
    if user is None:
        raise NotFound("Staff member not found.")
    return user


def _audit(session: Session, admin: Principal, action: AuditAction, user: User, changes: dict[str, object]) -> None:
    for school in (None, user.school_id):
        record(session, action, school_id=school, actor=admin.actor, meta=admin.meta, target=user, changes=changes)


def send_link(
    session: Session, admin: Principal, school_id: uuid.UUID, user_id: uuid.UUID
) -> Literal["invite", "reset"]:
    """Email the invite (no password yet) or a reset link. Returns which one went."""
    user = _staff_member(session, school_id, user_id)
    if not user.is_active:
        raise Conflict("This account is deactivated. The school's proprietor can reactivate it.")
    purpose: Literal["invite", "reset"] = "invite" if user.password_hash is None else "reset"
    send_password_link(session, user, purpose)
    _audit(session, admin, AuditAction.PLATFORM_STAFF_LINK_SENT, user, {"purpose": purpose})
    session.commit()
    return purpose


def reset_two_factor(session: Session, admin: Principal, school_id: uuid.UUID, user_id: uuid.UUID) -> None:
    user = _staff_member(session, school_id, user_id)
    if not user.mfa_enabled:
        raise Conflict("Two-factor sign-in isn't on for this account.")
    mfa.disable(session, user)
    revoke_sessions(session, user.id, "mfa_reset")
    _audit(session, admin, AuditAction.STAFF_MFA_RESET, user, {"by": "platform"})
    session.commit()

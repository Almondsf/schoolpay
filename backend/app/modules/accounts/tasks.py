"""Background tasks for accounts. Registered with Celery in app.workers.tasks."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.email import OutgoingEmail, deliver

from .auth_service import password_link_token
from .models import AuthSession, User

# Revoked/expired sessions are kept this long for support questions, then deleted
SESSION_RETENTION = timedelta(days=30)


def _password_email(user: User, purpose: str, link: str) -> OutgoingEmail:
    settings = get_settings()
    hours = settings.password_link_hours
    if purpose == "invite":
        subject = f"You've been invited to {settings.app_name}"
        intro = f"An account has been created for you on {settings.app_name}. Set your password to get started:"
    else:
        subject = f"Reset your {settings.app_name} password"
        intro = "Someone asked to reset the password for your account. If it was you, set a new password here:"
    body = (
        f"Hello {user.full_name},\n\n{intro}\n\n{link}\n\n"
        f"This link works once and expires in {hours} hours. If you didn't expect this email, you can ignore it.\n"
    )
    return OutgoingEmail(to=user.email, subject=subject, body=body)


def send_password_link(user_id: str, purpose: str) -> None:
    """Build the link at send time, so no usable token is ever stored in the outbox."""
    with SessionLocal() as session:
        user = session.get(User, uuid.UUID(user_id))
        if user is None or not user.is_active or purpose not in ("invite", "reset"):
            return
        if purpose == "invite" and user.password_hash is not None:
            return  # already accepted; a retried task must not send a stale invite
        token = password_link_token(user, purpose)
        link = f"{get_settings().frontend_url}/console/set-password#token={token}"
        deliver(_password_email(user, purpose, link))


def prune_sessions(session: Session | None = None) -> int:
    """Delete sessions that ended (expired or revoked) more than SESSION_RETENTION ago."""
    cutoff = datetime.now(UTC) - SESSION_RETENTION
    with session or SessionLocal() as s:
        result = s.execute(
            delete(AuthSession).where(or_(AuthSession.expires_at < cutoff, AuthSession.revoked_at < cutoff))
        )
        s.commit()
        return int(result.rowcount)  # type: ignore[attr-defined]

"""Background tasks for the parent portal. Registered in app.workers.tasks."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.core.db import SessionLocal
from app.core.tenancy import scope_platform_wide
from app.modules.notifications.sms import Sms, send_sms

from .models import ParentOtp, ParentSession


def send_code(to: str, code: str) -> None:
    """Text a sign-in code. Both values arrive encrypted and are only decrypted here, at sending time."""
    minutes = get_settings().otp_minutes
    send_sms(
        Sms(
            to=decrypt(to),
            route="dnd",
            text=f"{decrypt(code)} is your {get_settings().app_name} sign-in code. It expires in {minutes} minutes. "
            "Never share it, not even with school staff.",
        )
    )


def prune() -> int:
    """Delete used or expired codes after a day, and ended parent sessions after 30 days."""
    now = datetime.now(UTC)
    with SessionLocal() as session:
        scope_platform_wide(session)
        codes = len(
            session.execute(
                delete(ParentOtp).where(ParentOtp.created_at < now - timedelta(days=1)).returning(ParentOtp.id)
            ).all()
        )
        sessions = len(
            session.execute(
                delete(ParentSession)
                .where(
                    or_(
                        ParentSession.expires_at < now - timedelta(days=30),
                        ParentSession.revoked_at < now - timedelta(days=30),
                    )
                )
                .returning(ParentSession.id)
            ).all()
        )
        session.commit()
    return codes + sessions

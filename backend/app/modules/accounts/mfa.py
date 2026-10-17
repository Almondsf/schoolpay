"""
TOTP two-factor sign-in (Google Authenticator, Microsoft Authenticator, Authy).

For enrolled users, sign-in has two steps. The password step returns a
short-lived signed MFA ticket instead of tokens. The ticket plus a 6-digit
code (or a recovery code) is then exchanged for tokens.
"""

import hashlib
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings

from .models import RecoveryCode, User
from .security import decode_token, encode_token, password_fingerprint

TICKET_LIFETIME = timedelta(minutes=5)
RECOVERY_CODE_COUNT = 10


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").upper().encode()).hexdigest()


def make_ticket(user: User) -> str:
    return encode_token("mfa", user.id, TICKET_LIFETIME, pwd=password_fingerprint(user.password_hash))


def user_from_ticket(session: Session, ticket: str) -> User | None:
    """The user a ticket was issued to, or None if forged, expired, or the password changed since."""
    claims = decode_token(ticket, "mfa")
    if claims is None:
        return None
    user = session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active or claims.get("pwd") != password_fingerprint(user.password_hash):
        return None
    return user


def start_enrolment(user: User) -> tuple[str, str]:
    """A new secret, inactive until confirmed with a code. Returns (secret, otpauth URI)."""
    user.mfa_secret = pyotp.random_base32()
    user.mfa_enabled = False
    user.mfa_last_step = 0
    uri = pyotp.TOTP(user.mfa_secret).provisioning_uri(name=user.email, issuer_name=get_settings().mfa_issuer)
    return user.mfa_secret, uri


def verify_code(session: Session, user: User, code: str) -> bool:
    """Check a TOTP code, allowing one step of clock drift and never accepting the same step twice."""
    code = (code or "").replace(" ", "")
    if not user.mfa_secret or not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(user.mfa_secret)
    now = time.time()
    for drift in (0, -1, 1):
        at = now + drift * totp.interval
        step = int(at // totp.interval)
        if step > user.mfa_last_step and secrets.compare_digest(totp.at(int(at)), code):
            # "Only if still unused": two simultaneous sign-ins can't both spend one code
            result = session.execute(
                update(User).where(User.id == user.id, User.mfa_last_step < step).values(mfa_last_step=step)
            )
            if result.rowcount == 1:  # type: ignore[attr-defined]
                user.mfa_last_step = step
                return True
            return False
    return False


def use_recovery_code(session: Session, user: User, code: str) -> bool:
    match = session.scalar(
        select(RecoveryCode)
        .where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == _hash_recovery(code),
            RecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if match is None:
        return False
    match.used_at = datetime.now(UTC)
    return True


def regenerate_recovery_codes(session: Session, user: User) -> list[str]:
    session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = [f"{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}" for _ in range(RECOVERY_CODE_COUNT)]
    session.add_all(RecoveryCode(user_id=user.id, code_hash=_hash_recovery(c)) for c in codes)
    return codes


def disable(session: Session, user: User) -> None:
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_last_step = 0
    session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))

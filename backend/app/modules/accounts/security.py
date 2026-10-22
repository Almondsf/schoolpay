"""
Password hashing (argon2), password policy, and signed short-lived tokens.

Every JWT carries a `typ` claim and is decoded for one expected type only, so
an MFA ticket or password-reset link can never be used as an access token.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.core.errors import DomainError

TokenType = Literal["access", "mfa", "password", "parent", "parent_choice"]
ISSUER = "wardpay"
ALGORITHM = "HS256"
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHash.recommended()
# Verified against when the email is unknown, so response time doesn't reveal which emails exist
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

# Passwords people actually pick for school systems; rejected outright
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty123",
        "qwertyuiop",
        "iloveyou",
        "admin1234",
        "welcome1",
        "letmein123",
        "abc12345",
        "wardpay",
        "schoolpay",
        "parentpay",
        "school123",
        "nigeria123",
        "lagos12345",
        "football1",
        "princess1",
    }
)


class WeakPassword(DomainError):
    code = "weak_password"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        _hasher.verify(password, _DUMMY_HASH)
        return False
    return _hasher.verify(password, password_hash)


def check_password_policy(password: str, *, email: str = "", name: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"Use at least {MIN_PASSWORD_LENGTH} characters.", field="new_password")
    if len(password) > 128:
        raise WeakPassword("Use at most 128 characters.", field="new_password")
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS or len(set(lowered)) < 4:
        raise WeakPassword("That password is too easy to guess.", field="new_password")
    local_part = email.split("@")[0].lower()
    if (local_part and len(local_part) >= 4 and local_part in lowered) or (
        name and len(name) >= 4 and name.lower().replace(" ", "") in lowered.replace(" ", "")
    ):
        raise WeakPassword("Don't use your name or email in your password.", field="new_password")


def password_fingerprint(password_hash: str | None) -> str:
    """Changes whenever the password does, which invalidates outstanding MFA tickets and reset links."""
    return hashlib.sha256((password_hash or "unset").encode()).hexdigest()[:16]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def encode_token(typ: TokenType, subject: uuid.UUID, lifetime: timedelta, **claims: Any) -> str:
    now = datetime.now(UTC)
    payload = {
        "typ": typ,
        "sub": str(subject),
        "iss": ISSUER,
        "iat": now,
        "exp": now + lifetime,
        "jti": secrets.token_hex(8),
        **claims,
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_token(token: str, typ: TokenType) -> dict[str, Any] | None:
    """The token's claims if it's genuine, unexpired and of type `typ`; otherwise None."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "typ", "iss"]},
        )
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != typ:
        return None
    try:
        uuid.UUID(claims["sub"])
    except ValueError:
        return None
    return claims

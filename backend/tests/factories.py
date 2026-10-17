"""Test data builders. They write through the given session and flush (no commit needed)."""

import uuid
from itertools import count

import pyotp
from sqlalchemy.orm import Session

from app.core.http import RequestMeta
from app.modules.accounts import auth_service
from app.modules.accounts.models import Role, User
from app.modules.accounts.security import hash_password
from app.modules.schools.models import School

PASSWORD = "correct-horse-battery"
_seq = count(1)


def make_school(db: Session, name: str | None = None) -> School:
    n = next(_seq)
    school = School(id=uuid.uuid4(), name=name or f"Test School {n}", slug=f"school-{n}-{uuid.uuid4().hex[:6]}")
    db.add(school)
    db.flush()
    return school


def make_user(
    db: Session,
    school: School | None,
    role: Role = Role.SCHOOL_ADMIN,
    *,
    email: str | None = None,
    password: str | None = PASSWORD,
    mfa: bool | None = None,
    active: bool = True,
) -> User:
    n = next(_seq)
    user = User(
        id=uuid.uuid4(),
        school_id=school.id if school else None,
        role=role,
        full_name=f"Person {n}",
        email=email or f"user{n}-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password(password) if password else None,
        is_active=active,
    )
    # Roles that must use two-factor sign-in get it by default, so they can act straight away
    if mfa if mfa is not None else role in (Role.PROPRIETOR, Role.BURSAR, Role.PLATFORM_ADMIN):
        user.mfa_secret = pyotp.random_base32()
        user.mfa_enabled = True
    db.add(user)
    db.flush()
    return user


def headers_for(db: Session, user: User) -> dict[str, str]:
    """Bearer headers for a live session, as if the user had signed in."""
    issued = auth_service._issue(db, user, RequestMeta(ip="203.0.113.10"))
    db.commit()
    return {"Authorization": f"Bearer {issued.access_token}"}


def totp_now(user: User) -> str:
    assert user.mfa_secret
    return pyotp.TOTP(user.mfa_secret).now()

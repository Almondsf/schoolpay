"""
Authentication and authorization dependencies, and the one place an HTTP
request's database session gets its tenant scope.

    @router.get("/students")
    def list_students(ctx: StaffContext = Depends(require(Capability.STUDENTS_VIEW)),
                      db: Session = Depends(get_db)): ...

`require()` authenticates the bearer token against a live session, checks
the capability (and MFA enrolment for roles that need it), refuses
suspended schools, then scopes `db` to the caller's school. FastAPI caches
`get_db` per request, so the router's session is the scoped one.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import NotAuthenticated, PermissionDenied
from app.core.http import RequestMeta, request_meta
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.audit.service import Actor
from app.modules.schools.models import School, SchoolStatus

from .auth_service import actor_for, mfa_enrolment_required, scope_for_user
from .models import AuthSession, Role, User
from .permissions import Capability, role_has
from .security import decode_token

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


@dataclass(frozen=True)
class Principal:
    """A signed-in staff member or platform admin, before any capability check."""

    user: User
    session_id: uuid.UUID
    meta: RequestMeta

    @property
    def actor(self) -> Actor:
        return actor_for(self.user)


@dataclass(frozen=True)
class StaffContext(Principal):
    """A principal acting inside their school; the request's DB session is scoped to it."""

    school_id: uuid.UUID


def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Principal:
    claims = decode_token(credentials.credentials, "access") if credentials else None
    if claims is None or "sid" not in claims:
        raise NotAuthenticated("Please sign in.", code="not_authenticated")
    try:
        session_id = uuid.UUID(claims["sid"])
    except ValueError as e:
        raise NotAuthenticated("Please sign in.") from e
    # One query per request: session, user and school status together
    row = db.execute(
        select(AuthSession, User, School.status)
        .join(User, User.id == AuthSession.user_id)
        .outerjoin(School, School.id == User.school_id)
        .where(AuthSession.id == session_id, AuthSession.user_id == uuid.UUID(claims["sub"]))
    ).first()
    if row is None:
        raise NotAuthenticated("Please sign in.", code="session_expired")
    auth_session, user, school_status = row
    if auth_session.revoked_at is not None or auth_session.expires_at <= datetime.now(UTC) or not user.is_active:
        raise NotAuthenticated("Your session has ended. Please sign in again.", code="session_expired")
    if user.school_id is not None and school_status != SchoolStatus.ACTIVE:
        raise PermissionDenied("This school's account is suspended. Please contact support.", code="school_suspended")
    scope_for_user(db, user)
    return Principal(user=user, session_id=session_id, meta=request_meta(request))


def require(capability: Capability) -> Callable[..., StaffContext]:
    """Dependency for school endpoints: the caller must hold `capability` in their own school."""

    def dependency(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> StaffContext:
        user = principal.user
        if mfa_enrolment_required(user):
            raise PermissionDenied("Turn on two-factor sign-in to continue.", code="mfa_enrolment_required")
        if user.school_id is None or not role_has(user.role, capability):
            raise PermissionDenied("You don't have permission to do that.")
        scope_to_school(db, user.school_id)
        return StaffContext(user=user, session_id=principal.session_id, meta=principal.meta, school_id=user.school_id)

    dependency.__name__ = f"require_{capability.value.replace('.', '_')}"
    return dependency


def require_platform_admin(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> Principal:
    user = principal.user
    if user.role is not Role.PLATFORM_ADMIN:
        raise PermissionDenied("You don't have permission to do that.")
    if mfa_enrolment_required(user):
        raise PermissionDenied("Turn on two-factor sign-in to continue.", code="mfa_enrolment_required")
    scope_platform_wide(db)
    return principal

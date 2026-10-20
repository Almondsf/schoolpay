"""
Test data builders.

Staff users and schools aren't tenant tables, so they're written through the
test's own session. School-owned data (calendar, classes, students) is
written through `tenant_writer()`, a platform-wide session on the same test
connection, so the test's `db` session stays free to be scoped however the
test needs.
"""

import uuid
from contextlib import contextmanager
from datetime import date
from itertools import count

import pyotp
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.http import RequestMeta
from app.core.tenancy import scope_platform_wide
from app.modules.accounts import auth_service
from app.modules.accounts.models import Role, User
from app.modules.accounts.permissions import MFA_REQUIRED_ROLES
from app.modules.accounts.security import hash_password
from app.modules.schools.models import AcademicSession, Arm, ClassLevel, School, Term
from app.modules.students.models import Enrollment, Student

PASSWORD = "correct-horse-battery"
_seq = count(1)


@contextmanager
def tenant_writer():
    with SessionLocal() as session:
        scope_platform_wide(session)
        yield session
        session.commit()


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
    if mfa if mfa is not None else role in MFA_REQUIRED_ROLES:
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


def make_calendar(school: School, year: int = 2026, current_term: int = 1) -> tuple[AcademicSession, list[Term]]:
    """A session (Sept-July) with three terms; `current_term` (1-3, or 0 for none) is current."""
    spans = [
        (date(year, 9, 8), date(year, 12, 12)),
        (date(year + 1, 1, 5), date(year + 1, 4, 2)),
        (date(year + 1, 4, 20), date(year + 1, 7, 24)),
    ]
    with tenant_writer() as w:
        session = AcademicSession(
            id=uuid.uuid4(),
            school_id=school.id,
            name=f"{year}/{year + 1}",
            start_date=date(year, 9, 1),
            end_date=date(year + 1, 7, 31),
        )
        terms = [
            Term(
                id=uuid.uuid4(),
                school_id=school.id,
                session_id=session.id,
                number=n,
                start_date=s,
                end_date=e,
                is_current=n == current_term,
            )
            for n, (s, e) in enumerate(spans, start=1)
        ]
        w.add(session)
        w.flush()
        w.add_all(terms)
    return session, terms


def make_levels(school: School, spec: dict[str, list[str]] | None = None, first_rank: int = 1) -> list[ClassLevel]:
    """Class levels in rank order with their arms, e.g. {"JSS 1": ["A", "B"], "JSS 2": ["A"]}."""
    spec = spec or {"JSS 1": ["A", "B"], "JSS 2": ["A", "B"], "JSS 3": ["A"]}
    levels = []
    with tenant_writer() as w:
        for rank, (name, arms) in enumerate(spec.items(), start=first_rank):
            level = ClassLevel(id=uuid.uuid4(), school_id=school.id, name=name, rank=rank)
            level.arms = [Arm(id=uuid.uuid4(), school_id=school.id, class_level_id=level.id, name=a) for a in arms]
            levels.append(level)
        w.add_all(levels)
    return levels


def make_student(
    school: School, term: Term, arm: Arm, *, admission: str | None = None, first: str = "Ada", last: str = "Obi"
) -> Student:
    with tenant_writer() as w:
        student = Student(
            id=uuid.uuid4(),
            school_id=school.id,
            first_name=first,
            last_name=last,
            admission_number=admission or f"ADM{next(_seq):05d}",
        )
        w.add(student)
        w.flush()
        w.add(
            Enrollment(
                school_id=school.id,
                student_id=student.id,
                term_id=term.id,
                arm_id=arm.id,
                class_level_id=arm.class_level_id,
            )
        )
    return student


class QueryCounter:
    """Counts SQL statements run on a connection (to prove bulk operations don't query per row)."""

    def __init__(self, connection):
        self.connection = connection
        self.statements: list[str] = []

    def _count(self, _conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
            self.statements.append(statement)

    def __enter__(self):
        event.listen(self.connection, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *_exc):
        event.remove(self.connection, "before_cursor_execute", self._count)

    @property
    def count(self) -> int:
        return len(self.statements)

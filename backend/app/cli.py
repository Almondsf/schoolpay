"""
Operator commands.

    python -m app.cli create-platform-admin --email ops@example.com --name "Ops Person"

Prints a one-time set-password link for the new admin (there's no one to
email an invite from yet). Treat the link like a password.
"""

import argparse
import sys
import uuid

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.tenancy import scope_platform_wide
from app.models import load_all_models
from app.modules.accounts.auth_service import normalize_email, password_link_token
from app.modules.accounts.models import Role, User
from app.modules.accounts.security import hash_password
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record


def create_platform_admin(email: str, name: str) -> str:
    load_all_models()
    with SessionLocal() as session:
        scope_platform_wide(session)
        try:  # the same rule the API applies at sign-in, or the admin could never log in
            email = normalize_email(TypeAdapter(EmailStr).validate_python(email))
        except ValidationError as e:
            raise SystemExit(f"Not a usable email address: {email}") from e
        if session.scalar(select(User.id).where(User.email == email)) is not None:
            raise SystemExit(f"An account with {email} already exists.")
        user = User(id=uuid.uuid4(), email=email, full_name=name.strip(), role=Role.PLATFORM_ADMIN)
        session.add(user)
        record(
            session,
            AuditAction.STAFF_INVITED,
            school_id=None,
            actor=Actor.system("cli"),
            target=user,
            changes={"email": email, "role": Role.PLATFORM_ADMIN},
        )
        session.commit()
        token = password_link_token(user, "invite")
    return f"{get_settings().frontend_url}/console/set-password#token={token}"


DEMO_PASSWORD = "demo-password-123"  # noqa: S105  (demo data only; refuses to run in production)
DEMO_SLUG = "demo-school"


def seed_demo(mfa_secret: str, reset: bool) -> None:
    """
    A demo school for local development and browser tests. Refuses to run in
    production. Everyone's password is DEMO_PASSWORD; the proprietor, bursar and
    platform admin share `mfa_secret` so tests can generate their codes.
    """
    if get_settings().is_production:
        raise SystemExit("seed-demo never runs in production.")
    load_all_models()
    from app.modules.schools.models import School

    with SessionLocal() as session:
        scope_platform_wide(session)
        if session.scalar(select(School.id).where(School.slug == DEMO_SLUG)) is not None:
            if not reset:
                raise SystemExit("The demo school already exists (use --reset on a throwaway database).")
            _wipe_demo(session)
        school = School(
            id=uuid.uuid4(),
            name="Demo Model College",
            slug=DEMO_SLUG,
            address="12 Allen Avenue, Ikeja, Lagos",
            phone="+234 801 000 0000",
        )
        session.add(school)
        session.flush()
        people = [
            ("proprietor@demo.schoolpay.ng", "Grace Adeyemi", Role.PROPRIETOR, school.id, True),
            ("bursar@demo.schoolpay.ng", "Musa Bello", Role.BURSAR, school.id, True),
            ("admin@demo.schoolpay.ng", "Ada Obi", Role.SCHOOL_ADMIN, school.id, False),
            ("platform@demo.schoolpay.ng", "Platform Ops", Role.PLATFORM_ADMIN, None, True),
        ]
        password_hash = hash_password(DEMO_PASSWORD)
        session.add_all(
            User(
                id=uuid.uuid4(),
                school_id=school_id,
                email=email,
                full_name=name,
                role=role,
                password_hash=password_hash,
                mfa_secret=mfa_secret if mfa else None,
                mfa_enabled=mfa,
            )
            for email, name, role, school_id, mfa in people
        )
        session.commit()


def _wipe_demo(session: Session) -> None:
    """Remove the demo school's users (sessions cascade). Audit rows are append-only and stay."""
    from app.modules.schools.models import School

    demo_ids = select(School.id).where(School.slug == DEMO_SLUG).scalar_subquery()
    session.execute(delete(User).where(or_(User.school_id == demo_ids, User.email == "platform@demo.schoolpay.ng")))
    session.execute(update(School).where(School.slug == DEMO_SLUG).values(slug=f"old-demo-{uuid.uuid4().hex[:8]}"))
    session.flush()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-platform-admin", help="Create a platform admin and print their invite link")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    demo = commands.add_parser("seed-demo", help="Create a demo school (development and browser tests only)")
    demo.add_argument("--mfa-secret", default="JBSWY3DPEHPK3PXP", help="Base32 TOTP secret for MFA users")
    demo.add_argument("--reset", action="store_true", help="Replace an existing demo school's users")
    args = parser.parse_args(argv)
    if args.command == "seed-demo":
        seed_demo(args.mfa_secret, args.reset)
        print(f"Demo school ready. Password for every account: {DEMO_PASSWORD}")
    elif args.command == "create-platform-admin":
        link = create_platform_admin(args.email, args.name)
        sys.stdout.write(f"Platform admin created. Send them this one-time link to set a password:\n{link}\n")


if __name__ == "__main__":
    main()

"""
Operator commands.

    python -m app.cli create-platform-admin --email ops@example.com --name "Ops Person"
    python -m app.cli seed-demo [--reset] [--bare]   (development and browser tests only)

Prints a one-time set-password link for the new admin (there's no one to
email an invite from yet). Treat the link like a password.
"""

import argparse
import sys
import uuid

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

from app import demo
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.tenancy import scope_platform_wide
from app.models import load_all_models
from app.modules.accounts.auth_service import normalize_email, password_link_token
from app.modules.accounts.models import Role, User
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-platform-admin", help="Create a platform admin and print their invite link")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    seed = commands.add_parser("seed-demo", help="Create a demo school (development and browser tests only)")
    seed.add_argument("--mfa-secret", default="JBSWY3DPEHPK3PXP", help="Base32 TOTP secret for MFA users")
    seed.add_argument("--reset", action="store_true", help="Replace an existing demo school")
    seed.add_argument("--bare", action="store_true", help="Only the school and students, no bills or payments yet")
    args = parser.parse_args(argv)
    if args.command == "seed-demo":
        demo.seed_demo(args.mfa_secret, args.reset, bare=args.bare)
        sys.stdout.write(f"Demo school ready. Password for every account: {demo.DEMO_PASSWORD}\n")
    elif args.command == "create-platform-admin":
        link = create_platform_admin(args.email, args.name)
        sys.stdout.write(f"Platform admin created. Send them this one-time link to set a password:\n{link}\n")


if __name__ == "__main__":
    main()

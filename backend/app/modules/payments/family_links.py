"""
A parent's family payment link: making, replacing, ending and resolving it. Kept
apart from the page and checkout (`family.py`) so message sending can put the link
in bills and reminders without depending on the payment code.

The link is a bearer token (whoever has it can see the children's first names,
classes and balances, and pay), so it expires (`FAMILY_LINK_DAYS`), staff can
replace it, and erasing the parent ends it. It's looked up by its keyed hash; the
encrypted copy lets the same link go out in every message until it expires.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import blind_index
from app.core.db import SessionLocal
from app.core.errors import DomainError
from app.core.repo import get_or_404
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.schools.models import School, SchoolStatus
from app.modules.students.models import Guardian

from .models import FamilyPayLink


def link_url(token: str) -> str:
    return f"{get_settings().frontend_url}/pay/f/{token}"


def new_link_row(guardian: Guardian, created_by: uuid.UUID | None) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)  # 192 bits
    return {
        "id": uuid.uuid4(),
        "school_id": guardian.school_id,
        "guardian_id": guardian.id,
        "token_index": blind_index(token),
        "token": token,
        "expires_at": datetime.now(UTC) + timedelta(days=get_settings().family_link_days),
        "created_by": created_by,
    }


def live_link(session: Session, guardian: Guardian) -> FamilyPayLink:
    """
    The parent's current link, made if they have none or theirs has expired. Safe when
    two messages ask at once (one live link per parent, enforced by the database).
    The caller commits.
    """
    now = datetime.now(UTC)
    session.execute(
        update(FamilyPayLink)
        .where(
            FamilyPayLink.guardian_id == guardian.id,
            FamilyPayLink.revoked_at.is_(None),
            FamilyPayLink.expires_at <= now,
        )
        .values(revoked_at=now)
    )
    session.execute(
        insert(FamilyPayLink)
        .values(new_link_row(guardian, None))
        .on_conflict_do_nothing(index_elements=["guardian_id"], index_where=text("revoked_at IS NULL"))
    )
    link = session.scalar(
        select(FamilyPayLink).where(FamilyPayLink.guardian_id == guardian.id, FamilyPayLink.revoked_at.is_(None))
    )
    if link is None:  # inserted or found a moment ago in this transaction
        raise RuntimeError("Family link not visible in this session's school scope.")
    return link


def link_for(session: Session, ctx: StaffContext, guardian_id: uuid.UUID) -> FamilyPayLink:
    """The parent's link for staff to copy (made if needed)."""
    guardian = get_or_404(session, Guardian, guardian_id, label="Parent")
    if guardian.erased_at is not None:
        raise DomainError("This parent's details were erased.")
    link = live_link(session, guardian)
    session.commit()
    return link


def reissue(session: Session, ctx: StaffContext, guardian_id: uuid.UUID) -> FamilyPayLink:
    """Replace the parent's link (e.g. it was shared by mistake): the old one stops working at once."""
    guardian = get_or_404(session, Guardian, guardian_id, label="Parent", lock=True)
    if guardian.erased_at is not None:
        raise DomainError("This parent's details were erased.")
    now = datetime.now(UTC)
    replaced = session.execute(
        update(FamilyPayLink)
        .where(FamilyPayLink.guardian_id == guardian.id, FamilyPayLink.revoked_at.is_(None))
        .values(revoked_at=now)
        .returning(FamilyPayLink.id)
    ).first()
    link = FamilyPayLink(**new_link_row(guardian, ctx.user.id))
    session.add(link)
    record(
        session,
        AuditAction.FAMILY_LINK_ISSUED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=guardian,
        changes={"replaced": replaced is not None, "expires_at": link.expires_at},
    )
    session.commit()
    return link


def revoke_for(session: Session, guardian_ids: list[uuid.UUID]) -> None:
    """End these parents' links (their details are being erased). The caller commits."""
    if guardian_ids:
        session.execute(
            update(FamilyPayLink)
            .where(FamilyPayLink.guardian_id.in_(guardian_ids), FamilyPayLink.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )


@dataclass(frozen=True)
class Resolved:
    school_id: uuid.UUID
    link_id: uuid.UUID
    guardian_id: uuid.UUID


def resolve(token: str) -> Resolved | None:
    """The live link for a token, at a school that's open. None for anything else (expired, replaced, unknown)."""
    if not 20 <= len(token) <= 64:
        return None
    with SessionLocal() as lookup:
        scope_platform_wide(lookup)
        row = lookup.execute(
            select(FamilyPayLink.school_id, FamilyPayLink.id, FamilyPayLink.guardian_id)
            .join(School, School.id == FamilyPayLink.school_id)
            .join(Guardian, Guardian.id == FamilyPayLink.guardian_id)
            .where(
                FamilyPayLink.token_index == blind_index(token),
                FamilyPayLink.revoked_at.is_(None),
                FamilyPayLink.expires_at > datetime.now(UTC),
                School.status == SchoolStatus.ACTIVE,
                Guardian.erased_at.is_(None),
            )
        ).first()
    return Resolved(*row) if row else None

"""
Writing and reading the audit trail.

`record()` adds the entry to the caller's session, so it commits (or rolls
back) together with the change it describes. Values pass through redaction
before storage, so secrets can't end up in the trail by accident.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.http import RequestMeta
from app.core.redaction import redact

from .models import AuditAction, AuditLog, PlatformAuditLog


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID | None
    label: str

    @classmethod
    def system(cls, what: str) -> "Actor":
        return cls(user_id=None, label=f"system:{what}")


class Acting(Protocol):
    """Whoever is acting in a school: staff (StaffContext) or a parent (ParentContext)."""

    @property
    def school_id(self) -> uuid.UUID: ...

    @property
    def actor(self) -> Actor: ...

    @property
    def meta(self) -> RequestMeta: ...


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID | date):  # datetime is a date too
        return value.isoformat() if isinstance(value, date) else str(value)
    if hasattr(value, "value") and isinstance(value.value, str):  # enums
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    return value


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """{"field": {"from": old, "to": new}} for the fields that differ."""
    return {k: {"from": before.get(k), "to": after[k]} for k in after if before.get(k) != after[k]}


def record(
    session: Session,
    action: AuditAction,
    *,
    school_id: uuid.UUID | None,
    actor: Actor | None,
    meta: RequestMeta | None = None,
    target: Any = None,
    changes: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry: to the school's trail when `school_id` is given, otherwise the platform's."""
    meta = meta or RequestMeta()
    values: dict[str, Any] = {
        "actor_user_id": actor.user_id if actor else None,
        "actor_label": (actor.label if actor else "anonymous")[:255],
        "action": action.value,
        "target_type": type(target).__name__ if target is not None else "",
        "target_id": str(getattr(target, "id", "")) if target is not None else "",
        "changes": redact(_jsonable(changes or {})),
        "ip_address": meta.ip,
        "user_agent": meta.user_agent,
        "request_id": meta.request_id,
    }
    if school_id is not None:
        session.add(AuditLog(school_id=school_id, **values))
    else:
        session.add(PlatformAuditLog(**values))


def school_entries(
    *,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> Select[AuditLog]:
    """The school's trail, newest first. School scoping comes from the session."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_user_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    return stmt


def platform_entries(*, action: str | None = None) -> Select[PlatformAuditLog]:
    stmt = select(PlatformAuditLog).order_by(PlatformAuditLog.created_at.desc(), PlatformAuditLog.id)
    if action:
        stmt = stmt.where(PlatformAuditLog.action == action)
    return stmt

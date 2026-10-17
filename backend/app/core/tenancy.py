"""
Tenant isolation: the one place school scoping happens.

Every school-owned model inherits `TenantMixin` (a non-null `school_id`).
Every session declares its scope before it touches tenant data:

- `scope_to_school(session, school_id)`: the request/job acts for one school.
  Each SELECT, UPDATE and DELETE on a TenantMixin model gets
  `school_id = :school_id` added automatically (including relationship and
  `session.get` loads), and new rows get `school_id` stamped from the scope.
- `scope_platform_wide(session)`: platform admin and system jobs that work
  across schools. Nothing is filtered; new rows must set `school_id` themselves.

A session with no scope that queries or writes tenant data raises
`TenancyError`, so forgetting to scope fails closed instead of leaking.

Routers never filter by school by hand. Raw SQL (text()) is not scoped and
must not be used on tenant tables.
"""

import uuid
from typing import Any, Final

from sqlalchemy import ForeignKey, event
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    declared_attr,
    mapped_column,
    with_loader_criteria,
)
from sqlalchemy.orm.attributes import get_history

_SCOPE_KEY: Final = "tenant_scope"


class TenancyError(RuntimeError):
    """Programming error: tenant data was touched without (or against) the session's scope."""


class _PlatformWide:
    def __repr__(self) -> str:
        return "<platform-wide>"


PLATFORM_WIDE: Final = _PlatformWide()


class TenantMixin:
    """Marks a model as owned by one school."""

    @declared_attr
    def school_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(ForeignKey("schools.id", ondelete="RESTRICT"), index=True, nullable=False)


def scope_to_school(session: Session, school_id: uuid.UUID) -> None:
    current = session.info.get(_SCOPE_KEY)
    if current is not None and current != school_id:
        raise TenancyError("Session is already scoped differently; open a new session instead.")
    session.info[_SCOPE_KEY] = school_id


def scope_platform_wide(session: Session) -> None:
    current = session.info.get(_SCOPE_KEY)
    if current is not None and current is not PLATFORM_WIDE:
        raise TenancyError("A school-scoped session can't be widened to platform scope.")
    session.info[_SCOPE_KEY] = PLATFORM_WIDE


def current_scope(session: Session) -> uuid.UUID | _PlatformWide | None:
    scope: uuid.UUID | _PlatformWide | None = session.info.get(_SCOPE_KEY)
    return scope


def _touches_tenant_data(state: ORMExecuteState) -> bool:
    return any(issubclass(mapper.class_, TenantMixin) for mapper in state.all_mappers)


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_criteria(state: ORMExecuteState) -> None:
    if state.is_column_load:
        return  # refreshing attributes of an object that was already loaded under the scope
    if not (state.is_select or state.is_update or state.is_delete):
        return
    scope = state.session.info.get(_SCOPE_KEY)
    if scope is PLATFORM_WIDE:
        return
    if scope is None:
        if _touches_tenant_data(state):
            raise TenancyError("Tenant data queried from a session with no school scope.")
        return
    school_id = scope
    state.statement = state.statement.options(
        with_loader_criteria(TenantMixin, lambda cls: cls.school_id == school_id, include_aliases=True)
    )


@event.listens_for(Session, "before_flush")
def _stamp_and_guard_school(session: Session, _flush_context: Any, _instances: Any) -> None:
    scope = session.info.get(_SCOPE_KEY)
    for obj in session.new:
        if not isinstance(obj, TenantMixin):
            continue
        if scope is None:
            raise TenancyError(f"New {type(obj).__name__} in a session with no school scope.")
        if scope is PLATFORM_WIDE:
            if obj.school_id is None:
                raise TenancyError(f"New {type(obj).__name__} needs an explicit school_id in platform scope.")
        elif obj.school_id is None:
            obj.school_id = scope
        elif obj.school_id != scope:
            raise TenancyError(f"New {type(obj).__name__} belongs to another school.")
    for obj in session.dirty:
        if isinstance(obj, TenantMixin) and get_history(obj, "school_id").has_changes():
            raise TenancyError(f"{type(obj).__name__}.school_id can't be changed.")
    for obj in session.deleted:
        if isinstance(obj, TenantMixin) and scope is not PLATFORM_WIDE and obj.school_id != scope:
            raise TenancyError(f"{type(obj).__name__} belongs to another school.")

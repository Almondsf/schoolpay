"""The tenant-isolation mechanism itself (app/core/tenancy.py)."""

import uuid

import pytest
from sqlalchemy import String, delete, func, select, update
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from app.core.base import Base, UUIDPrimaryKey
from app.core.tenancy import TenancyError, TenantMixin, scope_platform_wide, scope_to_school
from tests.factories import make_school


class Widget(TenantMixin, UUIDPrimaryKey, Base):
    """A test-only tenant model; its table exists only inside each test's rolled-back transaction."""

    __tablename__ = "test_widgets"
    name: Mapped[str] = mapped_column(String(50))


@pytest.fixture
def world(connection, db):
    Widget.__table__.create(connection)
    school_a, school_b = make_school(db), make_school(db)
    scope_platform_wide(db)
    db.add_all(
        [
            Widget(name="a1", school_id=school_a.id),
            Widget(name="a2", school_id=school_a.id),
            Widget(name="b1", school_id=school_b.id),
        ]
    )
    db.commit()
    sessions = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    return school_a, school_b, sessions


def names(session):
    return sorted(session.scalars(select(Widget.name)))


def test_unscoped_session_cannot_read_tenant_data(world):
    _, _, sessions = world
    with sessions() as session, pytest.raises(TenancyError):
        session.scalars(select(Widget)).all()


def test_unscoped_session_cannot_write_tenant_data(world):
    school_a, _, sessions = world
    with sessions() as session:
        session.add(Widget(name="x", school_id=school_a.id))
        with pytest.raises(TenancyError):
            session.flush()


def test_scoped_session_sees_only_its_school(world):
    school_a, school_b, sessions = world
    with sessions() as session:
        scope_to_school(session, school_a.id)
        assert names(session) == ["a1", "a2"]
        assert session.scalar(select(func.count()).select_from(Widget)) == 2
    with sessions() as session:
        scope_to_school(session, school_b.id)
        assert names(session) == ["b1"]


def test_get_by_id_of_another_school_returns_nothing(world):
    school_a, _school_b, sessions = world
    with sessions() as session:
        scope_platform_wide(session)
        b1_id = session.scalar(select(Widget.id).where(Widget.name == "b1"))
    with sessions() as session:
        scope_to_school(session, school_a.id)
        assert session.get(Widget, b1_id) is None


def test_new_rows_take_school_from_scope(world):
    school_a, _, sessions = world
    with sessions() as session:
        scope_to_school(session, school_a.id)
        widget = Widget(name="new")
        session.add(widget)
        session.flush()
        assert widget.school_id == school_a.id


def test_rows_for_another_school_are_refused(world):
    school_a, school_b, sessions = world
    with sessions() as session:
        scope_to_school(session, school_a.id)
        session.add(Widget(name="sneaky", school_id=school_b.id))
        with pytest.raises(TenancyError):
            session.flush()


def test_school_id_cannot_be_changed(world):
    school_a, school_b, sessions = world
    with sessions() as session:
        scope_to_school(session, school_a.id)
        widget = session.scalars(select(Widget)).first()
        widget.school_id = school_b.id
        with pytest.raises(TenancyError):
            session.flush()


def test_bulk_update_and_delete_are_scoped(world):
    school_a, _, sessions = world
    with sessions() as session:
        scope_to_school(session, school_a.id)
        session.execute(update(Widget).values(name="renamed"))
        session.execute(delete(Widget).where(Widget.name == "renamed"))
        session.commit()
    with sessions() as session:
        scope_platform_wide(session)
        assert names(session) == ["b1"]


def test_scope_cannot_be_switched_or_widened(world):
    school_a, school_b, sessions = world
    with sessions() as session:
        scope_to_school(session, school_a.id)
        with pytest.raises(TenancyError):
            scope_to_school(session, school_b.id)
        with pytest.raises(TenancyError):
            scope_platform_wide(session)
        scope_to_school(session, school_a.id)  # same scope again is fine


def test_platform_scope_needs_explicit_school_on_new_rows(world):
    _, _, sessions = world
    with sessions() as session:
        scope_platform_wide(session)
        session.add(Widget(name="orphan"))
        with pytest.raises(TenancyError):
            session.flush()


def test_joins_through_aliases_are_scoped(world):
    school_a, _, sessions = world
    from sqlalchemy.orm import aliased

    other = aliased(Widget)
    with sessions() as session:
        scope_to_school(session, school_a.id)
        rows = session.execute(select(Widget.name, other.name).join(other, other.id != Widget.id)).all()
        assert all(a.startswith("a") and b.startswith("a") for a, b in rows)
        assert len(rows) == 2


def test_unknown_school_scope_sees_nothing(world):
    _, _, sessions = world
    with sessions() as session:
        scope_to_school(session, uuid.uuid4())
        assert names(session) == []

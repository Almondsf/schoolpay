"""
The academic calendar (sessions and terms) and class structure (levels and arms).

Every query here runs in a school-scoped session, so lists and lookups only
ever see the caller's school.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.clock import lagos_today
from app.core.errors import Conflict, DomainError
from app.core.repo import get_or_404
from app.modules.accounting import periods
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record

from .models import AcademicSession, Arm, ClassLevel, Term


@dataclass(frozen=True)
class TermDates:
    start_date: date
    end_date: date


# --- Calendar -------------------------------------------------------------------------------------


def list_sessions(session: Session) -> list[AcademicSession]:
    return list(
        session.scalars(
            select(AcademicSession)
            .options(selectinload(AcademicSession.terms))
            .order_by(AcademicSession.start_date.desc())
        )
    )


def current_term(session: Session) -> Term | None:
    return session.scalar(select(Term).where(Term.is_current.is_(True)).options(selectinload(Term.session)))


def require_current_term(session: Session) -> Term:
    term = current_term(session)
    if term is None:
        raise DomainError("Set up the school calendar and choose the current term first.", code="no_current_term")
    return term


def _check_terms(start: date, end: date, terms: list[TermDates]) -> None:
    if len(terms) != 3:
        raise DomainError("A session has exactly three terms.", field="terms")
    previous_end = None
    for number, term in enumerate(terms, start=1):
        if term.start_date >= term.end_date:
            raise DomainError(f"Term {number} must end after it starts.", field="terms")
        if term.start_date < start or term.end_date > end:
            raise DomainError(f"Term {number} must fall within the session's dates.", field="terms")
        if previous_end is not None and term.start_date <= previous_end:
            raise DomainError(f"Term {number} must start after term {number - 1} ends.", field="terms")
        previous_end = term.end_date


def create_session(
    session: Session,
    ctx: StaffContext,
    *,
    name: str,
    start_date: date,
    end_date: date,
    terms: list[TermDates],
    current_term: int | None = None,
) -> AcademicSession:
    """Create a session with its three terms; `current_term` (1-3) also makes that term the current one."""
    if start_date >= end_date:
        raise DomainError("The session must end after it starts.", field="end_date")
    _check_terms(start_date, end_date, terms)
    academic_session = AcademicSession(id=uuid.uuid4(), name=name.strip(), start_date=start_date, end_date=end_date)
    session.add(academic_session)
    new_terms = [
        Term(id=uuid.uuid4(), session_id=academic_session.id, number=n, start_date=t.start_date, end_date=t.end_date)
        for n, t in enumerate(terms, start=1)
    ]
    session.add_all(new_terms)
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise Conflict(f"There's already a {name} session.", field="name") from e
    record(
        session,
        AuditAction.ACADEMIC_SESSION_CREATED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=academic_session,
        changes={"name": academic_session.name},
    )
    if current_term is not None:
        _make_current(session, ctx, new_terms[current_term - 1])
    session.commit()
    return _reload_session(session, academic_session.id)


@dataclass(frozen=True)
class SuggestedSession:
    name: str
    start_date: date
    end_date: date
    terms: list[TermDates]
    current_term: int | None  # the term today falls in (or the next one, in a holiday); None if not this year


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def suggest_session(session: Session, today: date | None = None) -> SuggestedSession:
    """
    The usual Nigerian school year, so a school only checks the dates: the session after the
    latest one it has (or the one running now), September to July, three terms starting on a
    Monday and ending on a Friday. Schools adjust the dates to their own calendar.
    """
    today = today or lagos_today()
    latest_end = session.scalar(select(func.max(AcademicSession.end_date)))
    year = latest_end.year if latest_end else (today.year if today.month >= 8 else today.year - 1)
    monday, friday = 0, 4
    terms = [
        TermDates(_nth_weekday(year, 9, monday, 2), _nth_weekday(year, 12, friday, 2)),
        TermDates(_nth_weekday(year + 1, 1, monday, 2), _nth_weekday(year + 1, 4, friday, 1)),
        TermDates(_last_weekday(year + 1, 4, monday), _nth_weekday(year + 1, 7, friday, 3)),
    ]
    start, end = date(year, 9, 1), date(year + 1, 7, 31)
    running = start - timedelta(days=45) <= today <= end  # from the August holiday on
    current = next((n for n, t in enumerate(terms, start=1) if today <= t.end_date), 3) if running else None
    return SuggestedSession(f"{year}/{year + 1}", start, end, terms, current)


def _reload_session(session: Session, session_id: uuid.UUID) -> AcademicSession:
    return session.scalars(
        select(AcademicSession)
        .where(AcademicSession.id == session_id)
        .options(selectinload(AcademicSession.terms))
        .execution_options(populate_existing=True)
    ).one()


def update_session(
    session: Session,
    ctx: StaffContext,
    session_id: uuid.UUID,
    *,
    name: str | None,
    start_date: date | None,
    end_date: date | None,
) -> AcademicSession:
    academic_session = get_or_404(session, AcademicSession, session_id, label="Session", lock=True)
    terms = list(session.scalars(select(Term).where(Term.session_id == session_id).order_by(Term.number)))
    before = {
        "name": academic_session.name,
        "start_date": academic_session.start_date,
        "end_date": academic_session.end_date,
    }
    academic_session.name = name.strip() if name else academic_session.name
    academic_session.start_date = start_date or academic_session.start_date
    academic_session.end_date = end_date or academic_session.end_date
    _check_terms(
        academic_session.start_date, academic_session.end_date, [TermDates(t.start_date, t.end_date) for t in terms]
    )
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise Conflict("Another session already has that name.", field="name") from e
    record(
        session,
        AuditAction.ACADEMIC_CALENDAR_UPDATED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=academic_session,
        changes={
            "before": before,
            "after": {
                "name": academic_session.name,
                "start_date": academic_session.start_date,
                "end_date": academic_session.end_date,
            },
        },
    )
    session.commit()
    return _reload_session(session, session_id)


def update_term(session: Session, ctx: StaffContext, term_id: uuid.UUID, dates: TermDates) -> Term:
    term = get_or_404(session, Term, term_id, label="Term", lock=True)
    # A closed term's dates are fixed, and an open term can't be moved into the closed period
    if periods.term_is_closed(session, ctx.school_id, term) or periods.closed_on(
        session, ctx.school_id, dates.start_date
    ):
        raise Conflict("The books are closed for those dates. Ask the proprietor to re-open the term first.")
    academic_session = get_or_404(session, AcademicSession, term.session_id, label="Session")
    siblings = {t.number: t for t in session.scalars(select(Term).where(Term.session_id == term.session_id))}
    proposed = [TermDates(t.start_date, t.end_date) if n != term.number else dates for n, t in sorted(siblings.items())]
    _check_terms(academic_session.start_date, academic_session.end_date, proposed)
    before = {"start_date": term.start_date, "end_date": term.end_date}
    term.start_date, term.end_date = dates.start_date, dates.end_date
    record(
        session,
        AuditAction.ACADEMIC_CALENDAR_UPDATED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=term,
        changes={"before": before, "after": {"start_date": dates.start_date, "end_date": dates.end_date}},
    )
    session.commit()
    return term


def _make_current(session: Session, ctx: StaffContext, term: Term) -> None:
    previous = session.scalar(select(Term.id).where(Term.is_current.is_(True)))
    # Two statements: the "one current term" unique index is checked row by row
    session.execute(update(Term).where(Term.is_current.is_(True)).values(is_current=False))
    session.execute(update(Term).where(Term.id == term.id).values(is_current=True))
    record(
        session,
        AuditAction.CURRENT_TERM_CHANGED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=term,
        changes={"from": previous, "to": term.id},
    )


def set_current_term(session: Session, ctx: StaffContext, term_id: uuid.UUID) -> Term:
    term = get_or_404(session, Term, term_id, label="Term")
    if not term.is_current:
        _make_current(session, ctx, term)
        session.commit()
        session.refresh(term)
    return term


# --- Class structure ------------------------------------------------------------------------------


def list_class_levels(session: Session, *, include_inactive: bool = True) -> list[ClassLevel]:
    stmt = select(ClassLevel).options(selectinload(ClassLevel.arms)).order_by(ClassLevel.rank)
    if not include_inactive:
        stmt = stmt.where(ClassLevel.is_active.is_(True))
    return list(session.scalars(stmt))


def _audit_structure(session: Session, ctx: StaffContext, target: object, changes: dict[str, object]) -> None:
    record(
        session,
        AuditAction.CLASS_STRUCTURE_CHANGED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=target,
        changes=changes,
    )


def create_class_level(session: Session, ctx: StaffContext, *, name: str, arms: list[str]) -> ClassLevel:
    arm_names = _clean_arm_names(arms or ["A"])
    next_rank = (session.scalar(select(func.max(ClassLevel.rank))) or 0) + 1
    level = ClassLevel(id=uuid.uuid4(), name=name.strip(), rank=next_rank)
    session.add(level)
    session.add_all(Arm(id=uuid.uuid4(), class_level_id=level.id, name=arm) for arm in arm_names)
    _flush_unique(session, f"There's already a class called {name.strip()}.", "name")
    _audit_structure(session, ctx, level, {"created": level.name, "arms": arm_names})
    session.commit()
    return _reload_level(session, level.id)


# The classes most Nigerian private schools run, youngest first. New schools start from this list
# (all ticked) and untick what they don't have, instead of typing every class (owner, 2026-10-21).
STANDARD_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Preparatory", ("Creche", "Pre-Nursery", "Nursery 1", "Nursery 2", "Nursery 3")),
    ("Primary", tuple(f"Primary {n}" for n in range(1, 7))),
    ("Junior secondary", ("JSS 1", "JSS 2", "JSS 3")),
    ("Senior secondary", ("SSS 1", "SSS 2", "SSS 3")),
)
_STANDARD_POSITION = {
    name.lower(): position for position, name in enumerate(name for _, names in STANDARD_CLASSES for name in names)
}


@dataclass(frozen=True)
class StandardClass:
    group: str
    name: str
    exists: bool


def standard_classes(session: Session) -> list[StandardClass]:
    """The standard list, marking the classes this school already has (by name, ignoring case)."""
    have = {name.lower() for name in session.scalars(select(ClassLevel.name))}
    return [StandardClass(group, name, name.lower() in have) for group, names in STANDARD_CLASSES for name in names]


def add_class_levels(session: Session, ctx: StaffContext, classes: list[tuple[str, list[str]]]) -> list[ClassLevel]:
    """
    Add several classes at once. Standard classes go where they belong in the promotion order
    (Nursery 3 before Primary 1, JSS 1 after Primary 6) among the school's existing classes;
    other classes go after them, in the order given. A fixed number of statements, however many.
    """
    wanted = [(name.strip(), _clean_arm_names(arms or ["A"])) for name, arms in classes]
    lowered = [name.lower() for name, _ in wanted]
    if len(set(lowered)) != len(lowered):
        raise DomainError("Each class can only be listed once.", field="classes")
    # Lock the school's classes so two people adding at once can't hand out the same rank
    existing = session.execute(select(ClassLevel.id, ClassLevel.name).order_by(ClassLevel.rank).with_for_update()).all()
    taken = sorted(name for name, _ in wanted if name.lower() in {row.name.lower() for row in existing})
    if taken:
        raise Conflict(f"You already have {', '.join(taken)}.", field="classes")

    # The new order: existing classes keep theirs; each standard class goes before the first class
    # that comes after it in the standard list; anything else goes at the end
    order: list[uuid.UUID] = [row.id for row in existing]
    position = {row.id: _STANDARD_POSITION.get(row.name.lower()) for row in existing}
    new_levels = [ClassLevel(id=uuid.uuid4(), name=name, rank=0) for name, _ in wanted]
    standard = sorted(
        (level for level in new_levels if level.name.lower() in _STANDARD_POSITION),
        key=lambda level: _STANDARD_POSITION[level.name.lower()],
    )
    for level in standard:
        mine = _STANDARD_POSITION[level.name.lower()]
        later = next((i for i, lid in enumerate(order) if (position[lid] or -1) > mine), len(order))
        order.insert(later, level.id)
        position[level.id] = mine
    order += [level.id for level in new_levels if level.name.lower() not in _STANDARD_POSITION]
    rank = {level_id: n for n, level_id in enumerate(order, start=1)}

    if existing:  # out of the way first: (school, rank) is unique row by row
        session.execute(update(ClassLevel).values(rank=-ClassLevel.rank - 1000))
    for level in new_levels:
        level.rank = rank[level.id]
    session.add_all(new_levels)
    session.add_all(
        Arm(id=uuid.uuid4(), class_level_id=level.id, name=arm)
        for level, (_, arms) in zip(new_levels, wanted, strict=True)
        for arm in arms
    )
    _flush_unique(session, "Those classes were just changed by someone else. Try again.", "classes")
    if existing:
        session.execute(
            update(ClassLevel)
            .where(ClassLevel.id.in_([row.id for row in existing]))
            .values(rank=case({row.id: rank[row.id] for row in existing}, value=ClassLevel.id))
        )
    _audit_structure(session, ctx, None, {"added": [name for name, _ in wanted]})
    session.commit()
    session.expire_all()
    return list_class_levels(session)


def _clean_arm_names(names: list[str]) -> list[str]:
    cleaned = [n.strip() for n in names if n.strip()]
    if len({n.lower() for n in cleaned}) != len(cleaned):
        raise DomainError("Arm names must be different from each other.", field="arms")
    return cleaned


def _flush_unique(session: Session, message: str, field: str) -> None:
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise Conflict(message, field=field) from e


def _reload_level(session: Session, level_id: uuid.UUID) -> ClassLevel:
    return session.scalars(
        select(ClassLevel)
        .where(ClassLevel.id == level_id)
        .options(selectinload(ClassLevel.arms))
        .execution_options(populate_existing=True)
    ).one()


def update_class_level(
    session: Session, ctx: StaffContext, level_id: uuid.UUID, *, name: str | None, is_active: bool | None
) -> ClassLevel:
    level = get_or_404(session, ClassLevel, level_id, label="Class", lock=True)
    before = {"name": level.name, "is_active": level.is_active}
    if name is not None:
        level.name = name.strip()
    if is_active is not None:
        level.is_active = is_active
    _flush_unique(session, "Another class already has that name.", "name")
    _audit_structure(
        session, ctx, level, {"before": before, "after": {"name": level.name, "is_active": level.is_active}}
    )
    session.commit()
    return _reload_level(session, level_id)


def reorder_class_levels(session: Session, ctx: StaffContext, ordered_ids: list[uuid.UUID]) -> list[ClassLevel]:
    """Set the promotion order: the first id becomes the lowest class. Must list every class exactly once."""
    existing = set(session.scalars(select(ClassLevel.id)))
    if set(ordered_ids) != existing or len(ordered_ids) != len(existing):
        raise DomainError("List every class exactly once.", field="class_level_ids")
    # Move ranks out of the way first: the (school, rank) unique constraint is checked row by row
    session.execute(update(ClassLevel).values(rank=-ClassLevel.rank - 1000))
    ranks = case({level_id: rank for rank, level_id in enumerate(ordered_ids, start=1)}, value=ClassLevel.id)
    session.execute(update(ClassLevel).values(rank=ranks))
    _audit_structure(session, ctx, None, {"order": ordered_ids})
    session.commit()
    session.expire_all()
    return list_class_levels(session)


def add_arm(session: Session, ctx: StaffContext, level_id: uuid.UUID, name: str) -> Arm:
    level = get_or_404(session, ClassLevel, level_id, label="Class")
    arm = Arm(id=uuid.uuid4(), class_level_id=level.id, name=name.strip())
    session.add(arm)
    _flush_unique(session, f"{level.name} already has an arm called {name.strip()}.", "name")
    _audit_structure(session, ctx, arm, {"class": level.name, "arm_added": arm.name})
    session.commit()
    return arm


def update_arm(
    session: Session, ctx: StaffContext, arm_id: uuid.UUID, *, name: str | None, is_active: bool | None
) -> Arm:
    arm = get_or_404(session, Arm, arm_id, label="Arm", lock=True)
    before = {"name": arm.name, "is_active": arm.is_active}
    if name is not None:
        arm.name = name.strip()
    if is_active is not None:
        arm.is_active = is_active
    _flush_unique(session, "That class already has an arm with this name.", "name")
    _audit_structure(session, ctx, arm, {"before": before, "after": {"name": arm.name, "is_active": arm.is_active}})
    session.commit()
    return arm

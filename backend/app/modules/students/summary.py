"""
The school at a glance, without money: who is on roll, where they are placed this
term, and which records need attention (no parent, no consent, no account number).
It's the dashboard for staff who don't see figures (school admins). It always
runs three queries, however big the school. The session must be scoped to the school.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, FunctionFilter, and_, exists, false, func, select
from sqlalchemy.orm import Session

from app.modules.schools.models import AcademicSession, Arm, ClassLevel, Term

from .models import Enrollment, Gender, GuardianStudent, Student, StudentStatus
from .service import Missing, lacks


@dataclass(frozen=True)
class ArmCount:
    arm_id: uuid.UUID
    arm_name: str
    pupils: int


@dataclass
class ClassCount:
    class_level_id: uuid.UUID
    class_name: str
    pupils: int = 0
    boys: int = 0
    girls: int = 0
    arms: list[ArmCount] = field(default_factory=list)


@dataclass
class RollSummary:
    term_id: uuid.UUID | None = None
    term_label: str | None = None
    on_roll: int = 0
    placed: int = 0  # on roll with a class this term
    joined_this_term: int = 0  # first class is this term's
    left_this_term: int = 0  # withdrew or graduated since the term started
    no_parent: int = 0  # on roll with no parent or guardian at all
    no_consent: int = 0  # on roll with no parent whose consent is recorded
    no_account_number: int = 0  # on roll with no active bank account number
    parents: int = 0  # parents and guardians of pupils on roll
    by_class: list[ClassCount] = field(default_factory=list)

    @property
    def unplaced(self) -> int:
        return self.on_roll - self.placed


def _count_if(condition: ColumnElement[bool]) -> FunctionFilter[int]:
    return func.count().filter(condition)


def roll_summary(session: Session) -> RollSummary:
    term = session.execute(
        select(Term, AcademicSession.name).join(AcademicSession).where(Term.is_current.is_(True))
    ).first()
    active = Student.status == StudentStatus.ACTIVE
    placed: ColumnElement[bool]
    if term:
        term_id, start = term[0].id, term[0].start_date
        placed = exists().where(Enrollment.student_id == Student.id, Enrollment.term_id == term_id)
        placed_before = exists().where(
            Enrollment.student_id == Student.id, Enrollment.term_id == Term.id, Term.start_date < start
        )
        joined = and_(placed, ~placed_before)
        left = and_(~active, Student.left_on >= start)
    else:
        term_id, placed, joined, left = None, false(), false(), false()

    row = session.execute(
        select(
            _count_if(active),
            _count_if(and_(active, placed)),
            _count_if(and_(active, joined)),
            _count_if(left),
            _count_if(and_(active, lacks(Missing.PARENT))),
            _count_if(and_(active, lacks(Missing.CONSENT))),
            _count_if(and_(active, lacks(Missing.ACCOUNT_NUMBER))),
        ).where(Student.erased_at.is_(None))
    ).one()
    parents = session.scalar(
        select(func.count(func.distinct(GuardianStudent.guardian_id)))
        .join(Student, Student.id == GuardianStudent.student_id)
        .where(active)
    )
    result = RollSummary(
        term_id=term_id,
        term_label=f"{term[1]} {term[0].label}" if term else None,
        on_roll=row[0],
        placed=row[1],
        joined_this_term=row[2],
        left_this_term=row[3],
        no_parent=row[4],
        no_consent=row[5],
        no_account_number=row[6],
        parents=parents or 0,
    )
    if term_id is None:
        return result

    # Every active class and arm, including empty ones, with this term's pupils on roll
    rows = session.execute(
        select(
            ClassLevel.id,
            ClassLevel.name,
            Arm.id,
            Arm.name,
            func.count(Student.id),
            func.count(Student.id).filter(Student.gender == Gender.MALE),
            func.count(Student.id).filter(Student.gender == Gender.FEMALE),
        )
        .join(Arm, Arm.class_level_id == ClassLevel.id)
        .outerjoin(Enrollment, and_(Enrollment.arm_id == Arm.id, Enrollment.term_id == term_id))
        .outerjoin(Student, and_(Student.id == Enrollment.student_id, active))
        .where(ClassLevel.is_active.is_(True), Arm.is_active.is_(True))
        .group_by(ClassLevel.id, ClassLevel.name, ClassLevel.rank, Arm.id, Arm.name)
        .order_by(ClassLevel.rank, Arm.name)
    ).all()
    classes: dict[uuid.UUID, ClassCount] = {}
    for level_id, level_name, arm_id, arm_name, pupils, boys, girls in rows:
        c = classes.setdefault(level_id, ClassCount(level_id, level_name))
        c.pupils += pupils
        c.boys += boys
        c.girls += girls
        c.arms.append(ArmCount(arm_id, arm_name, pupils))
    result.by_class = list(classes.values())
    return result

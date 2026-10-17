"""
Moving students into the next term.

- Within a session (1st -> 2nd term), students keep their class: their
  enrolments are copied.
- Into a new session (`promote=True`), each student moves up to the next
  active class level by rank. They go to the arm with the same name ("JSS 1
  Gold" -> "JSS 2 Gold"), or the only arm if the next level has one. Students
  in the highest level graduate. Students who can't be placed are listed so
  staff can place them by hand.

Only active students move. Students already enrolled in the target term are
left alone, so running it twice is harmless. The whole thing is a fixed
number of queries (no per-student lookups).
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.core.errors import DomainError
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.schools.models import Arm, ClassLevel, Term

from .models import Enrollment, Student, StudentStatus


@dataclass
class Unplaced:
    student_id: uuid.UUID
    admission_number: str
    name: str
    reason: str


@dataclass
class RollForwardReport:
    dry_run: bool
    moved: int = 0
    promoted: int = 0
    graduated: int = 0
    already_enrolled: int = 0
    unplaced: list[Unplaced] = field(default_factory=list)


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def roll_forward(
    session: Session, ctx: StaffContext, *, from_term_id: uuid.UUID, to_term_id: uuid.UUID, promote: bool, dry_run: bool
) -> RollForwardReport:
    from_term = get_or_404(session, Term, from_term_id, label="Term", field="from_term_id")
    to_term = get_or_404(session, Term, to_term_id, label="Term", field="to_term_id")
    if to_term.start_date <= from_term.start_date:
        raise DomainError("Students can only move forward to a later term.", field="to_term_id")
    if promote and to_term.session_id == from_term.session_id:
        raise DomainError(
            "Promotion happens between sessions. Within a session, students keep their class.", field="promote"
        )

    # 1 query: who is in the source term, with their class
    sources = session.execute(
        select(
            Enrollment.student_id,
            Enrollment.arm_id,
            Enrollment.class_level_id,
            Student.admission_number,
            Student.first_name,
            Student.last_name,
        )
        .join(Student, Student.id == Enrollment.student_id)
        .where(Enrollment.term_id == from_term.id, Student.status == StudentStatus.ACTIVE)
    ).all()
    # 1 query: who is already in the target term
    already = set(session.scalars(select(Enrollment.student_id).where(Enrollment.term_id == to_term.id)))
    # 1 query: the class structure
    levels = list(session.scalars(select(ClassLevel).options(selectinload(ClassLevel.arms)).order_by(ClassLevel.rank)))
    arms_by_id: dict[uuid.UUID, Arm] = {a.id: a for level in levels for a in level.arms}
    active_levels = [lv for lv in levels if lv.is_active]

    def next_level(level_id: uuid.UUID) -> ClassLevel | None:
        current = next((lv for lv in levels if lv.id == level_id), None)
        if current is None:
            return None
        return next((lv for lv in active_levels if lv.rank > current.rank), None)

    report = RollForwardReport(dry_run=dry_run)
    new_enrollments: list[Enrollment] = []
    graduating: list[uuid.UUID] = []
    for student_id, arm_id, level_id, admission, first, last in sources:
        if student_id in already:
            report.already_enrolled += 1
            continue
        current_arm = arms_by_id[arm_id]
        if not promote:
            new_enrollments.append(
                Enrollment(student_id=student_id, term_id=to_term.id, arm_id=arm_id, class_level_id=level_id)
            )
            report.moved += 1
            continue
        target_level = next_level(level_id)
        if target_level is None:
            graduating.append(student_id)
            continue
        candidates = [a for a in target_level.arms if a.is_active]
        target_arm = next((a for a in candidates if _norm(a.name) == _norm(current_arm.name)), None)
        if target_arm is None and len(candidates) == 1:
            target_arm = candidates[0]
        if target_arm is None:
            report.unplaced.append(
                Unplaced(
                    student_id,
                    admission,
                    f"{first} {last}",
                    f"{target_level.name} has no arm called {current_arm.name}.",
                )
            )
            continue
        new_enrollments.append(
            Enrollment(student_id=student_id, term_id=to_term.id, arm_id=target_arm.id, class_level_id=target_level.id)
        )
        report.promoted += 1
    report.graduated = len(graduating)

    if dry_run:
        return report
    session.add_all(new_enrollments)
    if graduating:
        session.execute(
            update(Student)
            .where(Student.id.in_(graduating))
            .values(status=StudentStatus.GRADUATED, left_on=from_term.end_date)
        )
    record(
        session,
        AuditAction.STUDENTS_ROLLED_FORWARD,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=to_term,
        changes={
            "from_term": from_term.id,
            "to_term": to_term.id,
            "promote": promote,
            "moved": report.moved,
            "promoted": report.promoted,
            "graduated": report.graduated,
            "unplaced": len(report.unplaced),
        },
    )
    session.commit()
    return report

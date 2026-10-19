"""
Students, their per-term class (enrolment) and their guardians.

All queries run in a school-scoped session. IDs that come from request
bodies (arm, term, guardian) are loaded through that scope too, so another
school's IDs are simply "not found".
"""

import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import ColumnElement, Select, and_, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.crypto import blind_index
from app.core.errors import Conflict, DomainError, NotFound
from app.core.phone import InvalidPhone, normalize_phone
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import changed_fields, record
from app.modules.payments.models import VirtualAccount, VirtualAccountStatus
from app.modules.schools.academics_service import require_current_term
from app.modules.schools.models import AcademicSession, Arm, ClassLevel, Term

from .models import (
    ConsentMethod,
    Enrollment,
    Gender,
    Guardian,
    GuardianStudent,
    Relationship,
    Student,
    StudentStatus,
)

STUDENT_FIELDS = ("admission_number", "first_name", "middle_name", "last_name", "gender", "date_of_birth")


@dataclass(frozen=True)
class StudentRow:
    """A student with their class in the term being viewed (None if not enrolled that term)."""

    student: Student
    arm_id: uuid.UUID | None
    arm_name: str | None
    class_level_id: uuid.UUID | None
    class_level_name: str | None


@dataclass
class GuardianInput:
    full_name: str
    phone: str
    email: str | None = None
    relationship: Relationship = Relationship.GUARDIAN
    is_primary: bool = False


@dataclass
class StudentInput:
    admission_number: str
    first_name: str
    last_name: str
    middle_name: str = ""
    gender: Gender | None = None
    date_of_birth: date | None = None
    guardians: list[GuardianInput] = field(default_factory=list)


def phone_lookup(raw: str, field_name: str = "phone") -> tuple[str, str]:
    """(E.164 phone, blind index) or a field error."""
    try:
        phone = normalize_phone(raw)
    except InvalidPhone as e:
        raise DomainError(str(e), field=field_name) from e
    return phone, blind_index(phone)


# --- Listing ---------------------------------------------------------------------------------------


class Missing(enum.StrEnum):
    """Records a pupil should have but doesn't (the school-at-a-glance counts and the list filter)."""

    PARENT = "parent"  # no parent or guardian at all
    CONSENT = "consent"  # no parent whose consent is recorded
    ACCOUNT_NUMBER = "account_number"  # no active bank account number


def lacks(missing: Missing) -> ColumnElement[bool]:
    """True for a student (correlated to `Student`) who is missing this record."""
    match missing:
        case Missing.PARENT:
            has = exists().where(GuardianStudent.student_id == Student.id)
        case Missing.CONSENT:
            has = exists().where(
                GuardianStudent.student_id == Student.id,
                GuardianStudent.guardian_id == Guardian.id,
                Guardian.consent_at.is_not(None),
            )
        case Missing.ACCOUNT_NUMBER:
            has = exists().where(
                VirtualAccount.student_id == Student.id, VirtualAccount.status == VirtualAccountStatus.ACTIVE
            )
    return ~has


def students_query(
    term_id: uuid.UUID,
    *,
    search: str | None = None,
    class_level_id: uuid.UUID | None = None,
    arm_id: uuid.UUID | None = None,
    status: StudentStatus | None = StudentStatus.ACTIVE,
    unplaced_only: bool = False,
    missing: Missing | None = None,
) -> Select[Student, uuid.UUID, str, uuid.UUID, str]:
    """Students with their class for `term_id`, in one query (no per-row lookups)."""
    stmt = (
        select(Student, Arm.id, Arm.name, ClassLevel.id, ClassLevel.name)
        .outerjoin(Enrollment, and_(Enrollment.student_id == Student.id, Enrollment.term_id == term_id))
        .outerjoin(Arm, Arm.id == Enrollment.arm_id)
        .outerjoin(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
        .order_by(Student.last_name, Student.first_name, Student.admission_number)
    )
    if status is not None:
        stmt = stmt.where(Student.status == status)
    if class_level_id:
        stmt = stmt.where(Enrollment.class_level_id == class_level_id)
    if arm_id:
        stmt = stmt.where(Enrollment.arm_id == arm_id)
    if unplaced_only:
        stmt = stmt.where(Enrollment.id.is_(None))
    if missing:
        stmt = stmt.where(lacks(missing))
    if search and search.strip():
        terms = [t for t in search.strip().split() if t][:4]
        for t in terms:
            pattern = f"%{t}%"
            stmt = stmt.where(
                or_(
                    Student.first_name.ilike(pattern),
                    Student.last_name.ilike(pattern),
                    Student.middle_name.ilike(pattern),
                    Student.admission_number.ilike(pattern),
                )
            )
    return stmt


def list_students(
    session: Session, term_id: uuid.UUID, limit: int, offset: int, **filters: object
) -> tuple[list[StudentRow], int]:
    stmt = students_query(term_id, **filters)  # type: ignore[arg-type]
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = session.execute(stmt.limit(limit).offset(offset)).all()
    return [
        StudentRow(student, arm_id, arm_name, level_id, level_name)
        for student, arm_id, arm_name, level_id, level_name in rows
    ], total


# --- One student ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class EnrollmentView:
    term_id: uuid.UUID
    term_number: int
    session_name: str
    is_current: bool
    arm_id: uuid.UUID
    arm_name: str
    class_level_id: uuid.UUID
    class_level_name: str


@dataclass(frozen=True)
class StudentDetail:
    student: Student
    guardians: list[tuple[Guardian, GuardianStudent]]
    enrollments: list[EnrollmentView]


def student_detail(session: Session, student_id: uuid.UUID) -> StudentDetail:
    student = get_or_404(session, Student, student_id, label="Student")
    guardians = [
        (g, link)
        for g, link in session.execute(
            select(Guardian, GuardianStudent)
            .join(GuardianStudent, GuardianStudent.guardian_id == Guardian.id)
            .where(GuardianStudent.student_id == student_id)
            .order_by(GuardianStudent.is_primary.desc(), Guardian.full_name)
        )
    ]
    enrollments = [
        EnrollmentView(*row)
        for row in session.execute(
            select(
                Term.id,
                Term.number,
                AcademicSession.name,
                Term.is_current,
                Arm.id,
                Arm.name,
                ClassLevel.id,
                ClassLevel.name,
            )
            .select_from(Enrollment)
            .join(Term, Term.id == Enrollment.term_id)
            .join(AcademicSession, AcademicSession.id == Term.session_id)
            .join(Arm, Arm.id == Enrollment.arm_id)
            .join(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
            .where(Enrollment.student_id == student_id)
            .order_by(Term.start_date.desc())
        ).all()
    ]
    return StudentDetail(student=student, guardians=guardians, enrollments=enrollments)


def _active_arm(session: Session, arm_id: uuid.UUID) -> Arm:
    arm = get_or_404(session, Arm, arm_id, label="Arm", field="arm_id")
    if not arm.is_active:
        raise DomainError("That arm is no longer in use.", field="arm_id")
    return arm


def _enroll(session: Session, student_id: uuid.UUID, term_id: uuid.UUID, arm: Arm) -> None:
    existing = session.scalar(
        select(Enrollment).where(Enrollment.student_id == student_id, Enrollment.term_id == term_id)
    )
    if existing is None:
        session.add(
            Enrollment(student_id=student_id, term_id=term_id, arm_id=arm.id, class_level_id=arm.class_level_id)
        )
    else:
        existing.arm_id, existing.class_level_id = arm.id, arm.class_level_id


def create_student(
    session: Session,
    ctx: StaffContext,
    data: StudentInput,
    *,
    arm_id: uuid.UUID,
    term_id: uuid.UUID | None,
    consent_confirmed: bool,
) -> Student:
    term = (
        get_or_404(session, Term, term_id, label="Term", field="term_id") if term_id else require_current_term(session)
    )
    arm = _active_arm(session, arm_id)
    if data.guardians and not consent_confirmed:
        raise DomainError(
            "Confirm that the guardians agreed to their details being stored.",
            field="consent_confirmed",
            code="consent_required",
        )
    student = Student(id=uuid.uuid4(), **{f: getattr(data, f) for f in STUDENT_FIELDS})
    student.admission_number = student.admission_number.strip()
    session.add(student)
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise Conflict(f"Admission number {data.admission_number} is already in use.", field="admission_number") from e
    _enroll(session, student.id, term.id, arm)
    _link_guardians(session, student, _with_one_primary(data.guardians))
    record(
        session,
        AuditAction.STUDENT_CREATED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=student,
        changes={"admission_number": student.admission_number, "arm": arm.id, "guardians": len(data.guardians)},
    )
    session.commit()
    return student


def _with_one_primary(guardians: list[GuardianInput]) -> list[GuardianInput]:
    """Exactly one primary guardian: the first one marked primary, else the first listed."""
    primary = next((i for i, g in enumerate(guardians) if g.is_primary), 0)
    for i, g in enumerate(guardians):
        g.is_primary = i == primary
    return guardians


def update_student(session: Session, ctx: StaffContext, student_id: uuid.UUID, changes: dict[str, object]) -> Student:
    student = get_or_404(session, Student, student_id, label="Student", lock=True)
    before = {f: getattr(student, f) for f in STUDENT_FIELDS}
    for name, value in changes.items():
        if name in STUDENT_FIELDS:
            setattr(student, name, value.strip() if isinstance(value, str) else value)
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise Conflict("That admission number is already in use.", field="admission_number") from e
    diff = changed_fields(before, {f: getattr(student, f) for f in STUDENT_FIELDS})
    if diff:
        record(
            session,
            AuditAction.STUDENT_UPDATED,
            school_id=ctx.school_id,
            actor=ctx.actor,
            meta=ctx.meta,
            target=student,
            changes=diff,
        )
    session.commit()
    return student


def change_class(
    session: Session, ctx: StaffContext, student_id: uuid.UUID, arm_id: uuid.UUID, term_id: uuid.UUID | None
) -> Student:
    student = get_or_404(session, Student, student_id, label="Student")
    if student.status != StudentStatus.ACTIVE:
        raise DomainError("Only current students can be placed in a class.")
    term = (
        get_or_404(session, Term, term_id, label="Term", field="term_id") if term_id else require_current_term(session)
    )
    arm = _active_arm(session, arm_id)
    _enroll(session, student.id, term.id, arm)
    record(
        session,
        AuditAction.STUDENT_CLASS_CHANGED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=student,
        changes={"term": term.id, "arm": arm.id},
    )
    session.commit()
    return student


def withdraw(session: Session, ctx: StaffContext, student_id: uuid.UUID, *, left_on: date, reason: str) -> Student:
    """The student has left. Their history and any balance stay; they drop out of new billing."""
    student = get_or_404(session, Student, student_id, label="Student", lock=True)
    if student.status != StudentStatus.ACTIVE:
        raise Conflict("This student isn't currently active.")
    student.status, student.left_on = StudentStatus.WITHDRAWN, left_on
    record(
        session,
        AuditAction.STUDENT_WITHDRAWN,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=student,
        changes={"left_on": left_on, "reason": reason},
    )
    session.commit()
    return student


def reinstate(session: Session, ctx: StaffContext, student_id: uuid.UUID) -> Student:
    student = get_or_404(session, Student, student_id, label="Student", lock=True)
    if student.status == StudentStatus.ACTIVE:
        return student
    previous = student.status
    student.status, student.left_on = StudentStatus.ACTIVE, None
    record(
        session,
        AuditAction.STUDENT_REINSTATED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=student,
        changes={"from": previous},
    )
    session.commit()
    return student


# --- Guardians -----------------------------------------------------------------------------------


def _link_guardians(session: Session, student: Student, inputs: list[GuardianInput]) -> list[tuple[Guardian, bool]]:
    """
    Link guardians to a student, reusing existing guardians matched by phone.
    A fixed number of queries however many guardians there are. Returns
    (guardian, newly created) per input.
    """
    if not inputs:
        return []
    resolved = [(g, *phone_lookup(g.phone)) for g in inputs]
    indexes = [index for _, _, index in resolved]
    if len(set(indexes)) != len(indexes):
        raise DomainError("The same phone number is listed twice.", field="phone")
    existing = {g.phone_index: g for g in session.scalars(select(Guardian).where(Guardian.phone_index.in_(indexes)))}
    linked = set(
        session.scalars(
            select(GuardianStudent.guardian_id).where(
                GuardianStudent.student_id == student.id,
                GuardianStudent.guardian_id.in_([g.id for g in existing.values()]),
            )
        )
    )
    results: list[tuple[Guardian, bool]] = []
    now = datetime.now(UTC)
    for data, phone, index in resolved:
        guardian = existing.get(index)
        created = guardian is None
        if guardian is None:
            guardian = Guardian(
                id=uuid.uuid4(),
                full_name=data.full_name.strip(),
                phone=phone,
                phone_index=index,
                email=data.email or None,
                consent_at=now,
                consent_method=ConsentMethod.SCHOOL_ATTESTED,
            )
            session.add(guardian)
        elif guardian.id in linked:
            raise Conflict(f"{guardian.full_name} is already linked to this student.", field="phone")
        results.append((guardian, created))
    if any(data.is_primary for data, _, _ in resolved):
        session.execute(
            update(GuardianStudent).where(GuardianStudent.student_id == student.id).values(is_primary=False)
        )
    session.add_all(
        GuardianStudent(
            guardian_id=guardian.id,
            student_id=student.id,
            relationship_to_student=data.relationship,
            is_primary=data.is_primary,
        )
        for (guardian, _), (data, _, _) in zip(results, resolved, strict=True)
    )
    session.flush()
    return results


def add_guardian(
    session: Session, ctx: StaffContext, student_id: uuid.UUID, data: GuardianInput, consent_confirmed: bool
) -> Guardian:
    student = get_or_404(session, Student, student_id, label="Student")
    if not consent_confirmed:
        raise DomainError(
            "Confirm that the guardian agreed to their details being stored.",
            field="consent_confirmed",
            code="consent_required",
        )
    has_primary = session.scalar(
        select(GuardianStudent.guardian_id).where(
            GuardianStudent.student_id == student.id, GuardianStudent.is_primary.is_(True)
        )
    )
    data.is_primary = data.is_primary or has_primary is None
    [(guardian, created)] = _link_guardians(session, student, [data])
    record(
        session,
        AuditAction.GUARDIAN_LINKED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=student,
        changes={
            "guardian": guardian.id,
            "new_guardian": created,
            "relationship": data.relationship,
            "primary": data.is_primary,
        },
    )
    session.commit()
    return guardian


def remove_guardian(session: Session, ctx: StaffContext, student_id: uuid.UUID, guardian_id: uuid.UUID) -> None:
    link = session.get(GuardianStudent, (guardian_id, student_id), with_for_update=True)
    if link is None:
        raise NotFound("That guardian isn't linked to this student.")
    was_primary = link.is_primary
    session.delete(link)
    session.flush()
    if was_primary:  # promote another guardian, if any, so reminders still have someone to go to
        replacement = session.scalar(
            select(GuardianStudent)
            .where(GuardianStudent.student_id == student_id)
            .order_by(GuardianStudent.guardian_id)
            .limit(1)
        )
        if replacement is not None:
            replacement.is_primary = True
    record(
        session,
        AuditAction.GUARDIAN_UNLINKED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=link,
        changes={"student": student_id, "guardian": guardian_id},
    )
    session.commit()


def set_primary_guardian(session: Session, ctx: StaffContext, student_id: uuid.UUID, guardian_id: uuid.UUID) -> None:
    link = session.get(GuardianStudent, (guardian_id, student_id))
    if link is None:
        raise NotFound("That guardian isn't linked to this student.")
    session.execute(
        update(GuardianStudent)
        .where(GuardianStudent.student_id == student_id, GuardianStudent.is_primary.is_(True))
        .values(is_primary=False)
    )
    session.execute(
        update(GuardianStudent)
        .where(GuardianStudent.student_id == student_id, GuardianStudent.guardian_id == guardian_id)
        .values(is_primary=True)
    )
    record(
        session,
        AuditAction.GUARDIAN_LINKED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=link,
        changes={"student": student_id, "guardian": guardian_id, "primary": True},
    )
    session.commit()


def update_guardian(
    session: Session,
    ctx: StaffContext,
    guardian_id: uuid.UUID,
    *,
    full_name: str | None,
    phone: str | None,
    email: str | None,
    clear_email: bool = False,
) -> Guardian:
    """
    A guardian's phone is how they sign in to the parent portal, so changing it
    changes who can see the children's records: it's audited (without the numbers).
    """
    guardian = get_or_404(session, Guardian, guardian_id, label="Guardian", lock=True)
    changed: list[str] = []
    if full_name is not None and full_name.strip() != guardian.full_name:
        guardian.full_name = full_name.strip()
        changed.append("full_name")
    if phone is not None:
        new_phone, index = phone_lookup(phone)
        if index != guardian.phone_index:
            guardian.phone, guardian.phone_index = new_phone, index
            changed.append("phone")
    if clear_email or (email is not None and email != guardian.email):
        guardian.email = None if clear_email else email
        changed.append("email")
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise Conflict("Another guardian at this school already has that phone number.", field="phone") from e
    if changed:
        record(
            session,
            AuditAction.GUARDIAN_CONTACT_CHANGED,
            school_id=ctx.school_id,
            actor=ctx.actor,
            meta=ctx.meta,
            target=guardian,
            changes={"fields": changed},
        )
    session.commit()
    return guardian


def guardians_query(search: str | None) -> Select[Guardian, int]:
    """Guardians with how many students each is linked to. A phone search is an exact match (it's encrypted)."""
    counts = aliased(GuardianStudent)
    stmt = (
        select(Guardian, func.count(counts.student_id))
        .outerjoin(counts, counts.guardian_id == Guardian.id)
        .group_by(Guardian.id)
        .order_by(Guardian.full_name)
    )
    if search and search.strip():
        value = search.strip()
        try:
            stmt = stmt.where(Guardian.phone_index == blind_index(normalize_phone(value)))
        except InvalidPhone:
            stmt = stmt.where(Guardian.full_name.ilike(f"%{value}%"))
    return stmt


def guardian_children(session: Session, guardian_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Student]]:
    """Each guardian's linked students, for a page of guardians, in one query."""
    result: dict[uuid.UUID, list[Student]] = {gid: [] for gid in guardian_ids}
    if not guardian_ids:
        return result
    for guardian_id, student in session.execute(
        select(GuardianStudent.guardian_id, Student)
        .join(Student, Student.id == GuardianStudent.student_id)
        .where(GuardianStudent.guardian_id.in_(guardian_ids))
        .order_by(Student.first_name)
    ):
        result[guardian_id].append(student)
    return result

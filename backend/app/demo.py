"""
Demo data for local development and browser tests: `python -m app.cli seed-demo`.

Builds "Demo Model College" with staff, the 2026/2027 calendar, classes from
Primary 1 to SSS 3, 60 students with guardians (some siblings share a
parent), and first-term fees (tuition by class, optional bus, levies, a
sibling discount). Invoices aren't generated, so that step can be tried.
Refuses to run in production. Everything is inserted in a few bulk
statements.
"""

import random
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import blind_index
from app.core.db import SessionLocal
from app.core.tenancy import scope_platform_wide
from app.models import load_all_models
from app.modules.accounts.models import Role, User
from app.modules.accounts.security import hash_password
from app.modules.fees.models import Discount, DiscountKind, DiscountRule, FeeCategory, FeeItem, FeeSchedule
from app.modules.payments.gateways import GatewayCode, enabled_gateways
from app.modules.payments.models import SchoolPaymentAccount, VirtualAccount
from app.modules.schools.models import AcademicSession, Arm, ClassLevel, School, SchoolStatus, Term
from app.modules.students.models import (
    ConsentMethod,
    Enrollment,
    Gender,
    Guardian,
    GuardianStudent,
    Relationship,
    Student,
)

DEMO_PASSWORD = "demo-password-123"  # noqa: S105  (demo data only; refuses to run in production)
DEMO_SLUG = "demo-school"
PLATFORM_EMAIL = "platform@demo.schoolpay.ng"

CLASSES = [
    *((f"Primary {n}", ["A"]) for n in range(1, 7)),
    ("JSS 1", ["A", "B"]),
    ("JSS 2", ["A", "B"]),
    ("JSS 3", ["A", "B"]),
    ("SSS 1", ["Science", "Arts"]),
    ("SSS 2", ["Science", "Arts"]),
    ("SSS 3", ["Science", "Arts"]),
]
SURNAMES = [
    "Okafor",
    "Adeyemi",
    "Bello",
    "Eze",
    "Ibrahim",
    "Okonkwo",
    "Balogun",
    "Nwosu",
    "Abubakar",
    "Ogunleye",
    "Chukwu",
    "Adebayo",
    "Musa",
    "Obi",
    "Afolabi",
    "Danjuma",
    "Etim",
    "Ojo",
    "Uche",
    "Lawal",
]
GIRLS = ["Chiamaka", "Aisha", "Funmilayo", "Ngozi", "Zainab", "Adaeze", "Temitope", "Amina", "Ifeoma", "Kemi"]
BOYS = ["Emeka", "Tunde", "Ibrahim", "Chinedu", "Segun", "Musa", "Obinna", "Femi", "Yusuf", "Kelechi"]


def seed_demo(mfa_secret: str, reset: bool) -> None:
    if get_settings().is_production:
        raise SystemExit("seed-demo never runs in production.")
    load_all_models()
    with SessionLocal() as session:
        scope_platform_wide(session)
        if session.scalar(select(School.id).where(School.slug == DEMO_SLUG)) is not None:
            if not reset:
                raise SystemExit("The demo school already exists (use --reset on a throwaway database).")
            _retire_old_demo(session)
        school = School(
            id=uuid.uuid4(),
            name="Demo Model College",
            slug=DEMO_SLUG,
            address="12 Allen Avenue, Ikeja, Lagos",
            phone="+234 801 000 0000",
        )
        session.add(school)
        session.flush()
        _staff(session, school, mfa_secret)
        term = _calendar(session, school)
        arms = _classes(session, school)
        levels = _students(session, school, term, arms)
        _fees(session, school, term, levels)
        if GatewayCode.FAKE in enabled_gateways():
            _fake_payments_setup(session, school)
        session.commit()


def _retire_old_demo(session: Session) -> None:
    """
    Retire the previous demo school: remove its staff, suspend it (so its parents
    and account numbers stop working) and rename it out of the way. Its rows stay,
    because money and audit records are never deleted.
    """
    old_ids = select(School.id).where(School.slug == DEMO_SLUG).scalar_subquery()
    session.execute(delete(User).where(or_(User.school_id == old_ids, User.email == PLATFORM_EMAIL)))
    session.execute(
        update(School)
        .where(School.slug == DEMO_SLUG)
        .values(slug=f"old-demo-{uuid.uuid4().hex[:8]}", status=SchoolStatus.SUSPENDED)
    )
    # Demo schools retired before suspension was added
    session.execute(
        update(School)
        .where(School.slug.like("old-demo-%"), School.status == SchoolStatus.ACTIVE)
        .values(status=SchoolStatus.SUSPENDED)
    )
    session.flush()


def _staff(session: Session, school: School, mfa_secret: str) -> None:
    people = [
        ("proprietor@demo.schoolpay.ng", "Grace Adeyemi", Role.PROPRIETOR, school.id, True),
        ("bursar@demo.schoolpay.ng", "Musa Bello", Role.BURSAR, school.id, True),
        ("admin@demo.schoolpay.ng", "Ada Obi", Role.SCHOOL_ADMIN, school.id, False),
        (PLATFORM_EMAIL, "Platform Ops", Role.PLATFORM_ADMIN, None, True),
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


def _calendar(session: Session, school: School) -> Term:
    academic = AcademicSession(
        id=uuid.uuid4(), school_id=school.id, name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31)
    )
    terms = [
        Term(
            id=uuid.uuid4(),
            school_id=school.id,
            session_id=academic.id,
            number=n,
            start_date=s,
            end_date=e,
            is_current=n == 1,
        )
        for n, (s, e) in enumerate(
            [
                (date(2026, 9, 7), date(2026, 12, 11)),
                (date(2027, 1, 4), date(2027, 4, 1)),
                (date(2027, 4, 19), date(2027, 7, 23)),
            ],
            start=1,
        )
    ]
    session.add(academic)
    session.flush()
    session.add_all(terms)
    return terms[0]


def _classes(session: Session, school: School) -> list[Arm]:
    levels: list[ClassLevel] = []
    arms: list[Arm] = []
    for rank, (name, arm_names) in enumerate(CLASSES, start=1):
        level = ClassLevel(id=uuid.uuid4(), school_id=school.id, name=name, rank=rank)
        levels.append(level)
        arms.extend(Arm(id=uuid.uuid4(), school_id=school.id, class_level_id=level.id, name=a) for a in arm_names)
    session.add_all(levels)
    session.flush()
    session.add_all(arms)
    return arms


def _students(session: Session, school: School, term: Term, arms: list[Arm], count: int = 60) -> list[uuid.UUID]:
    rng = random.Random(2026)  # noqa: S311  (demo data, not security)
    now = datetime.now(UTC)
    students, guardians, links, enrollments = [], [], [], []
    for i in range(count):
        surname = SURNAMES[i // 3 % len(SURNAMES)]
        # Every three students share a surname and, for the first two, a parent
        if i % 3 != 2:
            parent_phone = f"+234803{5000000 + i // 3:07d}"
            if i % 3 == 0:
                guardians.append(
                    Guardian(
                        id=uuid.uuid4(),
                        school_id=school.id,
                        full_name=f"Mrs {rng.choice(GIRLS)} {surname}",
                        phone=parent_phone,
                        phone_index=blind_index(parent_phone),
                        consent_at=now,
                        consent_method=ConsentMethod.SCHOOL_ATTESTED,
                    )
                )
        else:
            other_phone = f"+234806{6000000 + i:07d}"
            guardians.append(
                Guardian(
                    id=uuid.uuid4(),
                    school_id=school.id,
                    full_name=f"Mr {rng.choice(BOYS)} {surname}",
                    phone=other_phone,
                    phone_index=blind_index(other_phone),
                    consent_at=now,
                    consent_method=ConsentMethod.SCHOOL_ATTESTED,
                )
            )
        girl = rng.random() < 0.5
        student = Student(
            id=uuid.uuid4(),
            school_id=school.id,
            admission_number=f"DMC/{2026 - i % 9}/{i + 1:03d}",
            first_name=rng.choice(GIRLS if girl else BOYS),
            last_name=surname,
            gender=Gender.FEMALE if girl else Gender.MALE,
            date_of_birth=date(2008 + i % 12, 1 + i % 12, 1 + i % 28),
        )
        arm = arms[i % len(arms)]
        students.append(student)
        enrollments.append(
            Enrollment(
                id=uuid.uuid4(),
                school_id=school.id,
                student_id=student.id,
                term_id=term.id,
                arm_id=arm.id,
                class_level_id=arm.class_level_id,
            )
        )
        links.append(
            GuardianStudent(
                school_id=school.id,
                guardian_id=guardians[-1].id,
                student_id=student.id,
                relationship_to_student=Relationship.MOTHER
                if guardians[-1].full_name.startswith("Mrs")
                else Relationship.FATHER,
                is_primary=True,
            )
        )
    session.add_all([*students, *guardians])
    session.flush()
    session.add_all([*enrollments, *links])
    return list(dict.fromkeys(a.class_level_id for a in arms))


def _fees(session: Session, school: School, term: Term, level_ids: list[uuid.UUID]) -> None:
    """Tuition rising by class, an optional bus fee, PTA and development levies, and a sibling discount."""
    items = {
        name: FeeItem(id=uuid.uuid4(), school_id=school.id, name=name, category=category)
        for name, category in (
            ("Tuition", FeeCategory.TUITION),
            ("School bus", FeeCategory.SERVICE),
            ("PTA levy", FeeCategory.LEVY),
            ("Development levy", FeeCategory.LEVY),
        )
    }
    session.add_all(items.values())
    session.flush()  # no ORM relationship tells the flush to insert items before the discount
    session.add(
        Discount(
            id=uuid.uuid4(),
            school_id=school.id,
            name="Sibling discount (10% of tuition)",
            kind=DiscountKind.PERCENT,
            value=1000,
            rule=DiscountRule.SIBLING,
            fee_item_id=items["Tuition"].id,
        )
    )
    session.flush()
    schedules = []
    for rank, level_id in enumerate(level_ids):
        for item, naira, optional in (
            ("Tuition", 120_000 + rank * 15_000, False),
            ("School bus", 45_000, True),
            ("PTA levy", 5_000, False),
            ("Development levy", 10_000, False),
        ):
            schedules.append(
                FeeSchedule(
                    school_id=school.id,
                    term_id=term.id,
                    class_level_id=level_id,
                    fee_item_id=items[item].id,
                    amount_kobo=naira * 100,
                    is_optional=optional,
                )
            )
    session.add_all(schedules)


def _fake_payments_setup(session: Session, school: School) -> None:
    """With the fake gateway: a connected bank account and a virtual account for every student."""
    session.add(
        SchoolPaymentAccount(
            school_id=school.id,
            gateway=GatewayCode.FAKE,
            subaccount_code="FAKE_SUB_DEMO",
            bank_code="058",
            bank_name="GTBank",
            account_number="0123456789",
            account_number_last4="6789",
            account_name="DEMO MODEL COLLEGE",
            platform_fee_bp=100,
        )
    )
    students = session.execute(
        select(Student.id, Student.first_name, Student.last_name)
        .where(Student.school_id == school.id)
        .order_by(Student.admission_number)
    ).all()
    prefix = uuid.uuid4().int % 90 + 10  # keeps numbers unique across re-seeds
    session.add_all(
        VirtualAccount(
            school_id=school.id,
            student_id=sid,
            gateway=GatewayCode.FAKE,
            account_number=f"9{prefix}{n:07d}",
            account_name=f"DMC/{last.upper()} {first.upper()}",
            bank_name="Fake Bank",
            provider_reference=f"FAKE_CUS_{n}",
        )
        for n, (sid, first, last) in enumerate(students, start=1)
    )

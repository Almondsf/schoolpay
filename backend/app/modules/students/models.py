"""
Students, guardians and enrolments.

Data is kept to what fee collection needs (NDPA minimisation): names,
admission number, class, and guardian contact details. Guardian phone and
email are encrypted at rest; `phone_index` (a keyed blind index of the
normalised phone) allows exact lookups without decrypting.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, Timestamped, UUIDPrimaryKey, enum_type
from app.core.crypto import BLIND_INDEX_TYPE, EncryptedString
from app.core.tenancy import TenantMixin
from app.modules.schools.models import Arm, ClassLevel, Term


class Gender(enum.StrEnum):
    MALE = "male"
    FEMALE = "female"


class StudentStatus(enum.StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"  # left the school; history and balances are kept
    GRADUATED = "graduated"  # finished the highest class


class Relationship(enum.StrEnum):
    MOTHER = "mother"
    FATHER = "father"
    GUARDIAN = "guardian"
    OTHER = "other"


class ConsentMethod(enum.StrEnum):
    # The school confirmed it collected the guardian's consent (e.g. on its admission form)
    SCHOOL_ATTESTED = "school_attested"
    # The guardian agreed in the parent portal
    PORTAL = "portal"


class Student(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("school_id", "admission_number"),
        Index("ix_students_school_name", "school_id", "last_name", "first_name"),
    )

    admission_number: Mapped[str] = mapped_column(String(30))
    first_name: Mapped[str] = mapped_column(String(60))
    middle_name: Mapped[str] = mapped_column(String(60), default="")
    last_name: Mapped[str] = mapped_column(String(60))
    gender: Mapped[Gender | None] = mapped_column(enum_type(Gender, "gender"))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    status: Mapped[StudentStatus] = mapped_column(
        enum_type(StudentStatus, "student_status"), default=StudentStatus.ACTIVE
    )
    left_on: Mapped[date | None] = mapped_column(Date)

    guardian_links: Mapped[list["GuardianStudent"]] = relationship(back_populates="student", lazy="raise")
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="student", lazy="raise")

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p)


class Enrollment(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    """
    Where a student is for one term. One row per student per term, so class
    history is kept and each term's fees use that term's class.
    `class_level_id` is copied from the arm, and the composite foreign key
    guarantees it matches.
    """

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "term_id"),
        ForeignKeyConstraint(
            ["arm_id", "class_level_id"],
            ["arms.id", "arms.class_level_id"],
            name="fk_enrollments_arm_level",
            ondelete="RESTRICT",
        ),
        Index("ix_enrollments_term_level", "term_id", "class_level_id"),
        Index("ix_enrollments_term_arm", "term_id", "arm_id"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"))
    arm_id: Mapped[uuid.UUID] = mapped_column()
    class_level_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_levels.id", ondelete="RESTRICT"))

    student: Mapped[Student] = relationship(back_populates="enrollments", lazy="raise")
    term: Mapped[Term] = relationship(lazy="raise")
    arm: Mapped[Arm] = relationship(lazy="raise", foreign_keys=[arm_id], primaryjoin="Enrollment.arm_id == Arm.id")
    class_level: Mapped[ClassLevel] = relationship(lazy="raise")


class Guardian(TenantMixin, UUIDPrimaryKey, Timestamped, Base):
    """A parent or other adult responsible for one or more students at the school."""

    __tablename__ = "guardians"
    __table_args__ = (UniqueConstraint("school_id", "phone_index"),)

    full_name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str] = mapped_column(EncryptedString)  # E.164, encrypted
    phone_index: Mapped[str] = mapped_column(BLIND_INDEX_TYPE, index=True)
    email: Mapped[str | None] = mapped_column(EncryptedString)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_method: Mapped[ConsentMethod | None] = mapped_column(enum_type(ConsentMethod, "consent_method"))

    student_links: Mapped[list["GuardianStudent"]] = relationship(back_populates="guardian", lazy="raise")


class GuardianStudent(TenantMixin, Base):
    """Links guardians and students (one parent, many children; one child, several guardians)."""

    __tablename__ = "guardian_students"
    __table_args__ = (
        # The primary guardian receives fee notices and reminders
        Index("uq_guardian_students_one_primary", "student_id", unique=True, postgresql_where=text("is_primary")),
    )

    guardian_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"), primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    relationship_to_student: Mapped[Relationship] = mapped_column(
        enum_type(Relationship, "guardian_relationship"), default=Relationship.GUARDIAN
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    guardian: Mapped[Guardian] = relationship(back_populates="student_links", lazy="raise")
    student: Mapped[Student] = relationship(back_populates="guardian_links", lazy="raise")

"""
A new school's getting-started checklist, shown on its dashboard until every
step is done. Worked out from the school's data (nothing to tick by hand), in
one query. The session must be scoped to the school.
"""

from dataclasses import dataclass

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import Role, User
from app.modules.fees.models import FeeSchedule, Invoice
from app.modules.payments.models import SchoolPaymentAccount, VirtualAccount
from app.modules.schools.models import ClassLevel, School, Term
from app.modules.students.models import Student, StudentStatus


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    hint: str
    link: str  # console page that does it
    done: bool


def checklist(session: Session, school: School) -> list[Step]:
    current_term = select(Term.id).where(Term.is_current.is_(True)).scalar_subquery()
    (calendar, classes, students, fees, staff, bank, numbers, bills) = session.execute(
        select(
            exists().where(Term.is_current.is_(True)),
            exists().where(ClassLevel.is_active.is_(True)),
            exists().where(Student.status == StudentStatus.ACTIVE),
            exists().where(FeeSchedule.term_id == current_term),
            exists().where(and_(User.school_id == school.id, User.role == Role.BURSAR)),
            exists().where(SchoolPaymentAccount.school_id == school.id),
            exists().where(VirtualAccount.school_id == school.id),
            exists().where(Invoice.term_id == current_term),
        )
    ).one()
    return [
        Step("profile", "Add your school's address and phone", "They appear on receipts.", "/console/school",
             bool(school.address and school.phone)),
        Step("calendar", "Set up the school year", "Add this session's three terms and say which is current.",
             "/console/calendar", bool(calendar)),
        Step("classes", "Add your classes", "Tick them from the usual list (Creche to SSS 3) and add your own.",
             "/console/classes", bool(classes)),
        Step("students", "Add your students", "Upload your existing spreadsheet, with each child's parent.",
             "/console/students/import", bool(students)),
        Step("fees", "Set this term's fees", "What each class pays, optional items and discounts.", "/console/fees",
             bool(fees)),
        Step("staff", "Invite your bursar", "They'll manage fees and payments day to day.", "/console/staff",
             bool(staff)),
        Step("bank", "Connect the school's bank account", "Parents' payments settle straight into it (proprietor).",
             "/console/payment-settings", bool(bank)),
        Step("account_numbers", "Give students their account numbers",
             "Each child gets a bank account number; transfers into it are matched automatically.",
             "/console/payment-settings", bool(numbers)),
        Step("bills", "Send the term's bills", "Generate every student's bill in one click.", "/console/billing",
             bool(bills)),
    ]  # fmt: skip

"""
Role-based access control. Endpoints check a capability, never a role name,
so changing what a role may do is a one-line change here.
"""

import enum

from .models import Role


class Capability(enum.StrEnum):
    SCHOOL_VIEW = "school.view"
    SCHOOL_MANAGE = "school.manage"
    STAFF_VIEW = "staff.view"
    STAFF_MANAGE = "staff.manage"
    AUDIT_VIEW = "audit.view"
    STUDENTS_VIEW = "students.view"
    STUDENTS_MANAGE = "students.manage"
    FEES_VIEW = "fees.view"
    FEES_MANAGE = "fees.manage"
    INVOICES_ISSUE = "invoices.issue"
    PAYMENTS_VIEW = "payments.view"
    PAYMENTS_RECORD = "payments.record"
    PAYMENTS_APPROVE = "payments.approve"
    PAYMENTS_REVERSE = "payments.reverse"
    # Choosing the bank account school money settles into: proprietors only
    PAYMENTS_SETUP = "payments.setup"
    REPORTS_VIEW = "reports.view"
    # Re-opening a closed term: the proprietor only (proprietors hold every school capability)
    BOOKS_REOPEN = "books.reopen"
    # Approving a write-off of debt: the proprietor only (owner's rule, 2026-10-24)
    DEBTS_WRITE_OFF = "debts.write_off"
    MESSAGING_MANAGE = "messaging.manage"
    PLATFORM_MANAGE = "platform.manage"


C = Capability

ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    # The owner: sees and controls everything in their school
    Role.PROPRIETOR: frozenset(c for c in Capability if c is not C.PLATFORM_MANAGE),
    # Money: billing, payments, reconciliation, reminders. Can't manage staff or school setup.
    Role.BURSAR: frozenset(
        {
            C.SCHOOL_VIEW,
            C.STAFF_VIEW,
            C.STUDENTS_VIEW,
            C.FEES_VIEW,
            C.FEES_MANAGE,
            C.INVOICES_ISSUE,
            C.PAYMENTS_VIEW,
            C.PAYMENTS_RECORD,
            C.PAYMENTS_APPROVE,
            C.PAYMENTS_REVERSE,
            C.REPORTS_VIEW,
            C.MESSAGING_MANAGE,
            C.AUDIT_VIEW,
        }
    ),
    # Runs the school day to day (owner's decision, 2026-10-20): records, school setup and everything the
    # bursar does. Only the proprietor manages staff and where the money settles. Cash still needs a
    # second person to approve it, so an admin can't approve their own entry.
    Role.SCHOOL_ADMIN: frozenset(
        {
            C.SCHOOL_VIEW,
            C.SCHOOL_MANAGE,
            C.STAFF_VIEW,
            C.STUDENTS_VIEW,
            C.STUDENTS_MANAGE,
            C.FEES_VIEW,
            C.FEES_MANAGE,
            C.INVOICES_ISSUE,
            C.PAYMENTS_VIEW,
            C.PAYMENTS_RECORD,
            C.PAYMENTS_APPROVE,
            C.PAYMENTS_REVERSE,
            C.REPORTS_VIEW,
            C.MESSAGING_MANAGE,
            C.AUDIT_VIEW,
        }
    ),
    Role.PLATFORM_ADMIN: frozenset({C.PLATFORM_MANAGE}),
    Role.PARENT: frozenset(),
}

# Roles that must enrol in two-factor sign-in before using anything (when ENFORCE_MFA is on): everyone who
# can move money or change who can
MFA_REQUIRED_ROLES = frozenset({Role.PLATFORM_ADMIN, Role.PROPRIETOR, Role.BURSAR, Role.SCHOOL_ADMIN})


def role_has(role: Role, capability: Capability) -> bool:
    return capability in ROLE_CAPABILITIES[role]

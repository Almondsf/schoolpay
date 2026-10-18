"""
Where money goes (the school's settlement account) and how parents pay in
(each student's virtual account).
"""

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.config import get_settings
from app.core.errors import Conflict, DomainError
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record
from app.modules.schools.models import School
from app.modules.students.models import Guardian, GuardianStudent, Student, StudentStatus

from .gateways import GatewayCode, GatewayError, GatewayRejected, enabled_gateways, get_gateway
from .gateways.base import Bank, CustomerDetails
from .models import SchoolPaymentAccount, VirtualAccount, VirtualAccountStatus

logger = logging.getLogger(__name__)
PROVISION_BATCH = 100


def payment_account(session: Session) -> SchoolPaymentAccount | None:
    return session.scalar(select(SchoolPaymentAccount))


def require_payment_account(session: Session) -> SchoolPaymentAccount:
    account = payment_account(session)
    if account is None:
        raise DomainError("Set up the school's bank account for payments first.", code="payments_not_set_up")
    return account


def list_banks(gateway: GatewayCode) -> list[Bank]:
    return get_gateway(gateway).list_banks()


def set_up_payment_account(
    session: Session, ctx: StaffContext, *, gateway: GatewayCode, bank_code: str, account_number: str
) -> SchoolPaymentAccount:
    """
    Connect the school's own bank account. The gateway confirms the account
    name, and a subaccount is created so payments settle straight to the school
    (minus the platform fee). Changing it after virtual accounts exist needs support.
    """
    if gateway not in enabled_gateways():
        raise DomainError("That payment provider isn't available.", field="gateway")
    if not (account_number.isdigit() and len(account_number) == 10):
        raise DomainError("Enter the 10-digit account number.", field="account_number")
    if session.scalar(select(VirtualAccount.id).limit(1)) is not None:
        raise Conflict(
            "Students already have account numbers linked to the current bank account. Contact support "
            "to change where payments settle."
        )
    provider = get_gateway(gateway)
    resolved = provider.resolve_account(bank_code=bank_code, account_number=account_number)
    bank_name = next((b.name for b in provider.list_banks() if b.code == bank_code), bank_code)
    school = session.get(School, ctx.school_id)
    fee_bp = get_settings().platform_fee_bp
    subaccount = provider.create_subaccount(
        business_name=school.name if school else "School",
        bank_code=bank_code,
        account_number=account_number,
        platform_fee_bp=fee_bp,
    )
    account = payment_account(session) or SchoolPaymentAccount(id=uuid.uuid4())
    before = (
        {"gateway": account.gateway, "bank": account.bank_name, "last4": account.account_number_last4}
        if account.subaccount_code
        else None
    )
    account.gateway, account.subaccount_code = gateway, subaccount.code
    account.bank_code, account.bank_name = bank_code, bank_name
    account.account_number, account.account_number_last4 = account_number, account_number[-4:]
    account.account_name, account.platform_fee_bp, account.set_up_by = resolved.account_name, fee_bp, ctx.user.id
    session.add(account)
    record(
        session,
        AuditAction.PAYMENT_ACCOUNT_SET_UP,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=account,
        changes={
            "before": before,
            "gateway": gateway,
            "bank": bank_name,
            "last4": account_number[-4:],
            "account_name": resolved.account_name,
            "platform_fee_bp": fee_bp,
        },
    )
    session.commit()
    return account


# --- Virtual accounts ------------------------------------------------------------------------------


def request_virtual_accounts(session: Session, ctx: StaffContext, student_ids: list[uuid.UUID] | None) -> int:
    """Queue account numbers for active students who don't have one. Returns how many will be requested."""
    require_payment_account(session)
    stmt = (
        select(Student.id)
        .outerjoin(
            VirtualAccount,
            and_(VirtualAccount.student_id == Student.id, VirtualAccount.status == VirtualAccountStatus.ACTIVE),
        )
        .where(Student.status == StudentStatus.ACTIVE, VirtualAccount.id.is_(None))
        .order_by(Student.id)
    )
    if student_ids:
        stmt = stmt.where(Student.id.in_(student_ids))
    missing = [str(sid) for sid in session.scalars(stmt)]
    for start in range(0, len(missing), PROVISION_BATCH):
        outbox.enqueue(
            session,
            "payments.provision_virtual_accounts",
            {"school_id": str(ctx.school_id), "student_ids": missing[start : start + PROVISION_BATCH]},
        )
    record(
        session,
        AuditAction.VIRTUAL_ACCOUNTS_ISSUED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        changes={"requested": len(missing)},
    )
    session.commit()
    return len(missing)


@dataclass
class ProvisionResult:
    created: int = 0
    failed: list[str] = field(default_factory=list)


def provision_virtual_accounts(session: Session, school_id: uuid.UUID, student_ids: list[uuid.UUID]) -> ProvisionResult:
    """
    Ask the gateway for each student's account number (one API call each, as the
    gateway requires), then save them all in one insert. Students who already
    have one are skipped, so a retried task is harmless.
    """
    result = ProvisionResult()
    account = require_payment_account(session)
    school = session.get(School, school_id)
    rows = session.execute(
        select(Student.id, Student.first_name, Student.last_name, Guardian.phone, Guardian.email)
        .outerjoin(
            VirtualAccount,
            and_(VirtualAccount.student_id == Student.id, VirtualAccount.status == VirtualAccountStatus.ACTIVE),
        )
        .outerjoin(GuardianStudent, and_(GuardianStudent.student_id == Student.id, GuardianStudent.is_primary))
        .outerjoin(Guardian, Guardian.id == GuardianStudent.guardian_id)
        .where(Student.id.in_(student_ids), Student.status == StudentStatus.ACTIVE, VirtualAccount.id.is_(None))
    ).all()
    provider = get_gateway(account.gateway)
    issued: list[VirtualAccount] = []
    for student_id, first, last, phone, email in rows:
        # Gateways insist on an email; parents often have none, so the school's routing address stands in
        contact = email or f"student-{student_id}@{school.slug if school else 'school'}.schoolpay.ng"
        try:
            created = provider.create_virtual_account(
                customer=CustomerDetails(
                    reference=str(student_id), first_name=first, last_name=last, email=contact, phone=phone or ""
                ),
                subaccount_code=account.subaccount_code,
            )
        except (GatewayError, GatewayRejected) as e:
            logger.warning("Virtual account for student %s failed: %s", student_id, e.message)
            result.failed.append(str(student_id))
            continue
        issued.append(
            VirtualAccount(
                school_id=school_id,
                student_id=student_id,
                gateway=account.gateway,
                account_number=created.account_number,
                account_name=created.account_name,
                bank_name=created.bank_name,
                provider_reference=created.provider_reference,
            )
        )
    session.add_all(issued)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()  # another run got there first; the next request picks up whoever is still missing
        raise
    result.created = len(issued)
    record(
        session,
        AuditAction.VIRTUAL_ACCOUNTS_ISSUED,
        school_id=school_id,
        actor=Actor.system("worker"),
        changes={"created": result.created, "failed": len(result.failed)},
    )
    session.commit()
    return result

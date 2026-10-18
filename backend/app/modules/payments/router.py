import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES, NotFound
from app.core.http import RequestMeta, request_meta
from app.core.pagination import Page, PageParams, page_params, paginate_rows
from app.core.ratelimit import get_limiter
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability
from app.modules.fees.setup_service import MAX_AMOUNT_KOBO
from app.modules.receipts.service import receipts_for_payments
from app.modules.students.models import Student

from . import accounts, checkout, manual, service, webhooks
from .allocation import credit_by_student
from .gateways import FakeGateway, GatewayCode, InvalidSignature, enabled_gateways
from .models import (
    IntentStatus,
    ManualPayment,
    ManualPaymentStatus,
    Payment,
    PaymentKind,
    PaymentMethod,
    VirtualAccount,
    VirtualAccountStatus,
)

router = APIRouter(tags=["payments"], responses=ERROR_RESPONSES)
webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"], include_in_schema=False)
VIEW = Depends(require(Capability.PAYMENTS_VIEW))
RECORD = Depends(require(Capability.PAYMENTS_RECORD))
APPROVE = Depends(require(Capability.PAYMENTS_APPROVE))
REVERSE = Depends(require(Capability.PAYMENTS_REVERSE))
SETUP = Depends(require(Capability.PAYMENTS_SETUP))

Kobo = Annotated[int, Field(gt=0, le=MAX_AMOUNT_KOBO, description="Amount in kobo")]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


# --- Schemas ---------------------------------------------------------------------------------------


class BankOut(BaseModel):
    code: str
    name: str


class PaymentAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    gateway: GatewayCode
    bank_name: str
    account_number_last4: str
    account_name: str
    platform_fee_bp: int


class PaymentAccountIn(BaseModel):
    gateway: GatewayCode
    bank_code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=20)]
    account_number: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{10}$")]


class PaymentSettingsOut(BaseModel):
    gateways: list[GatewayCode]
    account: PaymentAccountOut | None


class VirtualAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_number: str
    account_name: str
    bank_name: str
    gateway: GatewayCode


class ProvisionIn(BaseModel):
    student_ids: list[uuid.UUID] | None = Field(default=None, description="Default: every active student without one")


class ProvisionOut(BaseModel):
    requested: int


class StudentBrief(BaseModel):
    id: uuid.UUID
    admission_number: str
    full_name: str


class AppliedOut(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: int
    amount_kobo: int


class PaymentOut(BaseModel):
    id: uuid.UUID
    student: StudentBrief
    kind: PaymentKind
    amount_kobo: int
    method: PaymentMethod
    gateway: GatewayCode | None
    gateway_reference: str | None
    reference: str
    note: str
    received_at: datetime
    reversed: bool
    reverses_payment_id: uuid.UUID | None
    applied: list[AppliedOut]
    unapplied_kobo: int = Field(description="Kept as credit for the student's next invoices")
    receipt_id: uuid.UUID | None
    receipt_number: int | None


class ReverseIn(BaseModel):
    reason: Reason


class ManualIn(BaseModel):
    student_id: uuid.UUID
    amount_kobo: Kobo
    method: Literal[PaymentMethod.CASH, PaymentMethod.TELLER, PaymentMethod.POS, PaymentMethod.BANK_TRANSFER]
    received_on: date
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class ManualOut(BaseModel):
    id: uuid.UUID
    student: StudentBrief
    amount_kobo: int
    method: PaymentMethod
    received_on: date
    reference: str
    note: str
    status: ManualPaymentStatus
    recorded_by: uuid.UUID
    decided_by: uuid.UUID | None
    rejection_reason: str
    payment_id: uuid.UUID | None
    created_at: datetime


class PaymentLinkIn(BaseModel):
    email: EmailStr
    amount_kobo: Kobo | None = Field(default=None, description="Default: everything the student owes")


class PaymentLinkOut(BaseModel):
    reference: str
    amount_kobo: int
    authorization_url: str


class VerifyIn(BaseModel):
    reference: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^SP[0-9A-F]{20}$")]


class VerifyOut(BaseModel):
    reference: str
    status: IntentStatus
    amount_kobo: int


class SimulateTransferIn(BaseModel):
    account_number: Annotated[str, StringConstraints(pattern=r"^\d{10}$")]
    amount_kobo: Kobo


# --- Helpers ---------------------------------------------------------------------------------------


def _brief(student: Student) -> StudentBrief:
    return StudentBrief(id=student.id, admission_number=student.admission_number, full_name=student.full_name)


def _payments_out(db: Session, rows: list[tuple[Payment, Student]]) -> list[PaymentOut]:
    ids = [p.id for p, _ in rows]
    applied, reversed_ = service.applied_to(db, ids), service.reversed_ids(db, ids)
    receipts = receipts_for_payments(db, ids)
    out = []
    for payment, student in rows:
        used = [AppliedOut(**a.__dict__) for a in applied[payment.id]]
        receipt = receipts.get(payment.id)
        out.append(
            PaymentOut(
                id=payment.id,
                student=_brief(student),
                kind=payment.kind,
                amount_kobo=payment.amount_kobo,
                method=payment.method,
                gateway=payment.gateway,
                gateway_reference=payment.gateway_reference,
                reference=payment.reference,
                note=payment.note,
                received_at=payment.received_at,
                reversed=payment.id in reversed_,
                reverses_payment_id=payment.reverses_payment_id,
                applied=used,
                unapplied_kobo=payment.amount_kobo - sum(a.amount_kobo for a in used),
                receipt_id=receipt.id if receipt else None,
                receipt_number=receipt.number if receipt else None,
            )
        )
    return out


def _manual_out(entry: ManualPayment, student: Student) -> ManualOut:
    return ManualOut(
        id=entry.id,
        student=_brief(student),
        amount_kobo=entry.amount_kobo,
        method=entry.method,
        received_on=entry.received_on,
        reference=entry.reference,
        note=entry.note,
        status=entry.status,
        recorded_by=entry.recorded_by,
        decided_by=entry.decided_by,
        rejection_reason=entry.rejection_reason,
        payment_id=entry.payment_id,
        created_at=entry.created_at,
    )


# --- Setup -----------------------------------------------------------------------------------------


@router.get(
    "/payment-settings",
    response_model=PaymentSettingsOut,
    summary="Which gateways are available and where the school's money settles",
)
def get_settings_(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> PaymentSettingsOut:
    account = accounts.payment_account(db)
    return PaymentSettingsOut(
        gateways=enabled_gateways(), account=PaymentAccountOut.model_validate(account) if account else None
    )


@router.get("/payment-settings/banks", response_model=list[BankOut], summary="Banks the gateway can pay out to")
def list_banks(gateway: GatewayCode, ctx: StaffContext = SETUP) -> list[BankOut]:
    return [BankOut(code=b.code, name=b.name) for b in accounts.list_banks(gateway)]


@router.put(
    "/payment-settings",
    response_model=PaymentAccountOut,
    summary="Connect the school's bank account (the gateway confirms the account name)",
)
def set_up_account(
    body: PaymentAccountIn, ctx: StaffContext = SETUP, db: Session = Depends(get_db)
) -> PaymentAccountOut:
    get_limiter().hit("payment-setup:school", "10/hour", str(ctx.school_id))
    return PaymentAccountOut.model_validate(
        accounts.set_up_payment_account(
            db, ctx, gateway=body.gateway, bank_code=body.bank_code, account_number=body.account_number
        )
    )


@router.post(
    "/virtual-accounts/provision",
    response_model=ProvisionOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request account numbers for students who don't have one (done in the background)",
)
def provision(
    body: ProvisionIn, ctx: StaffContext = Depends(require(Capability.PAYMENTS_APPROVE)), db: Session = Depends(get_db)
) -> ProvisionOut:
    return ProvisionOut(requested=accounts.request_virtual_accounts(db, ctx, body.student_ids))


@router.get(
    "/students/{student_id}/virtual-account",
    response_model=VirtualAccountOut | None,
    summary="The account number this student's fees can be paid into",
)
def get_virtual_account(
    student_id: uuid.UUID, ctx: StaffContext = Depends(require(Capability.STUDENTS_VIEW)), db: Session = Depends(get_db)
) -> VirtualAccount | None:
    get_or_404(db, Student, student_id, label="Student")
    return db.scalar(
        select(VirtualAccount).where(
            VirtualAccount.student_id == student_id, VirtualAccount.status == VirtualAccountStatus.ACTIVE
        )
    )


# --- Payments --------------------------------------------------------------------------------------


@router.get("/payments", response_model=Page[PaymentOut], summary="Payments received, newest first")
def list_payments(
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
    params: PageParams = Depends(page_params),
    student_id: uuid.UUID | None = None,
    method: PaymentMethod | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = Query(default=None, max_length=100),
) -> Page[PaymentOut]:
    stmt = service.payments_query(student_id=student_id, method=method, since=since, until=until, search=search)
    rows, total = paginate_rows(db, stmt, params)
    return Page(
        items=_payments_out(db, [tuple(r) for r in rows]), total=total, limit=params.limit, offset=params.offset
    )


@router.get("/payments/{payment_id}", response_model=PaymentOut, summary="A payment and where it was applied")
def get_payment(payment_id: uuid.UUID, ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> PaymentOut:
    row = db.execute(service.payments_query().where(Payment.id == payment_id)).first()
    if row is None:
        raise NotFound("Payment not found.")
    payment, student = row
    return _payments_out(db, [(payment, student)])[0]


@router.post(
    "/payments/{payment_id}/reverse",
    response_model=PaymentOut,
    summary="Reverse a payment recorded in error (adds a negative entry; nothing is deleted)",
)
def reverse(
    payment_id: uuid.UUID, body: ReverseIn, ctx: StaffContext = REVERSE, db: Session = Depends(get_db)
) -> PaymentOut:
    reversal = service.reverse_payment(db, ctx, payment_id, body.reason)
    return get_payment(reversal.id, ctx, db)


@router.get(
    "/students/{student_id}/credit",
    response_model=int,
    summary="Money the student has paid that isn't applied to an invoice yet (kobo)",
)
def student_credit(student_id: uuid.UUID, ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> int:
    get_or_404(db, Student, student_id, label="Student")
    return credit_by_student(db, [student_id])[student_id]


# --- Manual payments -------------------------------------------------------------------------------


@router.post(
    "/manual-payments",
    response_model=ManualOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record cash, teller, POS or a direct transfer; someone else approves it",
)
def record_manual(body: ManualIn, ctx: StaffContext = RECORD, db: Session = Depends(get_db)) -> ManualOut:
    entry = manual.record_manual(
        db,
        ctx,
        student_id=body.student_id,
        amount_kobo=body.amount_kobo,
        method=body.method,
        received_on=body.received_on,
        reference=body.reference,
        note=body.note,
    )
    return _manual_out(entry, get_or_404(db, Student, entry.student_id))


@router.get("/manual-payments", response_model=Page[ManualOut], summary="Manual payments (default: awaiting approval)")
def list_manual(
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
    params: PageParams = Depends(page_params),
    status_filter: ManualPaymentStatus | None = Query(default=ManualPaymentStatus.PENDING, alias="status"),
) -> Page[ManualOut]:
    rows, total = paginate_rows(db, manual.manual_query(status_filter), params)
    return Page(items=[_manual_out(e, s) for e, s in rows], total=total, limit=params.limit, offset=params.offset)


@router.post(
    "/manual-payments/{entry_id}/approve",
    response_model=ManualOut,
    summary="Approve (must be someone other than who recorded it)",
)
def approve_manual(entry_id: uuid.UUID, ctx: StaffContext = APPROVE, db: Session = Depends(get_db)) -> ManualOut:
    entry = manual.approve(db, ctx, entry_id)
    return _manual_out(entry, get_or_404(db, Student, entry.student_id))


@router.post("/manual-payments/{entry_id}/reject", response_model=ManualOut, summary="Reject with a reason")
def reject_manual(
    entry_id: uuid.UUID, body: ReverseIn, ctx: StaffContext = APPROVE, db: Session = Depends(get_db)
) -> ManualOut:
    entry = manual.reject(db, ctx, entry_id, body.reason)
    return _manual_out(entry, get_or_404(db, Student, entry.student_id))


# --- Card checkout ---------------------------------------------------------------------------------


@router.post(
    "/students/{student_id}/payment-link",
    response_model=PaymentLinkOut,
    status_code=status.HTTP_201_CREATED,
    summary="A card checkout link to send to the parent",
)
def payment_link(
    student_id: uuid.UUID, body: PaymentLinkIn, ctx: StaffContext = RECORD, db: Session = Depends(get_db)
) -> PaymentLinkOut:
    intent = checkout.start_checkout(
        db,
        student_id=student_id,
        amount_kobo=body.amount_kobo,
        email=body.email,
        actor=ctx.actor,
        meta=ctx.meta,
        created_by=ctx.user.id,
    )
    return PaymentLinkOut(
        reference=intent.reference, amount_kobo=intent.amount_kobo, authorization_url=intent.authorization_url
    )


@router.post(
    "/payments/verify",
    response_model=VerifyOut,
    summary="After the gateway redirects back: confirm the checkout (the outcome comes from the gateway)",
)
def verify_checkout(body: VerifyIn, meta: RequestMeta = Depends(request_meta)) -> VerifyOut:
    get_limiter().hit("payment-verify:ip", "30/minute", meta.ip)
    intent = checkout.confirm(body.reference)
    if intent is None:
        raise NotFound("Payment not found.")
    return VerifyOut(reference=intent.reference, status=intent.status, amount_kobo=intent.amount_kobo)


# --- Webhooks --------------------------------------------------------------------------------------


async def _webhook(gateway: GatewayCode, request: Request, db: Session) -> Response:
    if gateway not in enabled_gateways():
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    get_limiter().hit("webhook:ip", "600/minute", request.client.host if request.client else "")
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:  # the DB work is synchronous: keep it off the event loop
        await run_in_threadpool(webhooks.receive, db, gateway, headers, body)
    except InvalidSignature:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    return Response(status_code=status.HTTP_200_OK)


@webhook_router.post("/paystack")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    return await _webhook(GatewayCode.PAYSTACK, request, db)


@webhook_router.post("/flutterwave")
async def flutterwave_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    return await _webhook(GatewayCode.FLUTTERWAVE, request, db)


@webhook_router.post("/fake")
async def fake_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    return await _webhook(GatewayCode.FAKE, request, db)


# --- Development only: pretend a parent paid --------------------------------------------------------

dev_router = APIRouter(prefix="/dev/fake-gateway", tags=["development"], include_in_schema=False)


@dev_router.post("/transfers", status_code=status.HTTP_202_ACCEPTED)
def simulate_transfer(body: SimulateTransferIn, db: Session = Depends(get_db)) -> dict[str, str]:
    """A transfer into a fake virtual account, delivered as a signed webhook. Development and tests only."""
    if get_settings().is_production or GatewayCode.FAKE not in enabled_gateways():
        raise NotFound("Not found.")
    headers, payload = FakeGateway.shared().simulate_transfer(body.account_number, body.amount_kobo)
    webhooks.receive(db, GatewayCode.FAKE, headers, payload)
    return {"status": "sent"}

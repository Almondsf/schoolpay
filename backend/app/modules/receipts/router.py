import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES, NotFound
from app.core.http import RequestMeta, request_meta
from app.core.ratelimit import get_limiter
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability

from . import service
from .models import Receipt
from .pdf import render_receipt

router = APIRouter(tags=["receipts"], responses=ERROR_RESPONSES)
public_router = APIRouter(prefix="/public", tags=["public"], responses=ERROR_RESPONSES)
VIEW = Depends(require(Capability.PAYMENTS_VIEW))
PDF = "application/pdf"


class ReceiptOut(BaseModel):
    id: uuid.UUID
    number: int
    payment_id: uuid.UUID
    student_id: uuid.UUID
    amount_kobo: int
    issued_at: datetime
    reversed: bool
    verification_url: str


class PublicReceiptOut(BaseModel):
    school_name: str
    receipt_number: int
    amount_kobo: int
    paid_on: datetime
    status: Literal["valid", "reversed"]
    student_initials: str


def _out(db: Session, receipt: Receipt) -> ReceiptOut:
    return ReceiptOut(
        id=receipt.id,
        number=receipt.number,
        payment_id=receipt.payment_id,
        student_id=receipt.student_id,
        amount_kobo=receipt.amount_kobo,
        issued_at=receipt.issued_at,
        reversed=service.is_reversed(db, receipt.payment_id),
        verification_url=service.verification_url(receipt.verification_code),
    )


@router.get("/payments/{payment_id}/receipt", response_model=ReceiptOut, summary="A payment's receipt")
def payment_receipt(payment_id: uuid.UUID, ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> ReceiptOut:
    receipt = db.scalar(select(Receipt).where(Receipt.payment_id == payment_id))
    if receipt is None:
        raise NotFound("Receipt not found.")
    return _out(db, receipt)


@router.get(
    "/receipts/{receipt_id}/pdf",
    summary="Download a receipt as PDF",
    response_class=Response,
    responses={200: {"content": {PDF: {}}}},
)
def receipt_pdf(receipt_id: uuid.UUID, ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> Response:
    receipt = get_or_404(db, Receipt, receipt_id, label="Receipt")
    content = render_receipt(receipt, reversed_=service.is_reversed(db, receipt.payment_id))
    return Response(
        content, media_type=PDF, headers={"Content-Disposition": f'attachment; filename="receipt-{receipt.number}.pdf"'}
    )


@public_router.get(
    "/receipts/{code}",
    response_model=PublicReceiptOut,
    summary="Check a receipt is genuine (no sign-in; shows no personal details beyond initials)",
)
def verify_receipt(
    code: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{16,32}$")], meta: RequestMeta = Depends(request_meta)
) -> PublicReceiptOut:
    get_limiter().hit("receipt-verify:ip", "60/minute", meta.ip)
    found = service.public_lookup(code)
    if found is None:
        raise NotFound("No receipt matches this code. It may be fake.")
    return PublicReceiptOut(**found.__dict__)

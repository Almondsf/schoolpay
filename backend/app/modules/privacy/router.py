import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES
from app.core.ratelimit import get_limiter
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability
from app.modules.parents.deps import ParentContext, get_parent

from . import service

router = APIRouter(tags=["privacy"], responses=ERROR_RESPONSES)
parent_router = APIRouter(prefix="/parent", tags=["parent"], responses=ERROR_RESPONSES)
EXPORT_RATE = "20/hour"


class EraseIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


class ErasedOut(BaseModel):
    erased: bool


def _json_download(data: dict[str, object], name: str) -> Response:
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}.json"', "Cache-Control": "no-store"},
    )


@router.get(
    "/guardians/{guardian_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/json": {}}}},
    summary="Everything held about a parent and their children (a parent's request for their data)",
)
def export_guardian(
    guardian_id: uuid.UUID,
    ctx: StaffContext = Depends(require(Capability.STUDENTS_MANAGE)),
    db: Session = Depends(get_db),
) -> Response:
    get_limiter().hit("privacy-export:school", EXPORT_RATE, str(ctx.school_id))
    return _json_download(service.export_guardian(db, ctx, guardian_id), "parent-data")


@router.post(
    "/guardians/{guardian_id}/erase",
    response_model=ErasedOut,
    summary="Erase a parent's personal details (none of their children may be current pupils)",
)
def erase_guardian(
    guardian_id: uuid.UUID,
    body: EraseIn,
    ctx: StaffContext = Depends(require(Capability.SCHOOL_MANAGE)),
    db: Session = Depends(get_db),
) -> ErasedOut:
    service.erase_guardian(db, ctx, guardian_id, body.reason)
    return ErasedOut(erased=True)


@router.post(
    "/students/{student_id}/erase",
    response_model=ErasedOut,
    summary="Erase a former pupil's personal details (bills and payments stay, anonymised)",
)
def erase_student(
    student_id: uuid.UUID,
    body: EraseIn,
    ctx: StaffContext = Depends(require(Capability.SCHOOL_MANAGE)),
    db: Session = Depends(get_db),
) -> ErasedOut:
    service.erase_student(db, ctx, student_id, body.reason)
    return ErasedOut(erased=True)


@parent_router.get(
    "/my-data",
    response_class=Response,
    responses={200: {"content": {"application/json": {}}}},
    summary="Download everything the school holds about you and your children",
)
def my_data(ctx: ParentContext = Depends(get_parent), db: Session = Depends(get_db)) -> Response:
    get_limiter().hit("privacy-export:guardian", "5/hour", str(ctx.guardian_id))
    return _json_download(service.export_guardian(db, ctx, ctx.guardian_id), "my-data")

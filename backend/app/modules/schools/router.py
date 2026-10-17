import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES
from app.core.pagination import Page, PageParams, page_params, paginate
from app.modules.accounts.deps import Principal, StaffContext, require, require_platform_admin
from app.modules.accounts.permissions import Capability

from . import service
from .models import SchoolStatus
from .schemas import SchoolCreateIn, SchoolOut, SchoolStatusIn, SchoolUpdateIn

school_router = APIRouter(prefix="/school", tags=["school"], responses=ERROR_RESPONSES)
platform_schools_router = APIRouter(prefix="/platform/schools", tags=["platform"], responses=ERROR_RESPONSES)


@school_router.get("", response_model=SchoolOut, summary="The signed-in user's school")
def get_school(
    ctx: StaffContext = Depends(require(Capability.SCHOOL_VIEW)), db: Session = Depends(get_db)
) -> SchoolOut:
    return SchoolOut.model_validate(service.current_school(db, ctx))


@school_router.patch("", response_model=SchoolOut, summary="Update the school's profile")
def update_school(
    body: SchoolUpdateIn, ctx: StaffContext = Depends(require(Capability.SCHOOL_MANAGE)), db: Session = Depends(get_db)
) -> SchoolOut:
    changes = {k: str(v) for k, v in body.model_dump(exclude_none=True).items()}
    return SchoolOut.model_validate(service.update_profile(db, ctx, changes))


@platform_schools_router.post(
    "",
    response_model=SchoolOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a school and invite its proprietor",
)
def create_school(
    body: SchoolCreateIn, admin: Principal = Depends(require_platform_admin), db: Session = Depends(get_db)
) -> SchoolOut:
    school, _ = service.create_school(
        db,
        admin,
        name=body.name,
        slug=body.slug,
        proprietor_email=body.proprietor_email,
        proprietor_name=body.proprietor_name,
    )
    return SchoolOut.model_validate(school)


@platform_schools_router.get("", response_model=Page[SchoolOut], summary="All schools")
def list_schools(
    admin: Principal = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    params: PageParams = Depends(page_params),
    search: str | None = Query(default=None, max_length=100),
) -> Page[SchoolOut]:
    rows, total = paginate(db, service.list_schools(search), params)
    return Page(
        items=[SchoolOut.model_validate(s) for s in rows], total=total, limit=params.limit, offset=params.offset
    )


@platform_schools_router.post(
    "/{school_id}/suspend", response_model=SchoolOut, summary="Suspend a school (signs out its staff)"
)
def suspend_school(
    school_id: uuid.UUID,
    body: SchoolStatusIn,
    admin: Principal = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> SchoolOut:
    return SchoolOut.model_validate(service.set_status(db, admin, school_id, SchoolStatus.SUSPENDED, body.reason))


@platform_schools_router.post("/{school_id}/reactivate", response_model=SchoolOut, summary="Reactivate a school")
def reactivate_school(
    school_id: uuid.UUID,
    body: SchoolStatusIn,
    admin: Principal = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> SchoolOut:
    return SchoolOut.model_validate(service.set_status(db, admin, school_id, SchoolStatus.ACTIVE, body.reason))

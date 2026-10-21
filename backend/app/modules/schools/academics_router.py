import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability

from . import academics_service as service
from .models import AcademicSession, ClassLevel, Term

router = APIRouter(tags=["academics"], responses=ERROR_RESPONSES)
SessionName = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}/\d{4}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
ArmName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]


class TermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    number: int
    label: str
    start_date: date
    end_date: date
    is_current: bool


class AcademicSessionOut(BaseModel):
    id: uuid.UUID
    name: str
    start_date: date
    end_date: date
    terms: list[TermOut]

    @classmethod
    def of(cls, s: AcademicSession) -> "AcademicSessionOut":
        return cls(
            id=s.id,
            name=s.name,
            start_date=s.start_date,
            end_date=s.end_date,
            terms=[TermOut.model_validate(t) for t in s.terms],
        )


class CurrentTermOut(TermOut):
    session_name: str


class TermDatesIn(BaseModel):
    start_date: date
    end_date: date


class AcademicSessionIn(BaseModel):
    name: SessionName = Field(description='e.g. "2026/2027"')
    start_date: date
    end_date: date
    terms: list[TermDatesIn] = Field(min_length=3, max_length=3)
    make_first_term_current: bool = False


class AcademicSessionUpdateIn(BaseModel):
    name: SessionName | None = None
    start_date: date | None = None
    end_date: date | None = None


class ArmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool


class ClassLevelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    rank: int
    is_active: bool
    arms: list[ArmOut]


class ClassLevelIn(BaseModel):
    name: Name
    arms: list[ArmName] = Field(default_factory=lambda: ["A"], max_length=20)


class ClassLevelsIn(BaseModel):
    classes: list[ClassLevelIn] = Field(min_length=1, max_length=60)


class StandardClassOut(BaseModel):
    group: str = Field(description="Preparatory, Primary, Junior secondary or Senior secondary")
    name: str
    exists: bool = Field(description="The school already has a class by this name")


class ClassLevelUpdateIn(BaseModel):
    name: Name | None = None
    is_active: bool | None = None


class ClassOrderIn(BaseModel):
    class_level_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class ArmIn(BaseModel):
    name: ArmName


class ArmUpdateIn(BaseModel):
    name: ArmName | None = None
    is_active: bool | None = None


VIEW = Depends(require(Capability.SCHOOL_VIEW))
MANAGE = Depends(require(Capability.SCHOOL_MANAGE))


# --- Calendar -------------------------------------------------------------------------------------


@router.get("/academic-sessions", response_model=list[AcademicSessionOut], summary="Sessions and their terms")
def list_sessions(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> list[AcademicSessionOut]:
    return [AcademicSessionOut.of(s) for s in service.list_sessions(db)]


@router.post(
    "/academic-sessions",
    response_model=AcademicSessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a session with its three terms",
)
def create_session(
    body: AcademicSessionIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> AcademicSessionOut:
    created = service.create_session(
        db,
        ctx,
        name=body.name,
        start_date=body.start_date,
        end_date=body.end_date,
        terms=[service.TermDates(t.start_date, t.end_date) for t in body.terms],
        make_first_term_current=body.make_first_term_current,
    )
    return AcademicSessionOut.of(created)


@router.patch("/academic-sessions/{session_id}", response_model=AcademicSessionOut, summary="Rename or re-date")
def update_session(
    session_id: uuid.UUID, body: AcademicSessionUpdateIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> AcademicSessionOut:
    return AcademicSessionOut.of(
        service.update_session(db, ctx, session_id, name=body.name, start_date=body.start_date, end_date=body.end_date)
    )


@router.get("/terms/current", response_model=CurrentTermOut | None, summary="The current term, if one is set")
def get_current_term(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> CurrentTermOut | None:
    term = service.current_term(db)
    return _current_out(term) if term else None


def _current_out(term: Term) -> CurrentTermOut:
    return CurrentTermOut(**TermOut.model_validate(term).model_dump(), session_name=term.session.name)


@router.patch("/terms/{term_id}", response_model=TermOut, summary="Change a term's dates")
def update_term(
    term_id: uuid.UUID, body: TermDatesIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> TermOut:
    return TermOut.model_validate(
        service.update_term(db, ctx, term_id, service.TermDates(body.start_date, body.end_date))
    )


@router.post("/terms/{term_id}/make-current", response_model=TermOut, summary="Make this the current term")
def make_current(term_id: uuid.UUID, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> TermOut:
    return TermOut.model_validate(service.set_current_term(db, ctx, term_id))


# --- Classes --------------------------------------------------------------------------------------


@router.get("/class-levels", response_model=list[ClassLevelOut], summary="Classes and arms, in promotion order")
def list_levels(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> list[ClassLevel]:
    return service.list_class_levels(db)


@router.post(
    "/class-levels",
    response_model=ClassLevelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a class (placed after the existing ones)",
)
def create_level(body: ClassLevelIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> ClassLevel:
    return service.create_class_level(db, ctx, name=body.name, arms=body.arms)


@router.get(
    "/class-levels/standard",
    response_model=list[StandardClassOut],
    summary="The usual classes (Creche to SSS 3), marking the ones the school already has",
)
def standard_levels(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> list[service.StandardClass]:
    return service.standard_classes(db)


@router.post(
    "/class-levels/batch",
    response_model=list[ClassLevelOut],
    status_code=status.HTTP_201_CREATED,
    summary="Add several classes at once; standard ones take their usual place in the order",
)
def create_levels(body: ClassLevelsIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> list[ClassLevel]:
    return service.add_class_levels(db, ctx, [(c.name, c.arms) for c in body.classes])


@router.put(
    "/class-levels/order", response_model=list[ClassLevelOut], summary="Set the promotion order (lowest class first)"
)
def reorder_levels(body: ClassOrderIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> list[ClassLevel]:
    return service.reorder_class_levels(db, ctx, body.class_level_ids)


@router.patch("/class-levels/{level_id}", response_model=ClassLevelOut, summary="Rename or retire a class")
def update_level(
    level_id: uuid.UUID, body: ClassLevelUpdateIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> ClassLevel:
    return service.update_class_level(db, ctx, level_id, name=body.name, is_active=body.is_active)


@router.post(
    "/class-levels/{level_id}/arms",
    response_model=ArmOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an arm to a class",
)
def add_arm(level_id: uuid.UUID, body: ArmIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> ArmOut:
    return ArmOut.model_validate(service.add_arm(db, ctx, level_id, body.name))


@router.patch("/arms/{arm_id}", response_model=ArmOut, summary="Rename or retire an arm")
def update_arm(
    arm_id: uuid.UUID, body: ArmUpdateIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> ArmOut:
    return ArmOut.model_validate(service.update_arm(db, ctx, arm_id, name=body.name, is_active=body.is_active))

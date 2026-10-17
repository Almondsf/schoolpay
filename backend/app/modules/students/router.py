import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES, DomainError
from app.core.pagination import Page, PageParams, page_params, paginate
from app.core.phone import mask_phone
from app.core.ratelimit import get_limiter
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability
from app.modules.schools.academics_service import list_class_levels, require_current_term
from app.modules.schools.models import Term

from . import importer, promotion, service
from .models import Gender, Relationship, Student, StudentStatus

router = APIRouter(tags=["students"], responses=ERROR_RESPONSES)
VIEW = Depends(require(Capability.STUDENTS_VIEW))
MANAGE = Depends(require(Capability.STUDENTS_MANAGE))

Admission = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
OptionalName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]
GuardianName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=150)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=25)]


# --- Schemas ---------------------------------------------------------------------------------------


class ClassPlacement(BaseModel):
    arm_id: uuid.UUID
    arm_name: str
    class_level_id: uuid.UUID
    class_level_name: str


class StudentListItem(BaseModel):
    id: uuid.UUID
    admission_number: str
    full_name: str
    first_name: str
    last_name: str
    gender: Gender | None
    status: StudentStatus
    placement: ClassPlacement | None = Field(description="Class in the term being viewed; null if not enrolled")


class GuardianOut(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str
    email: str | None
    relationship: Relationship
    is_primary: bool
    consent_at: str | None


class EnrollmentOut(BaseModel):
    term_id: uuid.UUID
    term_label: str
    session_name: str
    is_current: bool
    placement: ClassPlacement


class StudentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admission_number: str
    first_name: str
    middle_name: str
    last_name: str
    full_name: str
    gender: Gender | None
    date_of_birth: date | None
    status: StudentStatus
    left_on: date | None


class StudentDetailOut(StudentOut):
    guardians: list[GuardianOut]
    enrollments: list[EnrollmentOut]


class GuardianIn(BaseModel):
    full_name: GuardianName
    phone: Phone
    email: EmailStr | None = None
    relationship: Relationship = Relationship.GUARDIAN
    is_primary: bool = False

    def to_input(self) -> service.GuardianInput:
        return service.GuardianInput(
            full_name=self.full_name,
            phone=self.phone,
            email=self.email,
            relationship=self.relationship,
            is_primary=self.is_primary,
        )


class StudentCreateIn(BaseModel):
    admission_number: Admission
    first_name: PersonName
    middle_name: OptionalName = ""
    last_name: PersonName
    gender: Gender | None = None
    date_of_birth: date | None = None
    arm_id: uuid.UUID
    term_id: uuid.UUID | None = Field(default=None, description="Defaults to the current term")
    guardians: list[GuardianIn] = Field(default_factory=list, max_length=4)
    consent_confirmed: bool = Field(default=False, description="The school has the guardians' consent")


class StudentUpdateIn(BaseModel):
    admission_number: Admission | None = None
    first_name: PersonName | None = None
    middle_name: OptionalName | None = None
    last_name: PersonName | None = None
    gender: Gender | None = None
    date_of_birth: date | None = None


class ClassChangeIn(BaseModel):
    arm_id: uuid.UUID
    term_id: uuid.UUID | None = None


class WithdrawIn(BaseModel):
    left_on: date
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


class AddGuardianIn(GuardianIn):
    consent_confirmed: bool = False


class GuardianUpdateIn(BaseModel):
    full_name: GuardianName | None = None
    phone: Phone | None = None
    email: EmailStr | None = None
    clear_email: bool = False


class GuardianListItem(BaseModel):
    id: uuid.UUID
    full_name: str
    phone_masked: str
    students: list[StudentOut]


class ImportErrorOut(BaseModel):
    row: int
    column: str | None
    message: str


class ImportPreviewOut(BaseModel):
    row: int
    admission_number: str
    name: str
    class_name: str
    guardians: int
    action: str


class ImportReportOut(BaseModel):
    dry_run: bool
    total_rows: int
    students_created: int
    students_updated: int
    students_skipped: int
    guardians_created: int
    guardian_links_created: int
    errors: list[ImportErrorOut]
    preview: list[ImportPreviewOut]


class RollForwardIn(BaseModel):
    from_term_id: uuid.UUID
    promote: bool = Field(description="Move everyone up a class (between sessions)")
    dry_run: bool = True


class UnplacedOut(BaseModel):
    student_id: uuid.UUID
    admission_number: str
    name: str
    reason: str


class RollForwardOut(BaseModel):
    dry_run: bool
    moved: int
    promoted: int
    graduated: int
    already_enrolled: int
    unplaced: list[UnplacedOut]


# --- Helpers ---------------------------------------------------------------------------------------


def _placement(
    arm_id: uuid.UUID | None, arm_name: str | None, level_id: uuid.UUID | None, level_name: str | None
) -> ClassPlacement | None:
    if arm_id is None or level_id is None:
        return None
    return ClassPlacement(
        arm_id=arm_id, arm_name=arm_name or "", class_level_id=level_id, class_level_name=level_name or ""
    )


def _detail_out(detail: service.StudentDetail) -> StudentDetailOut:
    base = StudentOut.model_validate(detail.student).model_dump()
    return StudentDetailOut(
        **base,
        guardians=[
            GuardianOut(
                id=g.id,
                full_name=g.full_name,
                phone=g.phone,
                email=g.email,
                relationship=link.relationship_to_student,
                is_primary=link.is_primary,
                consent_at=g.consent_at.isoformat() if g.consent_at else None,
            )
            for g, link in detail.guardians
        ],
        enrollments=[
            EnrollmentOut(
                term_id=e.term_id,
                term_label=f"{('First', 'Second', 'Third')[e.term_number - 1]} term",
                session_name=e.session_name,
                is_current=e.is_current,
                placement=ClassPlacement(
                    arm_id=e.arm_id,
                    arm_name=e.arm_name,
                    class_level_id=e.class_level_id,
                    class_level_name=e.class_level_name,
                ),
            )
            for e in detail.enrollments
        ],
    )


# --- Students --------------------------------------------------------------------------------------


@router.get("/students", response_model=Page[StudentListItem], summary="Students with their class for a term")
def list_students(
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
    params: PageParams = Depends(page_params),
    term_id: uuid.UUID | None = Query(default=None, description="Defaults to the current term"),
    search: str | None = Query(default=None, max_length=100),
    class_level_id: uuid.UUID | None = None,
    arm_id: uuid.UUID | None = None,
    status_filter: StudentStatus | None = Query(default=StudentStatus.ACTIVE, alias="status"),
    unplaced: bool = Query(default=False, description="Only students with no class this term"),
) -> Page[StudentListItem]:
    term = get_or_404(db, Term, term_id, label="Term") if term_id else require_current_term(db)
    rows, total = service.list_students(
        db,
        term.id,
        params.limit,
        params.offset,
        search=search,
        class_level_id=class_level_id,
        arm_id=arm_id,
        status=status_filter,
        unplaced_only=unplaced,
    )
    items = [
        StudentListItem(
            id=r.student.id,
            admission_number=r.student.admission_number,
            full_name=r.student.full_name,
            first_name=r.student.first_name,
            last_name=r.student.last_name,
            gender=r.student.gender,
            status=r.student.status,
            placement=_placement(r.arm_id, r.arm_name, r.class_level_id, r.class_level_name),
        )
        for r in rows
    ]
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.post(
    "/students",
    response_model=StudentDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a student, place them in a class and link guardians",
)
def create_student(
    body: StudentCreateIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> StudentDetailOut:
    data = service.StudentInput(
        admission_number=body.admission_number,
        first_name=body.first_name,
        middle_name=body.middle_name,
        last_name=body.last_name,
        gender=body.gender,
        date_of_birth=body.date_of_birth,
        guardians=[g.to_input() for g in body.guardians],
    )
    student = service.create_student(
        db, ctx, data, arm_id=body.arm_id, term_id=body.term_id, consent_confirmed=body.consent_confirmed
    )
    return _detail_out(service.student_detail(db, student.id))


@router.get(
    "/students/import-template",
    summary="Excel template for importing students",
    response_class=Response,
    responses={200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}},
)
def import_template(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> Response:
    content = importer.template_workbook(list_class_levels(db, include_inactive=False))
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="schoolpay-students-template.xlsx"'},
    )


@router.post(
    "/students/import",
    response_model=ImportReportOut,
    summary="Import students and guardians from a .xlsx or .csv file (dry_run=true to preview)",
)
async def import_students(
    file: UploadFile = File(...),
    dry_run: bool = Form(default=True),
    mode: importer.ImportMode = Form(default=importer.ImportMode.SKIP),
    consent_confirmed: bool = Form(default=False),
    term_id: uuid.UUID | None = Form(default=None),
    ctx: StaffContext = MANAGE,
    db: Session = Depends(get_db),
) -> ImportReportOut:
    get_limiter().hit("students-import:school", "30/hour", str(ctx.school_id))
    content = await file.read()
    if not content:
        raise DomainError("The file is empty.", field="file")
    term = get_or_404(db, Term, term_id, label="Term", field="term_id") if term_id else require_current_term(db)
    report = importer.import_students(
        db,
        ctx,
        filename=file.filename or "upload",
        content=content,
        term=term,
        mode=mode,
        consent_confirmed=consent_confirmed,
        dry_run=dry_run,
    )
    return ImportReportOut(
        dry_run=report.dry_run,
        total_rows=report.total_rows,
        students_created=report.students_created,
        students_updated=report.students_updated,
        students_skipped=report.students_skipped,
        guardians_created=report.guardians_created,
        guardian_links_created=report.guardian_links_created,
        errors=[ImportErrorOut(row=e.row, column=e.column, message=e.message) for e in report.errors],
        preview=[ImportPreviewOut(**p.__dict__) for p in report.preview],
    )


@router.get("/students/{student_id}", response_model=StudentDetailOut, summary="A student with guardians and history")
def get_student(student_id: uuid.UUID, ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> StudentDetailOut:
    return _detail_out(service.student_detail(db, student_id))


@router.patch("/students/{student_id}", response_model=StudentOut, summary="Correct a student's details")
def update_student(
    student_id: uuid.UUID, body: StudentUpdateIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> Student:
    return service.update_student(db, ctx, student_id, body.model_dump(exclude_unset=True))


@router.put("/students/{student_id}/class", response_model=StudentOut, summary="Place a student in an arm for a term")
def change_class(
    student_id: uuid.UUID, body: ClassChangeIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> Student:
    return service.change_class(db, ctx, student_id, body.arm_id, body.term_id)


@router.post("/students/{student_id}/withdraw", response_model=StudentOut, summary="Record that a student has left")
def withdraw_student(
    student_id: uuid.UUID, body: WithdrawIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> Student:
    return service.withdraw(db, ctx, student_id, left_on=body.left_on, reason=body.reason)


@router.post("/students/{student_id}/reinstate", response_model=StudentOut, summary="Bring a student back")
def reinstate_student(student_id: uuid.UUID, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> Student:
    return service.reinstate(db, ctx, student_id)


@router.post(
    "/students/{student_id}/guardians",
    response_model=StudentDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Link a guardian (an existing guardian with the same phone is reused)",
)
def add_guardian(
    student_id: uuid.UUID, body: AddGuardianIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> StudentDetailOut:
    service.add_guardian(db, ctx, student_id, body.to_input(), body.consent_confirmed)
    return _detail_out(service.student_detail(db, student_id))


@router.delete(
    "/students/{student_id}/guardians/{guardian_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink a guardian from a student",
)
def remove_guardian(
    student_id: uuid.UUID, guardian_id: uuid.UUID, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> None:
    service.remove_guardian(db, ctx, student_id, guardian_id)


@router.post(
    "/students/{student_id}/guardians/{guardian_id}/primary",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Make this the guardian who receives fee notices",
)
def make_primary(
    student_id: uuid.UUID, guardian_id: uuid.UUID, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> None:
    service.set_primary_guardian(db, ctx, student_id, guardian_id)


# --- Guardians ------------------------------------------------------------------------------------


@router.get(
    "/guardians", response_model=Page[GuardianListItem], summary="Guardians (search by name, or by exact phone number)"
)
def list_guardians(
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
    params: PageParams = Depends(page_params),
    search: str | None = Query(default=None, max_length=100),
) -> Page[GuardianListItem]:
    guardians, total = paginate(db, service.guardians_query(search), params)  # scalars: the Guardian column
    children = service.guardian_children(db, [g.id for g in guardians])
    items = [
        GuardianListItem(
            id=g.id,
            full_name=g.full_name,
            phone_masked=mask_phone(g.phone),
            students=[StudentOut.model_validate(s) for s in children[g.id]],
        )
        for g in guardians
    ]
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.patch(
    "/guardians/{guardian_id}", response_model=GuardianListItem, summary="Update a guardian's name or contact details"
)
def update_guardian(
    guardian_id: uuid.UUID, body: GuardianUpdateIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> GuardianListItem:
    guardian = service.update_guardian(
        db, ctx, guardian_id, full_name=body.full_name, phone=body.phone, email=body.email, clear_email=body.clear_email
    )
    children = service.guardian_children(db, [guardian.id])
    return GuardianListItem(
        id=guardian.id,
        full_name=guardian.full_name,
        phone_masked=mask_phone(guardian.phone),
        students=[StudentOut.model_validate(s) for s in children[guardian.id]],
    )


# --- Moving to the next term ----------------------------------------------------------------------


@router.post(
    "/terms/{term_id}/roll-forward",
    response_model=RollForwardOut,
    summary="Enrol last term's students into this term (promote=true moves them up a class)",
)
def roll_forward(
    term_id: uuid.UUID, body: RollForwardIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> RollForwardOut:
    report = promotion.roll_forward(
        db, ctx, from_term_id=body.from_term_id, to_term_id=term_id, promote=body.promote, dry_run=body.dry_run
    )
    return RollForwardOut(
        dry_run=report.dry_run,
        moved=report.moved,
        promoted=report.promoted,
        graduated=report.graduated,
        already_enrolled=report.already_enrolled,
        unplaced=[UnplacedOut(**u.__dict__) for u in report.unplaced],
    )

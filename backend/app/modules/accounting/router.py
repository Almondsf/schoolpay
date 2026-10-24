import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from app.core.clock import lagos_today
from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES
from app.core.ratelimit import get_limiter
from app.modules.accounts.deps import StaffContext, require
from app.modules.accounts.permissions import Capability
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.reports.exports import XLSX
from app.modules.schools.models import School

from . import exports, journal, periods, session_pack
from .models import BookAction

router = APIRouter(prefix="/accounting", tags=["accounting"], responses=ERROR_RESPONSES)
VIEW = Depends(require(Capability.REPORTS_VIEW))
MANAGE = Depends(require(Capability.PAYMENTS_APPROVE))  # as for spending categories
EXPORT_RATE = "60/hour"


class JournalAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    code: str
    name: str
    type: str
    default_code: str
    default_name: str


class JournalAccountIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = Field(
        default="", description="Blank: WardPay's default"
    )
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = Field(
        default="", description="Blank: WardPay's default"
    )


class JournalTotalOut(BaseModel):
    account: JournalAccountOut
    debit_kobo: int
    credit_kobo: int


class JournalSummaryOut(BaseModel):
    start: date
    end: date
    entries: int
    debit_kobo: int
    credit_kobo: int
    totals: list[JournalTotalOut] = Field(description="Movement on each account in the period")
    unchecked_deposits: int = Field(description="Banking not yet checked by a second person, left out")


def period(
    start: date | None = Query(default=None, description="Lagos date; default: the first of this month"),
    end: date | None = Query(default=None, description="Lagos date, inclusive; default: today"),
) -> tuple[date, date]:
    end = end or lagos_today()
    return start or end.replace(day=1), end


@router.get("/accounts", response_model=list[JournalAccountOut], summary="The chart of accounts used in the journal")
def list_accounts(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> list[JournalAccountOut]:
    return [JournalAccountOut.model_validate(a) for a in journal.chart(db)]


@router.put(
    "/accounts/{key}", response_model=JournalAccountOut, summary="Use the school's own code and name for an account"
)
def change_account(
    key: str, body: JournalAccountIn, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)
) -> JournalAccountOut:
    return JournalAccountOut.model_validate(journal.rename(db, ctx, key, code=body.code, name=body.name))


@router.get("/journal", response_model=JournalSummaryOut, summary="The journal's totals for a period")
def journal_summary(
    dates: tuple[date, date] = Depends(period), ctx: StaffContext = VIEW, db: Session = Depends(get_db)
) -> JournalSummaryOut:
    j = journal.journal(db, *dates)
    return JournalSummaryOut(
        start=j.start,
        end=j.end,
        entries=len(j.entries),
        debit_kobo=j.debit_kobo,
        credit_kobo=j.credit_kobo,
        totals=[JournalTotalOut.model_validate(t, from_attributes=True) for t in j.totals],
        unchecked_deposits=j.unchecked_deposits,
    )


@router.get(
    "/journal/export",
    summary="Download the general journal for the accountant (Excel or CSV)",
    response_class=Response,
    responses={200: {"content": {XLSX: {}, exports.CSV: {}}}},
)
def export_journal(
    format: Literal["xlsx", "csv"] = "xlsx",
    dates: tuple[date, date] = Depends(period),
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
) -> Response:
    get_limiter().hit("reports-export:school", EXPORT_RATE, str(ctx.school_id))
    j = journal.journal(db, *dates)
    if format == "xlsx":
        school = db.get(School, ctx.school_id)
        content, media = exports.journal_xlsx(school.name if school else "", j), XLSX
    else:
        content, media = exports.journal_csv(j), exports.CSV
    record(
        db,
        AuditAction.REPORT_EXPORTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        changes={"report": "journal", "format": format, "entries": len(j.entries), "from": j.start, "to": j.end},
    )
    db.commit()
    name = f"journal-{j.start.isoformat()}-to-{j.end.isoformat()}.{format}"
    return Response(
        content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


@router.get(
    "/session-pack",
    summary="Download the end-of-session pack (Excel): summary, terms, money in and out, debtors, cash book, journal",
    response_class=Response,
    responses={200: {"content": {XLSX: {}}}},
)
def export_session_pack(
    session_id: uuid.UUID | None = Query(default=None, description="Default: the school year running today"),
    ctx: StaffContext = VIEW,
    db: Session = Depends(get_db),
) -> Response:
    get_limiter().hit("reports-export:school", EXPORT_RATE, str(ctx.school_id))
    pack = session_pack.session_pack(db, session_id)
    school = db.get(School, ctx.school_id)
    content = exports.session_pack_xlsx(school.name if school else "", pack)
    # It carries parents' names and phone numbers (the debtors sheet): who downloaded it is recorded
    record(
        db,
        AuditAction.REPORT_EXPORTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        changes={"report": "session_pack", "session": pack.name, "entries": len(pack.journal.entries)},
    )
    db.commit()
    name = f"end-of-session-{pack.name.replace('/', '-')}.xlsx"
    return Response(
        content,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


# --- Closing the books -------------------------------------------------------------------------------


class BookClosingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: BookAction
    term_id: uuid.UUID
    closed_through_before: date | None
    closed_through_after: date | None
    reason: str
    figures: dict[str, Any] = Field(description="The term's figures when it was closed")
    by: uuid.UUID
    created_at: datetime


class BooksOut(BaseModel):
    closed_through: date | None = Field(description="Nothing dated on or before this day can be recorded")
    history: list[BookClosingOut]


class ReopenIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


@router.get("/books", response_model=BooksOut, summary="How far the books are closed, and every close and re-open")
def books(ctx: StaffContext = VIEW, db: Session = Depends(get_db)) -> BooksOut:
    school = db.get(School, ctx.school_id)
    return BooksOut(
        closed_through=school.books_closed_through if school else None,
        history=[BookClosingOut.model_validate(h) for h in periods.history(db)],
    )


@router.post(
    "/terms/{term_id}/close",
    response_model=BookClosingOut,
    summary="Close a term that has ended: nothing dated in it can be recorded afterwards",
)
def close_term(term_id: uuid.UUID, ctx: StaffContext = MANAGE, db: Session = Depends(get_db)) -> BookClosingOut:
    return BookClosingOut.model_validate(periods.close_term(db, ctx, term_id))


@router.post(
    "/books/reopen",
    response_model=BookClosingOut,
    summary="Re-open the last closed term (proprietor only, with a reason)",
)
def reopen_books(
    body: ReopenIn, ctx: StaffContext = Depends(require(Capability.BOOKS_REOPEN)), db: Session = Depends(get_db)
) -> BookClosingOut:
    return BookClosingOut.model_validate(periods.reopen(db, ctx, body.reason))

"""
Big downloads built in the background: the general journal and the end-of-session pack.

A whole session's journal for a large school is tens of thousands of lines, and writing Excel takes about
a second per thousand; the web app's proxy gives up on a request after about 30 seconds. So the browser
asks for the file (`request`), the worker builds it (`build`, an outbox task) and stores it, and the
browser checks back and downloads it once it's ready. The file is kept for a day (the daily retention job
deletes it) and only the person who asked can download it; the download is what's audited, as for every
other report.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, undefer

from app.core import outbox
from app.core.errors import DomainError, NotFound
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.reports.exports import XLSX
from app.modules.schools.models import School

from . import exports, journal, session_pack
from .models import ExportFile, ExportKind, ExportStatus

logger = logging.getLogger(__name__)

BUILD_TASK = "accounting.build_export"
KEEP_FOR = timedelta(days=1)


@dataclass(frozen=True)
class Built:
    filename: str
    content_type: str
    content: bytes
    rows: int


def _params(
    kind: ExportKind, *, start: date | None, end: date | None, fmt: str, session_id: uuid.UUID | None
) -> dict[str, Any]:
    if kind == ExportKind.JOURNAL:
        if start is None or end is None:
            raise DomainError("Choose the dates.", field="start")
        if fmt not in ("xlsx", "csv"):
            raise DomainError("Choose Excel or CSV.", field="format")
        journal.check_period(start, end)
        return {"start": start.isoformat(), "end": end.isoformat(), "format": fmt}
    return {"session_id": str(session_id) if session_id else None}


def request(
    session: Session,
    ctx: StaffContext,
    kind: ExportKind,
    *,
    start: date | None = None,
    end: date | None = None,
    fmt: str = "xlsx",
    session_id: uuid.UUID | None = None,
) -> ExportFile:
    """Queue a file to be built. The checks that can fail fast happen here, not in the worker."""
    params = _params(kind, start=start, end=end, fmt=fmt, session_id=session_id)
    if kind == ExportKind.SESSION_PACK:
        session_pack.find_session(session, session_id)  # a wrong year is a 404 now, not a failed file later
    export = ExportFile(
        id=uuid.uuid4(),
        school_id=ctx.school_id,
        kind=kind,
        params=params,
        requested_by=ctx.user.id,
        expires_at=datetime.now(UTC) + KEEP_FOR,
    )
    session.add(export)
    outbox.enqueue(session, BUILD_TASK, {"school_id": str(ctx.school_id), "export_id": str(export.id)})
    session.commit()
    return export


def _build(session: Session, export: ExportFile) -> Built:
    school = session.get(School, export.school_id)
    name = school.name if school else ""
    p = export.params
    if export.kind == ExportKind.JOURNAL:
        j = journal.journal(session, date.fromisoformat(p["start"]), date.fromisoformat(p["end"]))
        fmt = p.get("format", "xlsx")
        content = exports.journal_xlsx(name, j) if fmt == "xlsx" else exports.journal_csv(j)
        return Built(
            f"journal-{j.start.isoformat()}-to-{j.end.isoformat()}.{fmt}",
            XLSX if fmt == "xlsx" else exports.CSV,
            content,
            len(j.entries),
        )
    pack = session_pack.session_pack(session, uuid.UUID(p["session_id"]) if p.get("session_id") else None)
    return Built(
        f"end-of-session-{pack.name.replace('/', '-')}.xlsx",
        XLSX,
        exports.session_pack_xlsx(name, pack),
        len(pack.journal.entries),
    )


def build(session: Session, export_id: uuid.UUID) -> ExportStatus:
    """The background task: build the file and store it. Safe to run twice (a finished one is left alone)."""
    export = session.scalar(select(ExportFile).where(ExportFile.id == export_id).with_for_update())
    if export is None or export.status != ExportStatus.PENDING:
        return export.status if export else ExportStatus.FAILED
    try:
        built = _build(session, export)
    except Exception as e:
        session.rollback()
        if isinstance(e, DomainError):
            message = e.message
        else:
            logger.exception("Building export %s failed", export_id)
            message = "Couldn't build the file. Please try again."
        session.execute(
            update(ExportFile)
            .where(ExportFile.id == export_id)
            .values(status=ExportStatus.FAILED, error=message[:300], finished_at=datetime.now(UTC))
        )
        session.commit()
        return ExportStatus.FAILED
    export.status = ExportStatus.READY
    export.filename, export.content_type = built.filename, built.content_type
    export.content, export.size_bytes = built.content, len(built.content)
    export.params = {**export.params, "rows": built.rows}
    export.finished_at = datetime.now(UTC)
    session.commit()
    return ExportStatus.READY


def get(session: Session, ctx: StaffContext, export_id: uuid.UUID) -> ExportFile:
    """The requester's own export (anyone else gets a 404). Without the file itself."""
    export = session.scalar(
        select(ExportFile).where(ExportFile.id == export_id, ExportFile.requested_by == ctx.user.id)
    )
    if export is None or export.expires_at < datetime.now(UTC):
        raise NotFound("That download has expired or doesn't exist. Ask for it again.")
    return export


def download(session: Session, ctx: StaffContext, export_id: uuid.UUID) -> ExportFile:
    export = session.scalar(
        select(ExportFile)
        .options(undefer(ExportFile.content))
        .where(ExportFile.id == export_id, ExportFile.requested_by == ctx.user.id)
    )
    if export is None or export.expires_at < datetime.now(UTC):
        raise NotFound("That download has expired or doesn't exist. Ask for it again.")
    if export.status != ExportStatus.READY:
        raise DomainError("It isn't ready yet.", code="export_not_ready")
    record(
        session,
        AuditAction.REPORT_EXPORTED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=export,
        changes={"report": export.kind.value, **{k: v for k, v in export.params.items() if v is not None}},
    )
    session.commit()
    return export


def prune(session: Session, now: datetime | None = None) -> int:
    """Delete expired downloads (platform-wide session). One statement."""
    result = session.execute(delete(ExportFile).where(ExportFile.expires_at < (now or datetime.now(UTC))))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]

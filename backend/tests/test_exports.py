"""Big downloads built in the background: asked for, built by the worker, downloaded by the person who asked."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.core.clock import lagos_today
from app.core.tenancy import scope_platform_wide
from app.modules.accounting.models import ExportFile
from app.modules.audit.models import AuditLog
from app.modules.privacy.service import apply_retention
from tests.test_payments import generate
from tests.test_payments import world as world  # the fixture

EXPORTS = "/api/v1/accounting/exports"


async def export(client, headers, **body):
    """Ask for a file and download it (tasks run at once in tests). Returns (status, download response)."""
    asked = await client.post(EXPORTS, headers=headers, json=body)
    assert asked.status_code == 202, asked.text
    status = (await client.get(f"{EXPORTS}/{asked.json()['id']}", headers=headers)).json()
    if status["status"] != "ready":
        return status, None
    downloaded = await client.get(f"{EXPORTS}/{asked.json()['id']}/download", headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["cache-control"] == "no-store"
    return status, downloaded


async def test_a_file_is_built_then_downloaded_by_whoever_asked(client, world, db):
    await generate(client, world)
    today = lagos_today().isoformat()
    status, downloaded = await export(client, world["proprietor"], kind="journal", start=today, end=today, format="csv")
    assert status["status"] == "ready" and status["filename"].endswith(".csv") and status["size_bytes"] > 0
    assert downloaded.content.decode("utf-8-sig").startswith("Date,Entry")
    # Someone else in the same school can't fetch it: it may carry parents' phone numbers
    assert (await client.get(f"{EXPORTS}/{status['id']}/download", headers=world["bursar"])).status_code == 404
    scope_platform_wide(db)
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "reports.exported", AuditLog.target_id == status["id"])
        )
        == 1
    )


async def test_bad_requests_fail_at_once_and_old_files_go(client, world, db):
    too_long = await client.post(
        EXPORTS, headers=world["bursar"], json={"kind": "journal", "start": "2025-01-01", "end": "2026-10-24"}
    )
    assert too_long.status_code == 400
    no_year = await client.post(
        EXPORTS,
        headers=world["bursar"],
        json={"kind": "session_pack", "session_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert no_year.status_code == 404
    status, _ = await export(client, world["bursar"], kind="session_pack")
    assert status["status"] == "ready" and status["filename"].startswith("end-of-session-")

    scope_platform_wide(db)
    db.execute(text("UPDATE export_files SET expires_at = :t"), {"t": datetime.now(UTC) - timedelta(minutes=1)})
    db.commit()
    assert (await client.get(f"{EXPORTS}/{status['id']}", headers=world["bursar"])).status_code == 404
    assert apply_retention()["downloads_deleted"] == 1
    scope_platform_wide(db)
    assert db.scalar(select(func.count()).select_from(ExportFile)) == 0

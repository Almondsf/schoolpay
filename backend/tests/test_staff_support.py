"""The platform admin helping a school's locked-out staff: password links and two-factor resets."""

from sqlalchemy import select

from app.core.outbox import OutboxTask
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from app.modules.audit.models import AuditAction, AuditLog, PlatformAuditLog
from tests.factories import headers_for, make_user
from tests.test_platform_billing import world  # noqa: F401 (a fixture)

BASE = "/api/v1/platform/schools"


def links_queued(db, user):
    scope_platform_wide(db)
    return [
        t.payload["purpose"]
        for t in db.scalars(select(OutboxTask).where(OutboxTask.task_name == "accounts.send_password_link"))
        if t.payload["user_id"] == str(user.id)
    ]


async def test_sends_the_invite_again_or_a_reset_link(client, world, db):  # noqa: F811
    invited = make_user(db, world["alpha"], Role.BURSAR, password=None)
    proprietor = world["alpha_proprietor"]
    db.commit()
    first = await client.post(f"{BASE}/{world['alpha'].id}/staff/{invited.id}/password-link", headers=world["admin"])
    assert first.json() == {"sent": "invite"}
    second = await client.post(
        f"{BASE}/{world['alpha'].id}/staff/{proprietor.id}/password-link", headers=world["admin"]
    )
    assert second.json() == {"sent": "reset"}
    assert links_queued(db, invited) == ["invite"] and links_queued(db, proprietor) == ["reset"]
    # Recorded for the school and for the platform
    scope_platform_wide(db)
    assert db.scalar(
        select(AuditLog.id).where(
            AuditLog.school_id == world["alpha"].id, AuditLog.action == AuditAction.PLATFORM_STAFF_LINK_SENT.value
        )
    )
    assert db.scalar(
        select(PlatformAuditLog.id).where(PlatformAuditLog.action == AuditAction.PLATFORM_STAFF_LINK_SENT.value)
    )


async def test_resets_two_factor_and_signs_the_person_out(client, world, db):  # noqa: F811
    proprietor = world["alpha_proprietor"]
    assert proprietor.mfa_enabled  # proprietors get it by default in tests
    signed_in = headers_for(db, proprietor)
    assert (await client.get("/api/v1/auth/me", headers=signed_in)).status_code == 200
    reset = await client.post(f"{BASE}/{world['alpha'].id}/staff/{proprietor.id}/reset-mfa", headers=world["admin"])
    assert reset.status_code == 204
    db.refresh(proprietor)
    assert not proprietor.mfa_enabled
    assert (await client.get("/api/v1/auth/me", headers=signed_in)).status_code == 401  # signed out everywhere
    again = await client.post(f"{BASE}/{world['alpha'].id}/staff/{proprietor.id}/reset-mfa", headers=world["admin"])
    assert again.status_code == 409


async def test_only_that_schools_active_staff_and_only_platform_admins(client, world, db):  # noqa: F811
    proprietor = world["alpha_proprietor"]
    gone = make_user(db, world["alpha"], Role.BURSAR, active=False)
    db.commit()
    # The wrong school in the URL: not found
    wrong = await client.post(f"{BASE}/{world['beta'].id}/staff/{proprietor.id}/password-link", headers=world["admin"])
    assert wrong.status_code == 404
    deactivated = await client.post(f"{BASE}/{world['alpha'].id}/staff/{gone.id}/password-link", headers=world["admin"])
    assert deactivated.status_code == 409
    # A school's own staff can't use the platform's tools
    as_bursar = await client.post(
        f"{BASE}/{world['alpha'].id}/staff/{proprietor.id}/reset-mfa", headers=world["alpha_bursar"]
    )
    assert as_bursar.status_code == 403
    detail = (await client.get(f"{BASE}/{world['alpha'].id}", headers=world["admin"])).json()
    assert str(proprietor.id) in {s["id"] for s in detail["staff"]}

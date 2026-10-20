"""Staff management, school profile, audit trail and platform admin: permissions and school isolation."""

import re
import uuid

import pytest

from app.core import email
from app.modules.accounts.models import Role
from tests.factories import PASSWORD, headers_for, make_school, make_user


@pytest.fixture
def two_schools(db):
    """School A with every role, and school B with its own proprietor and staff."""
    a, b = make_school(db, "Alpha College"), make_school(db, "Beta Academy")
    people = {
        "a_proprietor": make_user(db, a, Role.PROPRIETOR),
        "a_bursar": make_user(db, a, Role.BURSAR),
        "a_admin": make_user(db, a, Role.SCHOOL_ADMIN),
        "b_proprietor": make_user(db, b, Role.PROPRIETOR),
        "b_admin": make_user(db, b, Role.SCHOOL_ADMIN),
    }
    db.commit()
    return a, b, people


# --- Permissions ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "list_status", "invite_status"),
    [
        ("a_proprietor", 200, 201),
        ("a_bursar", 200, 403),
        ("a_admin", 200, 403),
    ],
)
async def test_staff_permissions_by_role(client, db, two_schools, role, list_status, invite_status):
    _, _, people = two_schools
    headers = headers_for(db, people[role])
    assert (await client.get("/api/v1/staff", headers=headers)).status_code == list_status
    response = await client.post(
        "/api/v1/staff",
        headers=headers,
        json={"email": f"new-{uuid.uuid4().hex[:6]}@example.com", "full_name": "New Person", "role": "bursar"},
    )
    assert response.status_code == invite_status


async def test_platform_admin_has_no_school_access(client, db):
    admin = make_user(db, None, Role.PLATFORM_ADMIN)
    headers = headers_for(db, admin)
    assert (await client.get("/api/v1/staff", headers=headers)).status_code == 403
    assert (await client.get("/api/v1/school", headers=headers)).status_code == 403


async def test_school_staff_have_no_platform_access(client, db, two_schools):
    _, _, people = two_schools
    headers = headers_for(db, people["a_proprietor"])
    assert (await client.get("/api/v1/platform/schools", headers=headers)).status_code == 403
    assert (await client.get("/api/v1/platform/audit", headers=headers)).status_code == 403


# --- Inviting and managing staff -----------------------------------------------------------------


async def test_invite_sends_link_and_invitee_can_set_password_and_sign_in(client, db, two_schools):
    _, _, people = two_schools
    headers = headers_for(db, people["a_proprietor"])
    response = await client.post(
        "/api/v1/staff",
        headers=headers,
        json={"email": "New.Admin@Example.com", "full_name": "Ada Obi", "role": "school_admin"},
    )
    assert response.status_code == 201
    staff = response.json()
    assert staff["email"] == "new.admin@example.com" and staff["invite_pending"] is True
    message = email.sent_messages[-1]
    assert message.to == "new.admin@example.com" and "invited" in message.subject
    token = re.search(r"#token=(\S+)", message.body).group(1)
    assert (
        await client.post("/api/v1/auth/password/set", json={"token": token, "new_password": "ada-strong-passphrase"})
    ).status_code == 204
    login = await client.post(
        "/api/v1/auth/login", json={"email": "new.admin@example.com", "password": "ada-strong-passphrase"}
    )
    assert login.status_code == 200


async def test_invite_rejects_existing_email_and_non_staff_roles(client, db, two_schools):
    _, _, people = two_schools
    headers = headers_for(db, people["a_proprietor"])
    taken = await client.post(
        "/api/v1/staff",
        headers=headers,
        json={"email": people["b_admin"].email, "full_name": "Copy Cat", "role": "bursar"},
    )
    assert taken.status_code == 409
    for role in ("platform_admin", "parent"):
        bad = await client.post(
            "/api/v1/staff",
            headers=headers,
            json={"email": f"{role}@example.com", "full_name": "Escalation Try", "role": role},
        )
        assert bad.status_code == 422


async def test_role_change_signs_the_person_out(client, db, two_schools):
    _, _, people = two_schools
    target_headers = headers_for(db, people["a_admin"])
    headers = headers_for(db, people["a_proprietor"])
    response = await client.patch(f"/api/v1/staff/{people['a_admin'].id}", headers=headers, json={"role": "bursar"})
    assert response.status_code == 200 and response.json()["role"] == "bursar"
    assert (await client.get("/api/v1/auth/me", headers=target_headers)).status_code == 401


async def test_cannot_change_own_role_or_deactivate_self(client, db, two_schools):
    _, _, people = two_schools
    me = people["a_proprietor"]
    headers = headers_for(db, me)
    assert (await client.patch(f"/api/v1/staff/{me.id}", headers=headers, json={"role": "bursar"})).status_code == 409
    assert (await client.post(f"/api/v1/staff/{me.id}/deactivate", headers=headers)).status_code == 409


async def test_last_active_proprietor_is_protected(db, two_schools):
    """Via the API this is backed up by the self-action rules; the service guard holds on its own too."""
    from app.core.errors import Conflict
    from app.core.http import RequestMeta
    from app.core.tenancy import scope_to_school
    from app.modules.accounts import staff_service
    from app.modules.accounts.deps import StaffContext

    a, _, people = two_schools
    sole, admin = people["a_proprietor"], people["a_admin"]
    scope_to_school(db, a.id)
    ctx = StaffContext(user=admin, session_id=uuid.uuid4(), meta=RequestMeta(), school_id=a.id)
    with pytest.raises(Conflict, match="at least one active proprietor"):
        staff_service.deactivate(db, ctx, sole.id)
    with pytest.raises(Conflict, match="at least one active proprietor"):
        staff_service.update(db, ctx, sole.id, full_name=None, role=Role.BURSAR)


async def test_one_proprietor_can_remove_another(client, db, two_schools):
    a, _, people = two_schools
    second = make_user(db, a, Role.PROPRIETOR)
    headers = headers_for(db, second)
    response = await client.post(f"/api/v1/staff/{people['a_proprietor'].id}/deactivate", headers=headers)
    assert response.status_code == 200 and response.json()["is_active"] is False


async def test_deactivation_ends_sessions_and_blocks_sign_in(client, db, two_schools):
    _, _, people = two_schools
    target = people["a_bursar"]
    target_headers = headers_for(db, target)
    headers = headers_for(db, people["a_proprietor"])
    assert (await client.post(f"/api/v1/staff/{target.id}/deactivate", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=target_headers)).status_code == 401
    login = await client.post("/api/v1/auth/login", json={"email": target.email, "password": PASSWORD})
    assert login.status_code == 401
    assert (await client.post(f"/api/v1/staff/{target.id}/reactivate", headers=headers)).json()["is_active"] is True


async def test_reset_mfa_for_lost_phone(client, db, two_schools):
    _, _, people = two_schools
    headers = headers_for(db, people["a_proprietor"])
    response = await client.post(f"/api/v1/staff/{people['a_bursar'].id}/reset-mfa", headers=headers)
    assert response.status_code == 200 and response.json()["mfa_enabled"] is False


# --- School isolation ----------------------------------------------------------------------------


async def test_staff_list_shows_only_own_school(client, db, two_schools):
    _, _, people = two_schools
    response = await client.get("/api/v1/staff", headers=headers_for(db, people["a_proprietor"]))
    emails = {s["email"] for s in response.json()["items"]}
    assert people["a_bursar"].email in emails
    assert people["b_proprietor"].email not in emails and people["b_admin"].email not in emails


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("patch", "", {"full_name": "Hijacked Name"}),
        ("post", "/deactivate", None),
        ("post", "/reactivate", None),
        ("post", "/reset-mfa", None),
        ("post", "/resend-invite", None),
    ],
)
async def test_cannot_touch_another_schools_staff_by_id(client, db, two_schools, method, path, body):
    _, _, people = two_schools
    victim = people["b_admin"]
    headers = headers_for(db, people["a_proprietor"])
    response = await client.request(method, f"/api/v1/staff/{victim.id}{path}", headers=headers, json=body)
    assert response.status_code == 404
    db.refresh(victim)
    assert victim.full_name != "Hijacked Name" and victim.is_active


async def test_school_profile_is_own_school_only(client, db, two_schools):
    a, b, people = two_schools
    headers = headers_for(db, people["a_admin"])
    assert (await client.get("/api/v1/school", headers=headers)).json()["id"] == str(a.id)
    updated = await client.patch("/api/v1/school", headers=headers, json={"phone": "+234 801 234 5678"})
    assert updated.status_code == 200
    db.refresh(b)
    assert b.phone == ""
    bursar = await client.patch("/api/v1/school", headers=headers_for(db, people["a_bursar"]), json={"phone": "1"})
    assert bursar.status_code == 403


async def test_audit_trail_is_per_school_and_permissioned(client, db, two_schools):
    _, _, people = two_schools
    a_headers = headers_for(db, people["a_proprietor"])
    b_headers = headers_for(db, people["b_proprietor"])
    await client.patch(f"/api/v1/staff/{people['a_admin'].id}", headers=a_headers, json={"full_name": "Renamed A"})
    await client.patch(f"/api/v1/staff/{people['b_admin'].id}", headers=b_headers, json={"full_name": "Renamed B"})

    a_entries = (await client.get("/api/v1/audit", headers=a_headers)).json()["items"]
    targets = {e["target_id"] for e in a_entries}
    assert str(people["a_admin"].id) in targets and str(people["b_admin"].id) not in targets
    update = next(e for e in a_entries if e["action"] == "staff.updated")
    assert update["changes"] == {"full_name": {"from": people["a_admin"].full_name, "to": "Renamed A"}}

    filtered = await client.get(f"/api/v1/audit?target_id={people['b_admin'].id}", headers=a_headers)
    assert filtered.json()["items"] == []
    assert (await client.get("/api/v1/audit", headers=headers_for(db, people["a_bursar"]))).status_code == 200
    assert (await client.get("/api/v1/audit", headers=headers_for(db, people["a_admin"]))).status_code == 200
    assert (await client.get("/api/v1/audit")).status_code == 401


async def test_audit_rows_cannot_be_changed_in_the_database(db, two_schools):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    a, _, _people = two_schools
    from app.core.tenancy import scope_to_school
    from app.modules.audit.models import AuditAction
    from app.modules.audit.service import Actor, record

    scope_to_school(db, a.id)
    record(db, AuditAction.STAFF_UPDATED, school_id=a.id, actor=Actor.system("test"))
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE audit_log SET action = 'x'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM audit_log"))
    db.rollback()


# --- Platform admin ------------------------------------------------------------------------------


async def test_platform_admin_creates_school_with_proprietor_invite(client, db):
    admin = make_user(db, None, Role.PLATFORM_ADMIN)
    headers = headers_for(db, admin)
    response = await client.post(
        "/api/v1/platform/schools",
        headers=headers,
        json={
            "name": "Gamma Schools",
            "slug": "gamma-schools",
            "proprietor_email": "owner@gamma.ng",
            "proprietor_name": "Chinedu Okafor",
        },
    )
    assert response.status_code == 201
    assert email.sent_messages[-1].to == "owner@gamma.ng"
    duplicate = await client.post(
        "/api/v1/platform/schools",
        headers=headers,
        json={
            "name": "Gamma Again",
            "slug": "gamma-schools",
            "proprietor_email": "x@gamma.ng",
            "proprietor_name": "X Y",
        },
    )
    assert duplicate.status_code == 409
    bad_slug = await client.post(
        "/api/v1/platform/schools",
        headers=headers,
        json={"name": "Bad", "slug": "Bad Slug!", "proprietor_email": "y@gamma.ng", "proprietor_name": "Y Z"},
    )
    assert bad_slug.status_code == 400
    listing = await client.get("/api/v1/platform/schools?search=gamma", headers=headers)
    assert [s["slug"] for s in listing.json()["items"]] == ["gamma-schools"]


async def test_suspending_a_school_signs_its_staff_out(client, db, two_schools):
    a, _, people = two_schools
    staff_headers = headers_for(db, people["a_admin"])
    admin = make_user(db, None, Role.PLATFORM_ADMIN)
    headers = headers_for(db, admin)
    response = await client.post(
        f"/api/v1/platform/schools/{a.id}/suspend", headers=headers, json={"reason": "Unpaid subscription"}
    )
    assert response.status_code == 200 and response.json()["status"] == "suspended"
    assert (await client.get("/api/v1/auth/me", headers=staff_headers)).status_code == 401
    events = (await client.get("/api/v1/platform/audit?action=school.suspended", headers=headers)).json()["items"]
    assert events and events[0]["changes"] == {"reason": "Unpaid subscription"}

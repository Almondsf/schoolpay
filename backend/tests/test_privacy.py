"""NDPA tooling: a parent's copy of their data, erasure, and retention."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.accounts.models import Role
from app.modules.notifications import sms
from app.modules.notifications.models import MessageLog
from app.modules.privacy import service as privacy
from app.modules.students.models import Guardian, Student, StudentStatus
from tests.factories import QueryCounter, headers_for, make_calendar, make_levels, make_school, make_student, make_user
from tests.test_fees import add_fees, generate
from tests.test_parents import PHONE, bearer, link_guardian, sign_in
from tests.test_payments import connect_gateway, pay_cash

NAIRA = 100


@pytest.fixture
async def world(db, client):
    school = make_school(db, "Alpha College")
    proprietor, bursar = make_user(db, school, Role.PROPRIETOR), make_user(db, school, Role.BURSAR)
    admin = make_user(db, school, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(school)
    levels = make_levels(school)
    add_fees(school, terms[0], levels)
    child = make_student(school, terms[0], levels[0].arms[0], first="Tolu", last="Adeyemi")
    guardian = link_guardian(school, [child])
    connect_gateway(school)
    db.commit()
    w = {
        "school": school,
        "terms": terms,
        "levels": levels,
        "child": child,
        "student": child,
        "guardian": guardian,
        "proprietor": headers_for(db, proprietor),
        "bursar": headers_for(db, bursar),
        "admin": headers_for(db, admin),
    }
    await generate(client, w)
    await pay_cash(client, w, 50_000)
    return w


async def withdraw(client, world, student):
    response = await client.post(
        f"/api/v1/students/{student.id}/withdraw",
        headers=world["admin"],
        json={"left_on": "2026-10-10", "reason": "Moved to Abuja"},
    )
    assert response.status_code == 200, response.text


# --- Export -------------------------------------------------------------------------------------------


async def test_parent_downloads_everything_about_them(client, world, db):
    parent = bearer(await sign_in(client))
    response = await client.get("/api/v1/parent/my-data", headers=parent)
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="my-data.json"'
    data = json.loads(response.content)
    assert data["you"]["phone"] == PHONE and data["you"]["name"] == "Mrs Adeyemi"
    [child] = data["children"]
    assert child["name"] == "Tolu Adeyemi" and child["classes"][0]["class"] == "JSS 1 A"
    assert {ln["description"] for ln in data["bills"][0]["lines"]} == {"Tuition", "PTA"}
    assert data["payments"][0]["amount"] == {"kobo": 5_000_000, "naira": "50000.00"}
    assert data["payments"][0]["receipt_number"] == 1
    assert data["messages"] and "received" in data["messages"][0]["text"]

    scope_to_school(db, world["school"].id)
    assert db.execute(text("SELECT count(*) FROM audit_log WHERE action = 'privacy.exported'")).scalar() == 1


async def test_staff_export_for_a_parent_and_isolation(client, world, db):
    url = f"/api/v1/guardians/{world['guardian'].id}/export"
    assert (await client.get(url, headers=world["admin"])).status_code == 200
    assert (await client.get(url, headers=world["bursar"])).status_code == 403  # bursars don't manage records

    other = make_school(db, "Beta Academy")
    theirs = headers_for(db, make_user(db, other, Role.SCHOOL_ADMIN))
    db.commit()
    assert (await client.get(url, headers=theirs)).status_code == 404


# --- Erasure ------------------------------------------------------------------------------------------


async def test_parent_of_a_current_pupil_cant_be_erased(client, world):
    response = await client.post(
        f"/api/v1/guardians/{world['guardian'].id}/erase", headers=world["proprietor"], json={"reason": "Asked to"}
    )
    assert response.status_code == 409 and "current pupil" in response.json()["error"]["message"]


async def test_erasing_a_parent_and_pupil_keeps_the_money(client, world, db):
    before = (await client.get(f"/api/v1/students/{world['child'].id}/account", headers=world["bursar"])).json()
    parent = bearer(await sign_in(client))
    await withdraw(client, world, world["child"])

    assert (
        await client.post(
            f"/api/v1/students/{world['child'].id}/erase", headers=world["bursar"], json={"reason": "Request"}
        )
    ).status_code == 403  # only school admins and proprietors erase
    erased_child = await client.post(
        f"/api/v1/students/{world['child'].id}/erase", headers=world["admin"], json={"reason": "Parent's request"}
    )
    assert erased_child.status_code == 200
    erased_parent = await client.post(
        f"/api/v1/guardians/{world['guardian'].id}/erase", headers=world["admin"], json={"reason": "Parent's request"}
    )
    assert erased_parent.status_code == 200
    again = await client.post(
        f"/api/v1/guardians/{world['guardian'].id}/erase", headers=world["admin"], json={"reason": "Twice"}
    )
    assert again.status_code == 409

    detail = (await client.get(f"/api/v1/students/{world['child'].id}", headers=world["admin"])).json()
    assert detail["erased"] is True and detail["full_name"] == "Erased pupil" and detail["date_of_birth"] is None
    assert detail["guardians"][0]["full_name"] == "Erased" and detail["guardians"][0]["erased"] is True
    assert detail["guardians"][0]["phone"] == "" and detail["guardians"][0]["email"] is None
    # The money is untouched
    after = (await client.get(f"/api/v1/students/{world['child'].id}/account", headers=world["bursar"])).json()
    assert (after["billed_kobo"], after["paid_kobo"]) == (before["billed_kobo"], before["paid_kobo"])
    # Message texts are gone, the parent's session ended, and the number no longer signs in
    scope_to_school(db, world["school"].id)
    assert set(db.scalars(select(MessageLog.body))) == {""}
    assert (await client.get("/api/v1/parent/home", headers=parent)).status_code == 401
    texts_before = len(sms.sent_sms)
    assert (await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})).status_code == 202
    assert len(sms.sent_sms) == texts_before  # an unknown number now: no code is sent


async def test_current_pupils_cant_be_erased(client, world):
    response = await client.post(
        f"/api/v1/students/{world['child'].id}/erase", headers=world["admin"], json={"reason": "Oops"}
    )
    assert response.status_code == 409


# --- Retention ----------------------------------------------------------------------------------------


async def test_retention_clears_old_texts_and_long_departed_pupils(client, world, db, connection):
    # A sibling who's still at school keeps their parent on record
    school = world["school"]
    stayer = make_student(school, world["terms"][0], world["levels"][0].arms[0], first="Stays")
    leaver = make_student(school, world["terms"][0], world["levels"][0].arms[0], first="Left", last="Long Ago")
    lone_parent = link_guardian(school, [leaver], phone="+2348077770000", name="Mr Long Ago")
    shared_parent = link_guardian(school, [stayer], phone="+2348077771111", name="Mrs Stays")
    db.commit()
    scope_platform_wide(db)
    db.execute(
        update(Student)
        .where(Student.id == leaver.id)
        .values(status=StudentStatus.WITHDRAWN, left_on=datetime.now(UTC).date() - timedelta(days=7 * 366))
    )
    db.execute(update(MessageLog).values(created_at=datetime.now(UTC) - timedelta(days=400)))
    db.commit()

    with QueryCounter(connection) as counter:
        result = privacy.apply_retention()
    assert result["pupils_erased"] == 1 and result["parents_erased"] == 1 and result["message_texts_cleared"] >= 1
    assert counter.count < 15  # bulk statements, whatever the numbers
    db.expire_all()
    assert db.get(Student, leaver.id).first_name == "Erased"
    assert db.get(Student, stayer.id).first_name == "Stays"
    assert db.get(Guardian, lone_parent.id).full_name == "Erased"
    assert db.get(Guardian, shared_parent.id).full_name == "Mrs Stays"
    assert db.get(Guardian, world["guardian"].id).full_name == "Mrs Adeyemi"  # their child is current
    assert privacy.apply_retention()["pupils_erased"] == 0  # nothing twice

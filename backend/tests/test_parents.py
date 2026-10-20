"""Parent portal: phone + code sign-in, and seeing only your own children."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text, update

from app.core.crypto import blind_index
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from app.modules.notifications import sms
from app.modules.parents.models import ParentOtp
from app.modules.schools.models import SchoolStatus
from app.modules.students.models import Guardian, GuardianStudent
from tests.factories import headers_for, make_calendar, make_levels, make_school, make_student, make_user, tenant_writer
from tests.test_fees import add_fees
from tests.test_payments import connect_gateway, pay_cash

PHONE = "+2348031112222"


def link_guardian(school, students, phone=PHONE, name="Mrs Adeyemi"):
    with tenant_writer() as w:
        guardian = Guardian(
            id=uuid.uuid4(), school_id=school.id, full_name=name, phone=phone, phone_index=blind_index(phone)
        )
        w.add(guardian)
        w.flush()
        w.add_all(
            GuardianStudent(school_id=school.id, guardian_id=guardian.id, student_id=s.id, is_primary=True)
            for s in students
        )
    return guardian


@pytest.fixture
def world(db):
    school = make_school(db, "Alpha College")
    bursar, admin = make_user(db, school, Role.BURSAR), make_user(db, school, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(school)
    levels = make_levels(school)
    add_fees(school, terms[0], levels)
    mine = [
        make_student(school, terms[0], levels[0].arms[0], first="Tolu"),
        make_student(school, terms[0], levels[1].arms[0], first="Bisi"),
    ]
    stranger = make_student(school, terms[0], levels[0].arms[0], first="Stranger")
    guardian = link_guardian(school, mine)
    link_guardian(school, [stranger], phone="+2348099998888", name="Someone Else")
    connect_gateway(school)
    db.commit()
    return {
        "school": school,
        "terms": terms,
        "mine": mine,
        "stranger": stranger,
        "guardian": guardian,
        "bursar": headers_for(db, bursar),
        "admin": headers_for(db, admin),
    }


def last_code() -> str:
    return re.search(r"\b(\d{6})\b", sms.sent_sms[-1].text).group(1)


async def sign_in(client, phone="08031112222"):
    assert (await client.post("/api/v1/parent/auth/request-code", json={"phone": phone})).status_code == 202
    response = await client.post("/api/v1/parent/auth/verify", json={"phone": phone, "code": last_code()})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(signed_in):
    return {"Authorization": f"Bearer {signed_in['access_token']}"}


# --- Sign-in ----------------------------------------------------------------------------------------


async def test_code_is_texted_and_signs_the_parent_in(client, world, db):
    body = await sign_in(client)
    assert body["status"] == "signed_in"
    assert sms.sent_sms[-1].to == PHONE and sms.sent_sms[-1].route == "dnd"
    assert "sp_parent_refresh" in client.cookies
    # Only a keyed hash of the code is stored
    raw = db.execute(text("SELECT code_hash FROM parent_otps")).scalar()
    assert last_code() not in raw


async def test_unknown_numbers_get_the_same_answer_and_no_text(client, world):
    response = await client.post("/api/v1/parent/auth/request-code", json={"phone": "08000000000"})
    assert response.status_code == 202
    assert sms.sent_sms == []
    bad = await client.post("/api/v1/parent/auth/request-code", json={"phone": "123"})
    assert bad.status_code in (400, 422)


async def test_wrong_codes_are_limited(client, world):
    await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    code = last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        response = await client.post("/api/v1/parent/auth/verify", json={"phone": PHONE, "code": wrong})
        assert response.json()["error"]["code"] == "otp_invalid"
    late = await client.post("/api/v1/parent/auth/verify", json={"phone": PHONE, "code": code})
    assert late.status_code == 401 and late.json()["error"]["code"] == "otp_expired"


async def test_codes_expire_and_a_new_one_replaces_the_old(client, world, db):
    await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    first = last_code()
    scope_platform_wide(db)
    db.execute(update(ParentOtp).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()
    expired = await client.post("/api/v1/parent/auth/verify", json={"phone": PHONE, "code": first})
    assert expired.json()["error"]["code"] == "otp_expired"

    await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    second = last_code()
    await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    third = last_code()
    if second != third:
        old = await client.post("/api/v1/parent/auth/verify", json={"phone": PHONE, "code": second})
        assert old.status_code == 401
    ok = await client.post("/api/v1/parent/auth/verify", json={"phone": PHONE, "code": third})
    assert ok.status_code == 200


async def test_code_requests_are_rate_limited_per_phone(client, world):
    for _ in range(3):
        await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    limited = await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    assert limited.status_code == 429


async def test_parent_with_children_at_two_schools_chooses_one(client, world, db):
    other = make_school(db, "Beta Academy")
    _, other_terms = make_calendar(other)
    other_child = make_student(other, other_terms[0], make_levels(other)[0].arms[0], first="Kemi")
    other_guardian = link_guardian(other, [other_child])
    db.commit()
    await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    choice = (await client.post("/api/v1/parent/auth/verify", json={"phone": PHONE, "code": last_code()})).json()
    assert choice["status"] == "choose_school"
    assert {s["school_name"] for s in choice["schools"]} == {"Alpha College", "Beta Academy"}
    stranger_id = str(uuid.uuid4())
    refused = await client.post(
        "/api/v1/parent/auth/choose-school", json={"ticket": choice["ticket"], "guardian_id": stranger_id}
    )
    assert refused.status_code == 400
    chosen = await client.post(
        "/api/v1/parent/auth/choose-school", json={"ticket": choice["ticket"], "guardian_id": str(other_guardian.id)}
    )
    home = (await client.get("/api/v1/parent/home", headers=bearer(chosen.json()))).json()
    assert home["school_name"] == "Beta Academy"
    assert [c["first_name"] for c in home["children"]] == ["Kemi"]


# --- The portal -------------------------------------------------------------------------------------


async def test_home_shows_my_children_with_balances_and_account_numbers(client, world):
    term = world["terms"][0]
    await client.post(f"/api/v1/terms/{term.id}/invoices/generate", headers=world["bursar"], json={"dry_run": False})
    await client.post("/api/v1/virtual-accounts/provision", headers=world["bursar"], json={})
    home = (await client.get("/api/v1/parent/home", headers=bearer(await sign_in(client)))).json()
    assert home["guardian_name"] == "Mrs Adeyemi"
    children = {c["first_name"]: c for c in home["children"]}
    assert set(children) == {"Tolu", "Bisi"}  # not the stranger
    assert children["Tolu"]["owed_kobo"] == 155_000 * 100
    assert children["Tolu"]["pay_in"]["account_number"].isdigit()
    assert children["Tolu"]["class_name"] == "JSS 1 A"
    assert sum(f["left_kobo"] for f in children["Tolu"]["fees"]) == 155_000 * 100  # the fees that make it up
    assert all(f["status"] == "Unpaid" for f in children["Tolu"]["fees"])


async def test_child_account_payments_receipts_and_card_checkout(client, world):
    term, tolu = world["terms"][0], world["mine"][0]
    await client.post(f"/api/v1/terms/{term.id}/invoices/generate", headers=world["bursar"], json={"dry_run": False})
    await pay_cash(client, {**world, "student": tolu}, 55_000)
    h = bearer(await sign_in(client))
    account = (await client.get(f"/api/v1/parent/children/{tolu.id}", headers=h)).json()
    [invoice] = account["invoices"]
    assert invoice["balance_kobo"] == 100_000 * 100 and invoice["status"] == "part_paid"
    [payment] = account["payments"]
    assert payment["receipt_number"] is not None
    pdf = await client.get(f"/api/v1/parent/receipts/{payment['receipt_id']}/pdf", headers=h)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    lines = (await client.get(f"/api/v1/parent/invoices/{invoice['id']}", headers=h)).json()["lines"]
    assert {line["description"] for line in lines} == {"Tuition", "PTA"}

    checkout = await client.post(f"/api/v1/parent/children/{tolu.id}/pay", headers=h, json={})
    assert checkout.status_code == 201
    assert checkout.json()["amount_kobo"] == 100_000 * 100
    assert checkout.json()["authorization_url"].startswith("https://checkout.fake/")


async def test_parents_cannot_see_other_children(client, world, db):
    term, stranger = world["terms"][0], world["stranger"]
    await client.post(f"/api/v1/terms/{term.id}/invoices/generate", headers=world["bursar"], json={"dry_run": False})
    await pay_cash(client, {**world, "student": stranger}, 1_000)
    stranger_account = (await client.get(f"/api/v1/students/{stranger.id}/account", headers=world["bursar"])).json()
    stranger_invoice = stranger_account["invoices"][0]["id"]
    payments = (await client.get(f"/api/v1/payments?student_id={stranger.id}", headers=world["bursar"])).json()
    stranger_receipt = payments["items"][0]["receipt_id"]

    other = make_school(db)
    _, other_terms = make_calendar(other)
    far_child = make_student(other, other_terms[0], make_levels(other)[0].arms[0])
    db.commit()
    h = bearer(await sign_in(client))
    for method, url, body in [
        ("get", f"/api/v1/parent/children/{stranger.id}", None),
        ("get", f"/api/v1/parent/children/{far_child.id}", None),
        ("get", f"/api/v1/parent/invoices/{stranger_invoice}", None),
        ("get", f"/api/v1/parent/receipts/{stranger_receipt}/pdf", None),
        ("post", f"/api/v1/parent/children/{stranger.id}/pay", {"amount_kobo": 100}),
    ]:
        response = await client.request(method, url, headers=h, json=body)
        assert response.status_code == 404, url


async def test_parent_and_staff_tokens_are_not_interchangeable(client, world):
    parent = bearer(await sign_in(client))
    assert (await client.get("/api/v1/students", headers=parent)).status_code == 401
    assert (await client.get("/api/v1/parent/home", headers=world["admin"])).status_code == 401


async def test_refresh_rotates_reuse_ends_the_session_and_logout_works(client, world):
    signed_in = await sign_in(client)
    old = client.cookies.get("sp_parent_refresh")
    rotated = await client.post("/api/v1/parent/auth/refresh")
    assert rotated.status_code == 200
    client.cookies.set("sp_parent_refresh", old, path="/api/v1/parent/auth")
    assert (await client.post("/api/v1/parent/auth/refresh")).status_code == 401
    assert (await client.get("/api/v1/parent/home", headers=bearer(signed_in))).status_code == 401

    fresh = await sign_in(client)
    assert (await client.post("/api/v1/parent/auth/logout", headers=bearer(fresh))).status_code == 204
    assert (await client.get("/api/v1/parent/home", headers=bearer(fresh))).status_code == 401


async def test_suspended_school_locks_parents_out(client, world, db):
    signed_in = await sign_in(client)
    world["school"].status = SchoolStatus.SUSPENDED
    db.merge(world["school"])
    db.commit()
    assert (await client.get("/api/v1/parent/home", headers=bearer(signed_in))).status_code == 403
    sms.sent_sms.clear()
    await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    assert sms.sent_sms == []


async def test_the_code_never_reaches_the_logs(client, world, caplog):
    with caplog.at_level("DEBUG"):
        await client.post("/api/v1/parent/auth/request-code", json={"phone": PHONE})
    assert sms.sent_sms, "a code should have been sent"
    assert last_code() not in caplog.text

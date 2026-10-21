"""Family payment links: the parent's page, paying several children by card with a chosen split, and the link's life."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.core.clock import lagos_today
from app.core.config import get_settings
from app.core.tenancy import scope_to_school
from app.modules.accounts.models import Role
from app.modules.audit.models import AuditLog
from app.modules.notifications import whatsapp
from app.modules.payments.gateways import FakeGateway
from app.modules.payments.models import FamilyPayLink, Payment
from app.modules.receipts.models import Receipt
from app.modules.students.models import GuardianStudent
from tests.factories import (
    QueryCounter,
    headers_for,
    make_calendar,
    make_levels,
    make_school,
    make_student,
    make_user,
    tenant_writer,
)
from tests.test_fees import add_fees, generate
from tests.test_parents import bearer, link_guardian, sign_in
from tests.test_payments import connect_gateway, pay_cash

NAIRA = 100


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    monkeypatch.setattr(get_settings(), "quiet_hours_start", 0)
    monkeypatch.setattr(get_settings(), "quiet_hours_end", 0)


@pytest.fixture
def world(db):
    """Mrs Adeyemi has Ada (JSS 1, owes 155k) and Bayo (JSS 2, owes 185k); Chidi is another family's."""
    school = make_school(db, "Alpha College")
    bursar, admin = make_user(db, school, Role.BURSAR), make_user(db, school, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(school)
    levels = make_levels(school)
    add_fees(school, terms[0], levels)
    ada = make_student(school, terms[0], levels[0].arms[0], first="Ada", last="Adeyemi")
    bayo = make_student(school, terms[0], levels[1].arms[0], first="Bayo", last="Adeyemi")
    chidi = make_student(school, terms[0], levels[2].arms[0], first="Chidi", last="Okafor")
    guardian = link_guardian(school, [ada, bayo])
    other = link_guardian(school, [chidi], phone="+2348039998888", name="Mr Okafor")
    connect_gateway(school)
    db.commit()
    return {
        "school": school,
        "terms": terms,
        "levels": levels,
        "ada": ada,
        "bayo": bayo,
        "chidi": chidi,
        "student": ada,
        "guardian": guardian,
        "other": other,
        "bursar": headers_for(db, bursar),
        "admin": headers_for(db, admin),
    }


async def staff_link(client, world, guardian=None, headers=None):
    guardian = guardian or world["guardian"]
    response = await client.get(f"/api/v1/guardians/{guardian.id}/pay-link", headers=headers or world["bursar"])
    assert response.status_code == 200, response.text
    return response.json()["url"].rsplit("/", 1)[1]


async def page(client, token):
    return await client.get(f"/api/v1/public/pay/{token}")


async def checkout(client, token, world, **amounts_naira):
    items = [{"student_id": str(world[name].id), "amount_kobo": naira * NAIRA} for name, naira in amounts_naira.items()]
    return await client.post(f"/api/v1/public/pay/{token}/checkout", json={"items": items})


async def owed(client, world, name):
    account = await client.get(f"/api/v1/students/{world[name].id}/account", headers=world["bursar"])
    return account.json()["outstanding_kobo"]


# --- The page --------------------------------------------------------------------------------------


async def test_parent_sees_only_their_children_with_first_names(client, world):
    await generate(client, world)
    token = await staff_link(client, world)
    assert await staff_link(client, world) == token  # the same link until it expires

    response = await page(client, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["school_name"] == "Alpha College" and body["card_payments"] is True
    kids = {c["first_name"]: c for c in body["children"]}
    assert set(kids) == {"Ada", "Bayo"}  # never Chidi
    assert kids["Ada"]["owed_kobo"] == kids["Ada"]["suggested_kobo"] == 155_000 * NAIRA
    assert kids["Bayo"]["class_name"] == "JSS 2 A"
    ada_fees = {f["name"]: f["left_kobo"] for f in kids["Ada"]["fees"]}  # what the 155,000 is made of
    assert ada_fees == {"PTA": 5_000 * NAIRA, "Tuition": 150_000 * NAIRA} and kids["Ada"]["earlier_left_kobo"] == 0
    assert "Adeyemi" not in response.text  # first names only (the parent's own name is shown to them)


async def test_unknown_expired_and_replaced_links_look_the_same(client, world, db):
    token = await staff_link(client, world)
    for bad in ("x" * 32, token[:-1] + ("A" if token[-1] != "A" else "B")):
        response = await page(client, bad)
        assert response.status_code == 404 and "expired or been replaced" in response.text

    replaced = await client.post(f"/api/v1/guardians/{world['guardian'].id}/pay-link/replace", headers=world["bursar"])
    assert replaced.status_code == 200, replaced.text
    new = replaced.json()["url"].rsplit("/", 1)[1]
    assert (await page(client, token)).status_code == 404
    assert (await page(client, new)).status_code == 200
    scope_to_school(db, world["school"].id)
    assert db.scalar(select(func.count()).where(AuditLog.action == "payments.family_link_issued")) == 1

    with tenant_writer() as w:  # time passes
        w.execute(update(FamilyPayLink).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    assert (await page(client, new)).status_code == 404
    assert await staff_link(client, world) not in (token, new)  # a fresh one is made when next needed


async def test_payment_plan_asks_for_the_next_instalment(client, world):
    report = await generate(client, world)
    query = {"student_id": str(world["ada"].id)}
    invoices = await client.get("/api/v1/invoices", headers=world["bursar"], params=query)
    invoice_id = invoices.json()["items"][0]["id"]
    plan = await client.post(
        f"/api/v1/invoices/{invoice_id}/plan",
        headers=world["bursar"],
        json={
            "instalments": [
                {"due_date": str(lagos_today() + timedelta(days=10)), "amount_kobo": 55_000 * NAIRA},
                {"due_date": str(lagos_today() + timedelta(days=40)), "amount_kobo": 100_000 * NAIRA},
            ]
        },
    )
    assert plan.status_code in (200, 201), plan.text
    kids = {c["first_name"]: c for c in (await page(client, await staff_link(client, world))).json()["children"]}
    assert kids["Ada"]["on_plan"] and kids["Ada"]["suggested_kobo"] == 55_000 * NAIRA
    assert kids["Ada"]["owed_kobo"] == 155_000 * NAIRA and report


async def test_page_is_a_fixed_number_of_queries(client, world, db, connection):
    await generate(client, world)
    token = await staff_link(client, world)
    with QueryCounter(connection) as two:
        await page(client, token)
    more = [make_student(world["school"], world["terms"][0], world["levels"][0].arms[0]) for _ in range(4)]
    with tenant_writer() as w:
        w.add_all(
            GuardianStudent(school_id=world["school"].id, guardian_id=world["guardian"].id, student_id=s.id)
            for s in more
        )
    with QueryCounter(connection) as six:
        assert len((await page(client, token)).json()["children"]) == 6
    assert six.count == two.count


# --- Paying ----------------------------------------------------------------------------------------


async def test_one_card_payment_is_split_as_the_parent_chose(client, world, db):
    await generate(client, world)
    token = await staff_link(client, world)
    started = await checkout(client, token, world, ada=155_000, bayo=50_000)
    assert started.status_code == 201, started.text
    reference = started.json()["reference"]
    assert started.json()["amount_kobo"] == 205_000 * NAIRA

    headers, body = FakeGateway.shared().complete_checkout(reference)
    verified = await client.post("/api/v1/payments/verify", json={"reference": reference})
    assert verified.json()["status"] == "succeeded"
    await client.post("/api/v1/webhooks/fake", headers=headers, content=body)  # the webhook too: still once
    await client.post("/api/v1/payments/verify", json={"reference": reference})

    assert await owed(client, world, "ada") == 0
    assert await owed(client, world, "bayo") == 135_000 * NAIRA
    scope_to_school(db, world["school"].id)
    payments = {p.gateway_reference: p for p in db.scalars(select(Payment))}
    assert set(payments) == {f"{reference}-1", f"{reference}-2"}
    assert {p.amount_kobo for p in payments.values()} == {155_000 * NAIRA, 50_000 * NAIRA}
    assert db.scalar(select(func.count()).select_from(Receipt)) == 2  # a receipt per child
    assert all(p.reference == reference for p in payments.values())


async def test_choices_are_checked(client, world, db):
    await generate(client, world)
    token = await staff_link(client, world)

    async def refused(**amounts):
        response = await checkout(client, token, world, **amounts)
        assert response.status_code in (400, 404, 422), response.text
        return response.json()["error"]["message"]

    assert "up to what's owed" in await refused(ada=155_001)
    assert "at least ₦1,000" in await refused(ada=500)
    assert "at least one child" in await refused(ada=0, bayo=0)
    assert "not found" in (await refused(chidi=10_000)).lower()  # another family's child

    await pay_cash(client, world, 155_000, student=world["ada"])
    assert "owes nothing" in await refused(ada=5_000)
    small = await checkout(client, token, world, bayo=185_000)
    assert small.status_code == 201


async def test_gateway_total_mismatch_is_parked_not_guessed(client, world, db):
    await generate(client, world)
    token = await staff_link(client, world)
    reference = (await checkout(client, token, world, ada=100_000, bayo=100_000)).json()["reference"]
    FakeGateway.shared().complete_checkout(reference, amount_kobo=150_000 * NAIRA)
    result = await client.post("/api/v1/payments/verify", json={"reference": reference})
    assert result.json()["status"] == "needs_review"
    scope_to_school(db, world["school"].id)
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


async def test_bills_and_reminders_carry_the_link(client, world):
    await generate(client, world, due_date=str(lagos_today() + timedelta(days=14)), notify_parents=True)
    notices = [m for m in whatsapp.sent_whatsapp if m.template == "fee_notice"]
    assert len(notices) == 3
    token = await staff_link(client, world)
    mine = [m for m in notices if m.parameters[0] == "Mrs Adeyemi"]
    assert {m.parameters[6] for m in mine} == {f"{get_settings().frontend_url}/pay/f/{token}"}  # one link per parent


# --- Who can do what -------------------------------------------------------------------------------


async def test_staff_need_payment_rights_and_stay_in_their_school(client, world, db):
    other = make_school(db)
    stranger = make_user(db, other, Role.BURSAR)
    proprietor = make_user(db, world["school"], Role.PROPRIETOR)
    db.commit()
    url = f"/api/v1/guardians/{world['guardian'].id}/pay-link"
    assert (await client.get(url, headers=headers_for(db, stranger))).status_code == 404
    assert (await client.post(f"{url}/replace", headers=headers_for(db, stranger))).status_code == 404
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=headers_for(db, proprietor))).status_code == 200


async def test_erasing_a_parent_ends_their_link(client, world, db):
    token = await staff_link(client, world, guardian=world["other"])
    withdraw = await client.post(
        f"/api/v1/students/{world['chidi'].id}/withdraw",
        headers=world["admin"],
        json={"left_on": "2026-10-01", "reason": "Moved away"},
    )
    assert withdraw.status_code == 200, withdraw.text
    erase = await client.post(
        f"/api/v1/guardians/{world['other'].id}/erase", headers=world["admin"], json={"reason": "Parent asked"}
    )
    assert erase.status_code == 200, erase.text
    assert (await page(client, token)).status_code == 404


async def test_signed_in_parent_pays_for_all_children_by_transfer(client, world, db):
    await generate(client, world)
    parent = bearer(await sign_in(client))
    family = await client.get("/api/v1/parent/family", headers=parent)
    assert family.status_code == 200, family.text
    assert [c["first_name"] for c in family.json()["children"]] == ["Ada", "Bayo"]

    items = [
        {"student_id": str(world["ada"].id), "amount_kobo": 155_000 * NAIRA},
        {"student_id": str(world["bayo"].id), "amount_kobo": 185_000 * NAIRA},
    ]
    started = await client.post("/api/v1/parent/family/checkout", headers=parent, json={"items": items})
    assert started.status_code == 201, started.text
    reference = started.json()["reference"]
    FakeGateway.shared().complete_checkout(
        reference, channel="bank_transfer"
    )  # a temporary account on the checkout page
    assert (await client.post("/api/v1/payments/verify", json={"reference": reference})).json()["status"] == "succeeded"

    assert await owed(client, world, "ada") == await owed(client, world, "bayo") == 0
    scope_to_school(db, world["school"].id)
    assert {p.method.value for p in db.scalars(select(Payment))} == {"checkout_transfer"}
    # The same link the parent gets in messages
    now = (await client.get("/api/v1/parent/family", headers=parent)).json()
    assert now == (await page(client, await staff_link(client, world))).json()


async def test_portal_family_payment_is_only_for_my_children(client, world):
    await generate(client, world)
    parent = bearer(await sign_in(client))
    items = [{"student_id": str(world["chidi"].id), "amount_kobo": 10_000 * NAIRA}]
    refused = await client.post("/api/v1/parent/family/checkout", headers=parent, json={"items": items})
    assert refused.status_code == 404
    assert (await client.get("/api/v1/parent/family", headers=world["bursar"])).status_code == 401

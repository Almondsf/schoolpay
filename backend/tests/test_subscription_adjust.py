"""Adjusting a school's subscription bill: discounts (e.g. the set-up fee waived) and due dates."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import lagos_today
from tests.test_platform_billing import NAIRA, STANDARD, generate, invoices
from tests.test_platform_billing import world as world  # the fixture


async def alpha_invoice(client, world):
    await generate(client, world)
    [bill] = await invoices(client, world, school_id=str(world["alpha"].id))
    return bill, f"/api/v1/platform/billing/invoices/{bill['id']}"


async def test_waiving_the_setup_fee_is_a_discount_line(client, world):
    bill, url = await alpha_invoice(client, world)
    done = await client.post(
        f"{url}/discount", headers=world["admin"], json={"amount_kobo": STANDARD[0], "reason": "Set-up fee waived"}
    )
    assert done.status_code == 200, done.text
    detail = done.json()
    assert detail["invoice"]["total_kobo"] == bill["total_kobo"] - STANDARD[0]
    discount = next(ln for ln in detail["lines"] if ln["kind"] == "discount")
    assert discount["amount_kobo"] == -STANDARD[0] and discount["description"] == "Discount: Set-up fee waived"
    # The school sees the lower bill too
    [mine] = (await client.get("/api/v1/subscription", headers=world["alpha_bursar"])).json()["invoices"]
    assert mine["balance_kobo"] == bill["total_kobo"] - STANDARD[0]


async def test_a_discount_can_never_exceed_what_is_still_owed(client, world):
    bill, url = await alpha_invoice(client, world)
    await client.post(
        f"{url}/payments",
        headers=world["admin"],
        json={"amount_kobo": bill["total_kobo"] - 1_000 * NAIRA, "received_on": lagos_today().isoformat()},
    )
    too_much = await client.post(
        f"{url}/discount", headers=world["admin"], json={"amount_kobo": 1_001 * NAIRA, "reason": "Goodwill"}
    )
    assert too_much.status_code == 400 and too_much.json()["error"]["field"] == "amount_kobo"
    rest = await client.post(
        f"{url}/discount", headers=world["admin"], json={"amount_kobo": 1_000 * NAIRA, "reason": "Goodwill"}
    )
    assert rest.json()["invoice"]["status"] == "paid"


async def test_the_database_refuses_a_negative_charge_or_a_positive_discount(client, world, db):
    await alpha_invoice(client, world)
    insert = (
        "INSERT INTO subscription_lines (id, school_id, invoice_id, kind, description, quantity, unit_kobo, "
        "amount_kobo) SELECT gen_random_uuid(), school_id, invoice_id, '{kind}', 'x', 1, {n}, {n} "
        "FROM subscription_lines LIMIT 1"
    )
    for statement in (insert.format(kind="pupils", n=-1), insert.format(kind="discount", n=1)):
        with pytest.raises(DBAPIError, match="amount_sign"):
            db.execute(text(statement))
        db.rollback()


async def test_moving_the_due_date_and_its_limits(client, world):
    _, url = await alpha_invoice(client, world)
    later = (lagos_today() + timedelta(days=60)).isoformat()
    moved = await client.put(
        f"{url}/due-date", headers=world["admin"], json={"due_date": later, "reason": "Asked for more time"}
    )
    assert moved.status_code == 200 and moved.json()["invoice"]["due_date"] == later
    same = await client.put(f"{url}/due-date", headers=world["admin"], json={"due_date": later, "reason": "Again"})
    assert same.status_code == 400
    before_issue = (lagos_today() - timedelta(days=5)).isoformat()
    early = await client.put(
        f"{url}/due-date", headers=world["admin"], json={"due_date": before_issue, "reason": "Oops"}
    )
    assert early.status_code == 400 and early.json()["error"]["field"] == "due_date"


async def test_only_platform_admins_and_not_on_a_void_bill(client, world):
    _, url = await alpha_invoice(client, world)
    as_school = await client.post(
        f"{url}/discount", headers=world["alpha_bursar"], json={"amount_kobo": 100, "reason": "Please"}
    )
    assert as_school.status_code == 403
    await client.post(f"{url}/void", headers=world["admin"], json={"reason": "Wrong plan"})
    void = await client.post(f"{url}/discount", headers=world["admin"], json={"amount_kobo": 100, "reason": "Late"})
    assert void.status_code == 409

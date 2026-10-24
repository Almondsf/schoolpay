"""Unusual activity: what a proprietor should look at, and who recorded it."""

from datetime import timedelta

from sqlalchemy import text

from app.core.clock import lagos_today
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from tests.factories import QueryCounter, headers_for, make_school, make_user
from tests.test_cashbook import bank, take_cash
from tests.test_expenses import spend
from tests.test_payments import NAIRA, account, generate
from tests.test_payments import world as world  # the fixture

URL = "/api/v1/reports/unusual-activity"


async def look(client, world, headers=None):
    response = await client.get(URL, headers=headers or world["proprietor"])
    assert response.status_code == 200, response.text
    return {s["key"]: s for s in response.json()["signals"]}


async def test_each_kind_shows_with_who_recorded_it(client, world, db):
    today = lagos_today()
    await generate(client, world)
    # Three reversals by one person
    for naira in (1_000, 2_000, 3_000):
        await take_cash(client, world, naira, day=today)
    scope_platform_wide(db)
    payment_ids = [r[0] for r in db.execute(text("SELECT id FROM payments ORDER BY amount_kobo"))]
    db.rollback()
    for payment_id in payment_ids:
        reversed_ = await client.post(
            f"/api/v1/payments/{payment_id}/reverse", headers=world["bursar"], json={"reason": "Teller bounced"}
        )
        assert reversed_.status_code in (200, 201), reversed_.text
    await take_cash(client, world, 4_000, day=today - timedelta(days=5))  # entered five days late
    invoice_id = (await account(client, world))["invoices"][0]["id"]
    await client.post(
        f"/api/v1/invoices/{invoice_id}/lines",
        headers=world["bursar"],
        json={"kind": "adjustment", "description": "Fees cut", "amount_kobo": -60_000 * NAIRA, "reason": "Asked"},
    )
    await client.post(
        f"/api/v1/invoices/{invoice_id}/write-off",
        headers=world["proprietor"],
        json={"amount_kobo": 1_000 * NAIRA, "reason": "Staff child"},
    )
    deposit = await bank(client, world, 500, day=today)
    await client.post(
        f"/api/v1/cash-deposits/{deposit['id']}/reject", headers=world["bursar2"], json={"reason": "Not on statement"}
    )
    await spend(client, world, 7_500, paid_from="bank", name="Transport")
    await spend(client, world, 7_500, paid_from="bank", name="Transport")  # the same again
    counted = await client.post(
        "/api/v1/cash-book/close", headers=world["bursar"], json={"day": today.isoformat(), "counted_kobo": 0}
    )
    assert counted.status_code == 201, counted.text

    found = await look(client, world)
    assert found["reversals"]["serious"] is True and len(found["reversals"]["items"]) == 3
    assert "reversed 3 or more" in found["reversals"]["why"]
    assert found["reversals"]["items"][0]["who"]  # the bursar's name
    assert found["backdated_cash"]["items"][0]["amount_kobo"] == 4_000 * NAIRA
    reduction = found["bill_reductions"]
    assert reduction["serious"] is True and reduction["items"][0]["amount_kobo"] == -60_000 * NAIRA
    assert reduction["items"][0]["link"] == f"/console/invoices/{invoice_id}"
    assert "the proprietor's own" in found["write_offs"]["items"][0]["what"]
    assert found["rejected_banking"]["items"][0]["amount_kobo"] == 500 * NAIRA
    assert len(found["duplicate_spending"]["items"]) == 1
    assert found["short_counts"]["items"][0]["amount_kobo"] < 0


async def test_only_the_proprietor_sees_it_and_queries_are_fixed(client, world, db, connection):
    assert (await client.get(URL, headers=world["bursar"])).status_code == 403
    assert (await client.get(URL, headers=world["admin"])).status_code == 403
    await generate(client, world)
    await take_cash(client, world, 1_000, day=lagos_today() - timedelta(days=4))
    stranger = headers_for(db, make_user(db, make_school(db, "Beta Academy"), Role.PROPRIETOR))
    db.commit()
    assert await look(client, world, stranger) == {}  # Beta sees nothing of Alpha's
    with QueryCounter(connection) as one:
        await look(client, world)
    for naira in (2_000, 3_000, 4_000):
        await take_cash(client, world, naira, day=lagos_today() - timedelta(days=6))
        await spend(client, world, naira, paid_from="bank")
        await spend(client, world, naira, paid_from="bank")
    with QueryCounter(connection) as many:
        await look(client, world)
    assert len(many.statements) == len(one.statements)

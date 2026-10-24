"""Writing off debt: anyone handling money asks, only the proprietor approves, and the ledger follows."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import lagos_today
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from tests.factories import headers_for, make_school, make_user
from tests.test_payments import NAIRA, account, generate, pay_cash
from tests.test_payments import world as world  # the fixture


async def bill(client, world):
    await generate(client, world)
    return (await account(client, world))["invoices"][0]["id"]


async def ask(client, world, invoice_id, naira, headers=None, reason="Family relocated abroad"):
    return await client.post(
        f"/api/v1/invoices/{invoice_id}/write-off",
        headers=headers or world["bursar"],
        json={"amount_kobo": naira * NAIRA, "reason": reason},
    )


async def test_the_proprietor_approves_and_the_balance_and_journal_follow(client, world):
    invoice_id = await bill(client, world)
    await pay_cash(client, world, 55_000)  # ₦100,000 left
    assert (await ask(client, world, invoice_id, 120_000)).status_code == 400  # more than owed
    asked = await ask(client, world, invoice_id, 60_000)
    assert asked.status_code == 201, asked.text
    request = asked.json()
    assert request["status"] == "pending" and request["invoice_number"] > 0
    assert (await ask(client, world, invoice_id, 1_000)).status_code == 409  # one open request per bill
    assert (await account(client, world))["outstanding_kobo"] == 100_000 * NAIRA  # nothing changes yet

    for other in ("bursar2", "admin"):  # two staff together can't do it
        refused = await client.post(f"/api/v1/write-offs/{request['id']}/approve", headers=world[other])
        assert refused.status_code == 403
    approved = await client.post(f"/api/v1/write-offs/{request['id']}/approve", headers=world["proprietor"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert (await account(client, world))["outstanding_kobo"] == 40_000 * NAIRA

    today = lagos_today().isoformat()
    journal = (
        await client.get("/api/v1/accounting/journal", headers=world["bursar"], params={"start": today, "end": today})
    ).json()
    moved = {t["account"]["key"]: t["debit_kobo"] - t["credit_kobo"] for t in journal["totals"]}
    assert moved["bad_debts"] == 60_000 * NAIRA
    listed = (await client.get("/api/v1/write-offs", headers=world["bursar"], params={"status": "approved"})).json()
    assert [w["id"] for w in listed] == [request["id"]]


async def test_money_arriving_meanwhile_and_rejection(client, world):
    invoice_id = await bill(client, world)
    request = (await ask(client, world, invoice_id, 150_000)).json()
    await pay_cash(client, world, 10_000)  # now only ₦145,000 is owed
    too_much = await client.post(f"/api/v1/write-offs/{request['id']}/approve", headers=world["proprietor"])
    assert too_much.status_code == 409 and too_much.json()["error"]["code"] == "write_off_too_much"
    vague = await client.post(
        f"/api/v1/write-offs/{request['id']}/reject", headers=world["proprietor"], json={"reason": ""}
    )
    assert vague.status_code == 422
    rejected = await client.post(
        f"/api/v1/write-offs/{request['id']}/reject", headers=world["proprietor"], json={"reason": "Parent will pay"}
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["rejection_reason"] == "Parent will pay"
    again = await client.post(f"/api/v1/write-offs/{request['id']}/approve", headers=world["proprietor"])
    assert again.status_code == 409


async def test_the_proprietors_own_applies_at_once_and_lines_cant_be_forged(client, world, db):
    invoice_id = await bill(client, world)
    own = await ask(client, world, invoice_id, 5_000, headers=world["proprietor"], reason="Staff child")
    assert own.status_code == 201 and own.json()["status"] == "approved"
    assert (await account(client, world))["outstanding_kobo"] == 150_000 * NAIRA
    forged = await client.post(
        f"/api/v1/invoices/{invoice_id}/lines",
        headers=world["bursar"],
        json={"kind": "write_off", "description": "Sneaky", "amount_kobo": -1_000 * NAIRA, "reason": "None"},
    )
    assert forged.status_code in (400, 422)  # refused by the schema, and by the service behind it
    scope_platform_wide(db)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM write_offs"))
    db.rollback()


async def test_other_schools_cant_touch_it(client, world, db):
    invoice_id = await bill(client, world)
    request = (await ask(client, world, invoice_id, 1_000)).json()
    beta = make_school(db, "Beta Academy")
    stranger = headers_for(db, make_user(db, beta, Role.PROPRIETOR))
    db.commit()
    assert (await ask(client, world, invoice_id, 1_000, headers=stranger)).status_code == 404
    assert (await client.post(f"/api/v1/write-offs/{request['id']}/approve", headers=stranger)).status_code == 404
    assert (await client.get("/api/v1/write-offs", headers=stranger)).json() == []

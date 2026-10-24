"""Spending: categories, maker-checker, voiding, the cash drawer, and income against expenditure."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import lagos_today
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from app.modules.expenses.models import DEFAULT_CATEGORIES
from tests.factories import QueryCounter, headers_for, make_school, make_user
from tests.test_cashbook import book, take_cash
from tests.test_payments import NAIRA, generate
from tests.test_payments import world as world  # the fixture

EXPENSES = "/api/v1/expenses"


async def category(client, world, name="Diesel and fuel"):
    listed = (await client.get("/api/v1/expense-categories", headers=world["bursar"])).json()
    return next(c for c in listed if c["name"] == name)


async def spend(client, world, naira, *, paid_from="cash", day=None, name="Diesel and fuel", headers=None):
    response = await client.post(
        EXPENSES,
        headers=headers or world["bursar"],
        json={
            "category_id": (await category(client, world, name))["id"],
            "amount_kobo": naira * NAIRA,
            "spent_on": (day or lagos_today()).isoformat(),
            "payee": "Total filling station",
            "description": "Generator diesel",
            "paid_from": paid_from,
            "reference": "RCPT-12",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_default_categories_are_made_once_and_a_school_can_add_its_own(client, world):
    first = (await client.get("/api/v1/expense-categories", headers=world["bursar"])).json()
    again = (await client.get("/api/v1/expense-categories", headers=world["bursar"])).json()
    assert {c["name"] for c in first} == set(DEFAULT_CATEGORIES) and len(again) == len(first)
    added = await client.post("/api/v1/expense-categories", headers=world["bursar"], json={"name": "Sports day"})
    assert added.status_code == 201
    clash = await client.post("/api/v1/expense-categories", headers=world["bursar"], json={"name": "sports DAY"})
    assert clash.status_code == 409
    retired = await client.put(
        f"/api/v1/expense-categories/{added.json()['id']}",
        headers=world["bursar"],
        json={"name": "Sports day", "is_active": False},
    )
    assert retired.json()["is_active"] is False
    refused = await client.post(
        EXPENSES,
        headers=world["bursar"],
        json={
            "category_id": added.json()["id"],
            "amount_kobo": 100,
            "spent_on": lagos_today().isoformat(),
            "payee": "Someone",
            "paid_from": "bank",
        },
    )
    assert refused.status_code == 400 and refused.json()["error"]["field"] == "category_id"


async def test_spending_is_approved_by_someone_else_and_voided_not_deleted(client, world, db):
    expense = await spend(client, world, 25_000)
    assert expense["status"] == "pending" and expense["category"] == "Diesel and fuel"
    own = await client.post(f"{EXPENSES}/{expense['id']}/approve", headers=world["bursar"])
    assert own.status_code == 403
    approved = await client.post(f"{EXPENSES}/{expense['id']}/approve", headers=world["bursar2"])
    assert approved.json()["status"] == "approved"
    vague = await client.post(f"{EXPENSES}/{expense['id']}/void", headers=world["proprietor"], json={"reason": ""})
    assert vague.status_code == 400
    voided = await client.post(
        f"{EXPENSES}/{expense['id']}/void", headers=world["proprietor"], json={"reason": "Recorded twice"}
    )
    assert voided.json()["status"] == "voided" and voided.json()["void_reason"] == "Recorded twice"
    other = await spend(client, world, 1_000)
    rejected = await client.post(
        f"{EXPENSES}/{other['id']}/reject", headers=world["bursar2"], json={"reason": "No receipt"}
    )
    assert rejected.json()["status"] == "rejected"
    listed = (await client.get(EXPENSES, headers=world["bursar"], params={"status": "voided"})).json()
    assert [e["id"] for e in listed["items"]] == [expense["id"]]
    scope_platform_wide(db)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM expenses"))
    db.rollback()
    future = (lagos_today() + timedelta(days=2)).isoformat()
    later = await client.post(
        EXPENSES,
        headers=world["bursar"],
        json={
            "category_id": expense["category_id"],
            "amount_kobo": 100,
            "spent_on": future,
            "payee": "Someone",
            "paid_from": "bank",
        },
    )
    assert later.status_code == 400


async def test_cash_spending_comes_off_the_drawer_and_bank_spending_does_not(client, world):
    await generate(client, world)
    today = lagos_today()
    await take_cash(client, world, 100_000, day=today)
    pending = await spend(client, world, 20_000, paid_from="cash")  # pending: the notes have left anyway
    await spend(client, world, 50_000, paid_from="bank")
    result = await book(client, world, today, today)
    assert result["held_now_kobo"] == 80_000 * NAIRA
    assert result["days"][-1]["spent_kobo"] == 20_000 * NAIRA
    await client.post(f"{EXPENSES}/{pending['id']}/reject", headers=world["bursar2"], json={"reason": "Wrong"})
    assert (await book(client, world, today, today))["held_now_kobo"] == 100_000 * NAIRA  # rejected: not spent


async def test_income_against_expenditure(client, world):
    await generate(client, world)
    today = lagos_today()
    await take_cash(client, world, 100_000, day=today)
    for naira, name in ((30_000, "Diesel and fuel"), (45_000, "Salaries and wages")):
        e = await spend(client, world, naira, paid_from="bank", name=name)
        await client.post(f"{EXPENSES}/{e['id']}/approve", headers=world["bursar2"])
    await spend(client, world, 5_000, paid_from="bank")  # not yet approved
    report = (
        await client.get(
            "/api/v1/reports/income-expenditure",
            headers=world["proprietor"],
            params={"start": today.isoformat(), "end": today.isoformat()},
        )
    ).json()
    assert report["income_kobo"] == 100_000 * NAIRA
    assert [(i["label"], i["amount_kobo"]) for i in report["income"]] == [("Cash", 100_000 * NAIRA)]
    assert [(e["label"], e["amount_kobo"]) for e in report["expenditure"]] == [
        ("Salaries and wages", 45_000 * NAIRA),
        ("Diesel and fuel", 30_000 * NAIRA),
    ]
    assert report["surplus_kobo"] == 25_000 * NAIRA and report["awaiting_approval_kobo"] == 5_000 * NAIRA
    assert report["billed_kobo"] > 0


async def test_another_school_sees_none_of_it_and_queries_are_fixed(client, world, db, connection):
    expense = await spend(client, world, 1_000)
    stranger = headers_for(db, make_user(db, make_school(db, "Beta Academy"), Role.BURSAR))
    db.commit()
    assert (await client.get(EXPENSES, headers=stranger)).json()["items"] == []
    assert (await client.post(f"{EXPENSES}/{expense['id']}/approve", headers=stranger)).status_code == 404
    today = lagos_today().isoformat()
    params = {"start": today, "end": today}
    with QueryCounter(connection) as one:
        await client.get("/api/v1/reports/income-expenditure", headers=world["proprietor"], params=params)
    for name in ("Rent", "Transport", "Electricity"):
        e = await spend(client, world, 1_000, paid_from="bank", name=name)
        await client.post(f"{EXPENSES}/{e['id']}/approve", headers=world["bursar2"])
    with QueryCounter(connection) as many:
        await client.get("/api/v1/reports/income-expenditure", headers=world["proprietor"], params=params)
    assert len(many.statements) == len(one.statements)

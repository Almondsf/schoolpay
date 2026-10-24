"""The cash book: cash taken, cash banked (checked by a second person), what should be held, and day closes."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import lagos_today
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from tests.factories import QueryCounter, headers_for, make_school, make_user
from tests.test_payments import NAIRA, generate
from tests.test_payments import world as world  # the fixture

BOOK = "/api/v1/cash-book"
DEPOSITS = "/api/v1/cash-deposits"


async def take_cash(client, world, naira, *, day, approve=True, method="cash"):
    recorded = await client.post(
        "/api/v1/manual-payments",
        headers=world["admin"],
        json={
            "student_id": str(world["student"].id),
            "amount_kobo": naira * NAIRA,
            "method": method,
            "received_on": day.isoformat(),
            "reference": "" if method == "cash" else "TLR-1",
        },
    )
    assert recorded.status_code == 201, recorded.text
    if approve:
        approved = await client.post(
            f"/api/v1/manual-payments/{recorded.json()['id']}/approve", headers=world["bursar"]
        )
        assert approved.status_code == 200, approved.text
    return recorded.json()


async def bank(client, world, naira, *, day, headers=None):
    response = await client.post(
        DEPOSITS,
        headers=headers or world["bursar"],
        json={"amount_kobo": naira * NAIRA, "banked_on": day.isoformat(), "bank_reference": "TLR-778"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def book(client, world, start, end):
    response = await client.get(
        BOOK, headers=world["bursar"], params={"start": start.isoformat(), "end": end.isoformat()}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_cash_taken_less_banked_is_what_should_be_held(client, world):
    await generate(client, world)
    today = lagos_today()
    monday, tuesday = today - timedelta(days=2), today - timedelta(days=1)
    await take_cash(client, world, 50_000, day=monday)
    await take_cash(client, world, 20_000, day=monday, approve=False)  # in the drawer, waiting for approval
    await take_cash(client, world, 30_000, day=tuesday)
    await take_cash(client, world, 99_000, day=tuesday, method="teller")  # not cash: not in the drawer
    await bank(client, world, 60_000, day=tuesday)

    result = await book(client, world, monday, today)
    days = {d["day"]: d for d in result["days"]}
    assert days[monday.isoformat()]["received_kobo"] == 70_000 * NAIRA
    assert days[monday.isoformat()]["held_kobo"] == 70_000 * NAIRA
    assert days[tuesday.isoformat()]["banked_kobo"] == 60_000 * NAIRA
    assert days[tuesday.isoformat()]["held_kobo"] == 40_000 * NAIRA
    assert result["held_now_kobo"] == 40_000 * NAIRA
    assert result["unchecked_banking_kobo"] == 60_000 * NAIRA  # nobody has checked it against the bank yet
    staff = {s["name"]: s for s in result["staff"]}
    assert sum(s["received_kobo"] for s in staff.values()) == 100_000 * NAIRA


async def test_banking_is_checked_by_someone_else_and_a_rejected_one_counts_as_still_held(client, world):
    today = lagos_today()
    await generate(client, world)
    await take_cash(client, world, 80_000, day=today)
    deposit = await bank(client, world, 50_000, day=today)
    own = await client.post(f"{DEPOSITS}/{deposit['id']}/confirm", headers=world["bursar"])
    assert own.status_code == 403  # not by the person who recorded it
    confirmed = await client.post(f"{DEPOSITS}/{deposit['id']}/confirm", headers=world["bursar2"])
    assert confirmed.json()["status"] == "confirmed"
    again = await client.post(f"{DEPOSITS}/{deposit['id']}/reject", headers=world["proprietor"], json={"reason": "No"})
    assert again.status_code == 409

    wrong = await bank(client, world, 30_000, day=today)
    vague = await client.post(f"{DEPOSITS}/{wrong['id']}/reject", headers=world["bursar2"], json={"reason": ""})
    assert vague.status_code == 400
    rejected = await client.post(
        f"{DEPOSITS}/{wrong['id']}/reject", headers=world["bursar2"], json={"reason": "Not on the bank statement"}
    )
    assert rejected.json()["status"] == "rejected"
    result = await book(client, world, today, today)
    assert result["held_now_kobo"] == 30_000 * NAIRA and result["unchecked_banking_kobo"] == 0
    listed = (await client.get(DEPOSITS, headers=world["bursar"], params={"status": "rejected"})).json()
    assert [d["rejection_reason"] for d in listed] == ["Not on the bank statement"]


async def test_closing_a_day_shows_a_shortage_and_flags_cash_recorded_afterwards(client, world, db):
    await generate(client, world)
    yesterday = lagos_today() - timedelta(days=1)
    await take_cash(client, world, 40_000, day=yesterday)
    closed = await client.post(
        f"{BOOK}/close",
        headers=world["bursar"],
        json={"day": yesterday.isoformat(), "counted_kobo": 38_000 * NAIRA, "note": "Counted with the principal"},
    )
    assert closed.status_code == 201, closed.text
    assert closed.json()["expected_kobo"] == 40_000 * NAIRA
    twice = await client.post(
        f"{BOOK}/close", headers=world["bursar"], json={"day": yesterday.isoformat(), "counted_kobo": 0}
    )
    assert twice.status_code == 409
    future = (lagos_today() + timedelta(days=1)).isoformat()
    assert (
        await client.post(f"{BOOK}/close", headers=world["bursar"], json={"day": future, "counted_kobo": 0})
    ).status_code == 400

    late = await take_cash(client, world, 5_000, day=yesterday)  # recorded for yesterday after it was closed
    # (the test runs in one transaction, where "now" never moves: make the entry visibly later)
    db.execute(
        text("UPDATE manual_payments SET created_at = now() + interval '5 minutes' WHERE id = :id"), {"id": late["id"]}
    )
    result = await book(client, world, yesterday, lagos_today())
    day = next(d for d in result["days"] if d["day"] == yesterday.isoformat())
    assert day["counted_kobo"] == 38_000 * NAIRA and day["difference_kobo"] == -2_000 * NAIRA
    assert [(e["kind"], e["amount_kobo"]) for e in result["late"]] == [("cash", 5_000 * NAIRA)]
    assert result["last_closed"] == yesterday.isoformat()


async def test_banking_and_closes_are_kept_and_permissions_hold(client, world, db):
    await generate(client, world)
    today = lagos_today()
    await bank(client, world, 1_000, day=today)
    await client.post(f"{BOOK}/close", headers=world["bursar"], json={"day": today.isoformat(), "counted_kobo": 0})
    scope_platform_wide(db)
    for statement in ("DELETE FROM cash_deposits", "UPDATE cash_day_closes SET counted_kobo = 1"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(statement))
        db.rollback()
    # Another school sees nothing of this school's cash
    other = make_school(db, "Beta Academy")
    stranger = headers_for(db, make_user(db, other, Role.BURSAR))
    db.commit()
    theirs = (await client.get(BOOK, headers=stranger)).json()
    assert theirs["held_now_kobo"] == 0 and theirs["days"] == [] and theirs["unchecked_banking_kobo"] == 0
    assert (await client.get(DEPOSITS, headers=stranger)).json() == []


async def test_the_book_is_a_fixed_number_of_queries(client, world, connection):
    await generate(client, world)
    today = lagos_today()
    await take_cash(client, world, 1_000, day=today)
    with QueryCounter(connection) as one:
        await book(client, world, today - timedelta(days=30), today)
    for n in range(4):
        await take_cash(client, world, 1_000, day=today - timedelta(days=n + 1))
        await bank(client, world, 500, day=today - timedelta(days=n + 1))
    with QueryCounter(connection) as many:
        await book(client, world, today - timedelta(days=30), today)
    assert len(many.statements) == len(one.statements)

"""Closing a term: nothing dated in it can be recorded; corrections are dated today; the proprietor re-opens."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import lagos_today
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from tests.factories import headers_for, make_school, make_user
from tests.test_cashbook import bank, take_cash
from tests.test_payments import NAIRA, generate
from tests.test_payments import world as world  # the fixture

BOOKS = "/api/v1/accounting/books"


@pytest.fixture
def ended(world, db):
    """The world's first term, ended yesterday."""
    term = world["terms"][0]
    scope_platform_wide(db)
    db.execute(
        text("UPDATE terms SET end_date = :end WHERE id = :id"),
        {"end": lagos_today() - timedelta(days=1), "id": term.id},
    )
    db.commit()
    return world


async def close(client, world, headers=None):
    return await client.post(
        f"/api/v1/accounting/terms/{world['terms'][0].id}/close", headers=headers or world["bursar"]
    )


async def test_a_term_closes_only_once_ended_and_with_nothing_waiting(client, world, ended, db):
    in_term = lagos_today() - timedelta(days=2)
    await generate(client, world)
    waiting = await take_cash(client, world, 10_000, day=in_term, approve=False)
    refused = await close(client, world)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "books_waiting"
    assert "1 cash or teller payments" in refused.json()["error"]["message"]
    await client.post(f"/api/v1/manual-payments/{waiting['id']}/approve", headers=world["bursar"])

    closed = await close(client, world)
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["closed_through_after"] == str(lagos_today() - timedelta(days=1))
    assert body["figures"]["collected_kobo"] == 10_000 * NAIRA and body["figures"]["invoices"] == 1
    assert (await close(client, world)).status_code == 409  # already closed
    books = (await client.get(BOOKS, headers=world["proprietor"])).json()
    assert books["closed_through"] == body["closed_through_after"] and len(books["history"]) == 1

    scope_platform_wide(db)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM book_closings"))
    db.rollback()


async def test_nothing_dated_in_a_closed_term_can_be_recorded(client, world, ended):
    in_term, today = lagos_today() - timedelta(days=2), lagos_today()
    await generate(client, world)
    assert (await close(client, world)).status_code == 200

    def refused(response, field=None):
        error = response.json()["error"]
        assert response.status_code == 400 and error["code"] == "books_closed", response.text
        if field:
            assert error["field"] == field

    recorded = await client.post(
        "/api/v1/manual-payments",
        headers=world["admin"],
        json={
            "student_id": str(world["student"].id),
            "amount_kobo": 1_000 * NAIRA,
            "method": "cash",
            "received_on": in_term.isoformat(),
        },
    )
    refused(recorded, "received_on")
    await take_cash(client, world, 1_000, day=today)  # dated today: fine
    categories = (await client.get("/api/v1/expense-categories", headers=world["bursar"])).json()
    spent = await client.post(
        "/api/v1/expenses",
        headers=world["bursar"],
        json={
            "category_id": categories[0]["id"],
            "amount_kobo": 500 * NAIRA,
            "spent_on": in_term.isoformat(),
            "payee": "Total filling station",
            "paid_from": "cash",
        },
    )
    refused(spent, "spent_on")
    banked = await client.post(
        "/api/v1/cash-deposits",
        headers=world["bursar"],
        json={"amount_kobo": 500 * NAIRA, "banked_on": in_term.isoformat(), "bank_reference": "TLR-1"},
    )
    refused(banked, "banked_on")
    counted = await client.post(
        "/api/v1/cash-book/close", headers=world["bursar"], json={"day": in_term.isoformat(), "counted_kobo": 0}
    )
    refused(counted, "day")

    # The closed term's bills: no billing again, no voids; adjustments are fine (dated today)
    term = world["terms"][0]
    again = await client.post(
        f"/api/v1/terms/{term.id}/invoices/generate", headers=world["bursar"], json={"dry_run": False}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "books_closed"
    account = (await client.get(f"/api/v1/students/{world['student'].id}/account", headers=world["bursar"])).json()
    url = f"/api/v1/invoices/{account['invoices'][0]['id']}"
    assert (await client.post(f"{url}/void", headers=world["bursar"], json={"reason": "Wrong"})).status_code == 409
    adjusted = await client.post(
        f"{url}/lines",
        headers=world["bursar"],
        json={"kind": "adjustment", "description": "PTA waived", "amount_kobo": -5_000 * NAIRA, "reason": "Board"},
    )
    assert adjusted.status_code == 201, adjusted.text
    moved = await client.patch(
        f"/api/v1/terms/{term.id}",
        headers=world["proprietor"],
        json={"start_date": str(term.start_date), "end_date": str(today + timedelta(days=30))},
    )
    assert moved.status_code == 409


async def test_only_the_proprietor_reopens_with_a_reason(client, world, ended, db):
    in_term = lagos_today() - timedelta(days=2)
    await bank(client, world, 1, day=in_term)  # pending banking blocks the close until checked
    deposits = (await client.get("/api/v1/cash-deposits", headers=world["bursar"])).json()
    await client.post(
        f"/api/v1/cash-deposits/{deposits[0]['id']}/reject",
        headers=world["bursar2"],
        json={"reason": "Not on statement"},
    )
    assert (await close(client, world)).status_code == 200
    reopen = "/api/v1/accounting/books/reopen"
    assert (await client.post(reopen, headers=world["bursar"], json={"reason": "Mistake"})).status_code == 403
    assert (await client.post(reopen, headers=world["proprietor"], json={"reason": ""})).status_code == 422
    reopened = await client.post(reopen, headers=world["proprietor"], json={"reason": "Teller found late"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["closed_through_after"] is None  # no earlier term
    assert (await client.get(BOOKS, headers=world["bursar"])).json()["closed_through"] is None
    await take_cash(client, world, 1_000, day=in_term)  # open again
    assert (await client.post(reopen, headers=world["proprietor"], json={"reason": "Again"})).status_code == 409

    stranger = headers_for(db, make_user(db, make_school(db, "Beta Academy"), Role.PROPRIETOR))
    db.commit()
    assert (await close(client, world, stranger)).status_code == 404
    assert (await client.get(BOOKS, headers=stranger)).json()["history"] == []

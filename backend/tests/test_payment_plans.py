"""Payment plans: paying a bill in instalments, and how lateness, reminders and the portal follow the plan."""

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.pagination import PageParams
from app.core.tenancy import scope_to_school
from app.modules.accounts.models import Role
from app.modules.fees.plans import lagos_today
from app.modules.notifications import service as notifications
from app.modules.notifications import whatsapp
from app.modules.notifications.service import LAGOS
from app.modules.reports import service as reports
from app.modules.reports.service import AgingBucket, DebtorFilters
from tests.factories import headers_for, make_calendar, make_levels, make_school, make_student, make_user
from tests.test_fees import add_fees, generate
from tests.test_parents import bearer, link_guardian, sign_in
from tests.test_payments import connect_gateway, pay_cash

NAIRA = 100
OWED = 155_000 * NAIRA  # JSS 1 tuition + PTA in add_fees


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    monkeypatch.setattr(get_settings(), "quiet_hours_start", 0)
    monkeypatch.setattr(get_settings(), "quiet_hours_end", 0)


@pytest.fixture
async def world(db, client):
    school = make_school(db, "Alpha College")
    bursar, admin = make_user(db, school, Role.BURSAR), make_user(db, school, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(school)
    levels = make_levels(school)
    add_fees(school, terms[0], levels)
    student = make_student(school, terms[0], levels[0].arms[0], first="Tolu", last="Adeyemi")
    link_guardian(school, [student])
    connect_gateway(school)
    db.commit()
    w = {
        "school": school,
        "terms": terms,
        "student": student,
        "bursar": headers_for(db, bursar),
        "admin": headers_for(db, admin),
    }
    # The bill was due ten days ago: without a plan, it's overdue
    await generate(client, w, due_date=str(lagos_today() - timedelta(days=10)))
    account = (await client.get(f"/api/v1/students/{student.id}/account", headers=w["bursar"])).json()
    w["invoice_id"] = account["invoices"][0]["id"]
    return w


def schedule(*parts: tuple[int, int]) -> list[dict[str, object]]:
    """(days from today, naira) pairs."""
    return [
        {"due_date": (lagos_today() + timedelta(days=d)).isoformat(), "amount_kobo": naira * NAIRA}
        for d, naira in parts
    ]


async def make_plan(client, world, instalments, note=""):
    return await client.post(
        f"/api/v1/invoices/{world['invoice_id']}/plan",
        headers=world["bursar"],
        json={"instalments": instalments, "note": note},
    )


async def test_plan_must_cover_exactly_whats_owed_in_order(client, world):
    for bad, why in (
        (schedule((5, 155_000)), "one instalment"),
        (schedule((5, 100_000), (35, 50_000)), "doesn't add up"),
        (schedule((35, 55_000), (5, 100_000)), "out of order"),
        (schedule((-1, 55_000), (30, 100_000)), "in the past"),
    ):
        response = await make_plan(client, world, bad)
        assert response.status_code in (400, 422), why
    ok = await make_plan(
        client, world, schedule((0, 55_000), (30, 50_000), (60, 50_000)), note="Agreed with Mrs Adeyemi"
    )
    assert ok.status_code == 201, ok.text
    plan = ok.json()["plan"]
    assert plan["covered_kobo"] == OWED and plan["note"] == "Agreed with Mrs Adeyemi"
    assert [i["status"] for i in plan["instalments"]] == ["due_today", "upcoming", "upcoming"]
    assert (plan["next_number"], plan["next_amount_kobo"], plan["behind_kobo"]) == (1, 55_000 * NAIRA, 55_000 * NAIRA)
    again = await make_plan(client, world, schedule((1, 55_000), (30, 100_000)))
    assert again.status_code == 409  # one live plan per bill


async def test_payments_cover_instalments_in_order(client, world):
    await make_plan(client, world, schedule((0, 55_000), (30, 50_000), (60, 50_000)))
    await pay_cash(client, world, 70_000)
    plan = (await client.get(f"/api/v1/invoices/{world['invoice_id']}", headers=world["bursar"])).json()["plan"]
    assert [(i["status"], i["paid_kobo"]) for i in plan["instalments"]] == [
        ("paid", 55_000 * NAIRA),
        ("part_paid", 15_000 * NAIRA),
        ("upcoming", 0),
    ]
    assert plan["next_number"] == 2 and plan["next_amount_kobo"] == 35_000 * NAIRA and plan["behind_kobo"] == 0


async def test_cancel_and_void_end_the_plan(client, world):
    await make_plan(client, world, schedule((0, 55_000), (30, 100_000)))
    cancelled = await client.post(
        f"/api/v1/invoices/{world['invoice_id']}/plan/cancel", headers=world["bursar"], json={"reason": "New terms"}
    )
    assert cancelled.json()["plan"] is None
    assert (await make_plan(client, world, schedule((7, 77_500), (37, 77_500)))).status_code == 201
    voided = await client.post(
        f"/api/v1/invoices/{world['invoice_id']}/void", headers=world["bursar"], json={"reason": "Wrong class"}
    )
    assert voided.status_code == 200 and voided.json()["plan"] is None


async def test_instalments_cant_be_edited(client, world, db):
    await make_plan(client, world, schedule((0, 55_000), (30, 100_000)))
    for statement in ("UPDATE plan_instalments SET amount_kobo = 1", "DELETE FROM plan_instalments"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(statement))
        db.rollback()


async def test_only_billing_staff_and_only_their_school(client, world, db):
    plan = {"instalments": schedule((0, 55_000), (30, 100_000))}
    url = f"/api/v1/invoices/{world['invoice_id']}/plan"
    assert (await client.post(url, headers=world["admin"], json=plan)).status_code == 403  # admins don't bill
    other = make_school(db, "Beta Academy")
    theirs = headers_for(db, make_user(db, other, Role.BURSAR))
    db.commit()
    response = await client.post(
        f"/api/v1/invoices/{world['invoice_id']}/plan",
        headers=theirs,
        json={"instalments": schedule((0, 55_000), (30, 100_000))},
    )
    assert response.status_code == 404


# --- Lateness follows the plan ---------------------------------------------------------------------


async def test_a_parent_keeping_to_the_plan_isnt_overdue(client, world, db):
    session = db
    scope_to_school(session, world["school"].id)
    overdue_before = reports.debtors(session, DebtorFilters(overdue_only=True), PageParams(limit=50, offset=0))
    assert overdue_before.total == 1  # the bill was due ten days ago

    await make_plan(client, world, schedule((5, 55_000), (35, 100_000)))
    page = reports.debtors(session, DebtorFilters(), PageParams(limit=50, offset=0))
    [debtor] = page.items
    assert debtor.on_plan is True and debtor.days_overdue == 0
    assert debtor.oldest_due == lagos_today() + timedelta(days=5)  # the next instalment
    assert reports.debtors(session, DebtorFilters(overdue_only=True), PageParams(limit=50, offset=0)).total == 0
    aging = {a.bucket: a.amount_kobo for a in reports.overview(session, None).aging}
    assert aging[AgingBucket.NOT_DUE] == OWED

    # Three weeks on, the first instalment was missed: overdue again, from that date
    later = lagos_today() + timedelta(days=21)
    late = reports.debtors(session, DebtorFilters(overdue_only=True), PageParams(limit=50, offset=0), later)
    assert late.total == 1 and late.items[0].days_overdue == 16


async def test_reminders_follow_instalments_and_ask_for_the_instalment(client, world, db):
    await make_plan(client, world, schedule((7, 55_000), (37, 100_000)))
    first_due = lagos_today() + timedelta(days=7)

    def at(day: date) -> datetime:
        return datetime.combine(day, time(9), tzinfo=LAGOS)

    # The bill's own due date (ten days ago + 3 = a reminder day) no longer drives reminders
    assert notifications.schedule_reminders(at(lagos_today() - timedelta(days=7))) == 0
    assert notifications.schedule_reminders(at(first_due - timedelta(days=7))) == 1
    [sent] = whatsapp.sent_whatsapp
    balance = sent.parameters[2]  # reminder template: parent, school, balance, ...
    assert balance == "₦55,000.00"  # the instalment, not the whole ₦155,000
    scope_to_school(db, world["school"].id)
    body = db.execute(text("SELECT body FROM message_log WHERE kind = 'reminder'")).scalar()
    assert "instalment 1 of 2" in body and "₦55,000.00" in body


async def test_parent_sees_the_schedule(client, world):
    await make_plan(client, world, schedule((7, 55_000), (37, 100_000)))
    parent = bearer(await sign_in(client))
    account = (await client.get(f"/api/v1/parent/children/{world['student'].id}", headers=parent)).json()
    [bill] = account["invoices"]
    assert bill["plan"]["next_amount_kobo"] == 55_000 * NAIRA
    assert [i["amount_kobo"] for i in bill["plan"]["instalments"]] == [55_000 * NAIRA, 100_000 * NAIRA]
    detail = (await client.get(f"/api/v1/parent/invoices/{world['invoice_id']}", headers=parent)).json()
    assert detail["plan"]["next_number"] == 1

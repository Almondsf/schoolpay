"""The collection curve against last term, and the cash forecast for the next 30/60/90 days."""

from datetime import date, timedelta

from app.core.clock import lagos_today
from app.modules.accounts.models import Role
from tests.factories import QueryCounter, headers_for, make_calendar, make_school, make_user
from tests.test_payment_plans import OWED, make_plan, schedule
from tests.test_payment_plans import _daytime as _daytime  # the fixture
from tests.test_payment_plans import world as world  # the fixture

NAIRA = 100
INSIGHTS = "/api/v1/reports/insights"


async def take(client, world, naira, day):
    recorded = await client.post(
        "/api/v1/manual-payments",
        headers=world["admin"],
        json={
            "student_id": str(world["student"].id),
            "amount_kobo": naira * NAIRA,
            "method": "cash",
            "received_on": day.isoformat(),
        },
    )
    assert recorded.status_code == 201, recorded.text
    approved = await client.post(f"/api/v1/manual-payments/{recorded.json()['id']}/approve", headers=world["bursar"])
    assert approved.status_code == 200, approved.text


async def get(client, world):
    response = await client.get(INSIGHTS, headers=world["bursar"])
    assert response.status_code == 200, response.text
    return response.json()


async def test_curve_counts_collections_by_day_since_the_term_began(client, world):
    make_calendar(world["school"], 2025, current_term=0)  # last session: its third term is "last term"
    term = world["terms"][0]
    today = lagos_today()
    await take(client, world, 20_000, term.start_date - timedelta(days=3))  # before the term: day 0
    await take(client, world, 30_000, today)
    result = await get(client, world)
    current = result["current"]
    assert current["label"] == "2026/2027 First term" and current["billed_kobo"] == OWED
    assert current["days"] == max((min(today, term.end_date) - term.start_date).days, 0)
    curve = current["collected_kobo"]
    assert len(curve) == current["days"] + 1 and curve[0] == 20_000 * NAIRA
    assert curve[-1] == 50_000 * NAIRA and curve == sorted(curve)
    previous = result["previous"]
    assert previous["label"] == "2025/2026 Third term" and previous["start"] == str(date(2026, 4, 20))
    assert previous["billed_kobo"] == 0 and set(previous["collected_kobo"]) == {0}


async def test_forecast_counts_only_the_plan_instalments_that_fall_due(client, world):
    before = await get(client, world)
    assert before["overdue_kobo"] == OWED  # the bill was due ten days ago
    assert [w["due_kobo"] for w in before["windows"]] == [0, 0, 0]

    assert (await make_plan(client, world, schedule((5, 100_000), (40, 55_000)))).status_code == 201
    planned = await get(client, world)
    assert planned["overdue_kobo"] == 0
    assert [(w["days"], w["due_kobo"]) for w in planned["windows"]] == [
        (30, 100_000 * NAIRA),
        (60, OWED),
        (90, OWED),
    ]

    await take(client, world, 30_000, lagos_today())
    paid = await get(client, world)
    assert [w["due_kobo"] for w in paid["windows"]] == [70_000 * NAIRA, 125_000 * NAIRA, 125_000 * NAIRA]
    # At the recent pace: ₦30,000 over 30 days is ₦1,000 a day
    assert [w["pace_in_kobo"] for w in paid["windows"]] == [30_000 * NAIRA, 60_000 * NAIRA, 90_000 * NAIRA]
    assert paid["windows"][0]["pace_out_kobo"] == 0


async def test_no_current_term_and_other_schools(client, world, db, connection):
    stranger_school = make_school(db, "Beta Academy")
    stranger = headers_for(db, make_user(db, stranger_school, Role.BURSAR))
    db.commit()
    empty = (await client.get(INSIGHTS, headers=stranger)).json()
    assert empty["current"] is None and empty["previous"] is None and empty["overdue_kobo"] == 0
    with QueryCounter(connection) as one:
        await client.get(INSIGHTS, headers=world["bursar"])
    await make_plan(client, world, schedule((5, 100_000), (40, 55_000)))
    for _ in range(3):
        await take(client, world, 1_000, lagos_today())
    with QueryCounter(connection) as many:
        await client.get(INSIGHTS, headers=world["bursar"])
    assert len(many.statements) == len(one.statements)

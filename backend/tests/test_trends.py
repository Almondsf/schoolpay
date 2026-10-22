"""The platform's trends: month by month, a fixed number of queries."""

from datetime import date

from app.core.clock import lagos_today
from app.modules.platform.trends import _months_back
from tests.factories import QueryCounter
from tests.test_platform_billing import generate, invoices
from tests.test_platform_billing import world as world  # the fixture

TRENDS = "/api/v1/platform/trends"


def test_months_back_cross_the_new_year():
    assert _months_back(date(2026, 2, 14), 4) == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]


async def test_this_month_counts_joins_suspensions_and_subscription_money(client, world, db, connection):
    await generate(client, world)
    [bill] = await invoices(client, world, school_id=str(world["alpha"].id))
    today = lagos_today().isoformat()
    await client.post(
        f"/api/v1/platform/billing/invoices/{bill['id']}/payments",
        headers=world["admin"],
        json={"amount_kobo": 20_000_00, "received_on": today},
    )
    suspended = await client.post(
        f"/api/v1/platform/schools/{world['beta'].id}/suspend",
        headers=world["admin"],
        json={"reason": "Asked to pause"},
    )
    assert suspended.status_code == 200, suspended.text

    with QueryCounter(connection) as twelve:
        months = (await client.get(TRENDS, headers=world["admin"])).json()
    assert len(months) == 12 and months[-1]["month"] == today[:7]
    now = months[-1]
    assert now["subscription_received_kobo"] == 20_000_00
    assert now["schools_joined"] >= 3 and now["schools_suspended"] == 1
    assert all(m["schools_joined"] == 0 for m in months[:-1])  # the test schools were all made today
    with QueryCounter(connection) as thirty_six:
        assert len((await client.get(TRENDS, headers=world["admin"], params={"months": 36})).json()) == 36
    assert len(thirty_six.statements) == len(twelve.statements)
    assert (await client.get(TRENDS, headers=world["admin"], params={"months": 37})).status_code == 422
    assert (await client.get(TRENDS, headers=world["alpha_bursar"])).status_code == 403

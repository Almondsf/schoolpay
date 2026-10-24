"""Settlement reconciliation: the provider's payouts matched to payments, and what's missing either way."""

from datetime import timedelta

from sqlalchemy import func, select, text

from app.core.clock import lagos_today
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from app.modules.payments import settlements
from app.modules.payments.gateways import FakeGateway
from app.modules.payments.gateways.base import SettledTransaction
from app.modules.payments.models import SettlementItem
from tests.factories import QueryCounter, headers_for, make_school, make_user
from tests.test_payments import NAIRA, generate, provision
from tests.test_payments import world as world  # the fixture

URL = "/api/v1/settlements"
SUBACCOUNT = "FAKE_SUB_1"  # connect_gateway in test_payments


async def transfers(client, world, *amounts):
    """Parents pay into the pupil's account number; returns the gateway references, oldest first."""
    va = await provision(client, world)
    fake = FakeGateway.shared()
    references = []
    for naira in amounts:
        headers, body = fake.simulate_transfer(va["account_number"], naira * NAIRA)
        assert (await client.post("/api/v1/webhooks/fake", headers=headers, content=body)).status_code == 200
        references.append(list(fake.state.transactions)[-1])
    return references


def arrived_days_ago(db, days):
    scope_platform_wide(db)
    db.execute(text("SET LOCAL session_replication_role = replica"))  # payments are append-only
    db.execute(text("UPDATE payments SET received_at = received_at - make_interval(days => :d)"), {"d": days})
    db.execute(text("SET LOCAL session_replication_role = origin"))
    db.commit()


async def refresh(client, world):
    response = await client.post(f"{URL}/refresh", headers=world["bursar"])
    assert response.status_code == 202, response.text


async def report(client, world, headers=None):
    start = (lagos_today() - timedelta(days=30)).isoformat()
    response = await client.get(URL, headers=headers or world["bursar"], params={"start": start})
    assert response.status_code == 200, response.text
    return response.json()


async def test_payouts_are_matched_and_gaps_show(client, world, db):
    await generate(client, world)
    first, second = await transfers(client, world, 60_000, 40_000)
    arrived_days_ago(db, 5)
    fake = FakeGateway.shared()
    stranger_money = SettledTransaction("NOT_OURS_1", 5_000 * NAIRA, 0)
    fake.settle(SUBACCOUNT, [first], on=lagos_today() - timedelta(days=4), extra=[stranger_money])
    await refresh(client, world)

    result = await report(client, world)
    assert result["connected"] is True and result["checked_at"] is not None
    [payout] = result["payouts"]
    assert payout["gross_kobo"] == 65_000 * NAIRA
    assert payout["charges_kobo"] == 900 * NAIRA  # 1.5% of the ₦60,000 the fake charges on
    assert payout["paid_out_kobo"] == 65_000 * NAIRA - 900 * NAIRA
    assert (payout["transactions"], payout["matched"]) == (2, 1)
    assert [u["reference"] for u in result["unsettled"]] == [second]
    assert result["unsettled_kobo"] == 40_000 * NAIRA
    assert [(u["reference"], u["amount_kobo"]) for u in result["unknown"]] == [("NOT_OURS_1", 5_000 * NAIRA)]

    # The next payout covers the rest; fetching again doesn't duplicate anything
    fake.settle(SUBACCOUNT, [second], on=lagos_today() - timedelta(days=3))
    await refresh(client, world)
    await refresh(client, world)
    after = await report(client, world)
    assert len(after["payouts"]) == 2 and after["unsettled"] == [] and after["unsettled_kobo"] == 0
    assert after["paid_out_kobo"] == after["gross_kobo"] - after["charges_kobo"]
    scope_platform_wide(db)
    assert db.scalar(select(func.count()).select_from(SettlementItem)) == 3


async def test_a_pending_payout_is_updated_when_paid(client, world):
    await generate(client, world)
    [reference] = await transfers(client, world, 10_000)
    fake = FakeGateway.shared()
    fake.settle(SUBACCOUNT, [reference], on=lagos_today(), status="pending")
    await refresh(client, world)
    assert (await report(client, world))["payouts"][0]["status"] == "pending"
    payout, items = fake.state.settlements[SUBACCOUNT][0]
    fake.state.settlements[SUBACCOUNT][0] = (
        type(payout)(
            payout.id, "success", payout.settled_on, payout.gross_kobo, payout.charges_kobo, payout.paid_out_kobo
        ),
        items,
    )
    await refresh(client, world)
    [updated] = (await report(client, world))["payouts"]
    assert updated["status"] == "success" and updated["matched"] == 1


async def test_permissions_other_schools_and_fixed_queries(client, world, db, connection):
    stranger = headers_for(db, make_user(db, make_school(db, "Beta Academy"), Role.BURSAR))
    db.commit()
    assert (await report(client, world, stranger))["connected"] is False
    await generate(client, world)
    references = await transfers(client, world, 1_000, 2_000, 3_000, 4_000)
    fake = FakeGateway.shared()
    fake.settle(SUBACCOUNT, references[:1], on=lagos_today() - timedelta(days=2))
    await refresh(client, world)
    assert (await report(client, world, stranger))["payouts"] == []  # Beta sees none of Alpha's
    with QueryCounter(connection) as one:
        await report(client, world)
    for i, reference in enumerate(references[1:], start=1):
        fake.settle(SUBACCOUNT, [reference], on=lagos_today() - timedelta(days=2 - i % 2))
    await refresh(client, world)
    with QueryCounter(connection) as many:
        await report(client, world)
    assert len(many.statements) == len(one.statements)


def test_the_daily_job_covers_active_schools_on_a_reporting_provider(world, db):
    scope_platform_wide(db)
    unconnected = make_school(db, "Gamma School")
    db.commit()
    chosen = settlements.schools_to_sync(db)
    assert world["school"].id in chosen and unconnected.id not in chosen

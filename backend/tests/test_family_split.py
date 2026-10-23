"""One amount for the whole family, shared between the children by the rule the school chose."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.core.tenancy import scope_to_school
from app.modules.accounts.models import Role
from app.modules.audit.models import AuditAction, AuditLog
from app.modules.payments.family_split import Bill, ChildDebt, split
from app.modules.payments.gateways import FakeGateway
from app.modules.payments.models import Payment
from app.modules.schools.models import FamilySplitRule
from tests.factories import headers_for, make_user
from tests.test_family_link import NAIRA, owed, page, staff_link
from tests.test_family_link import world as world  # the fixture
from tests.test_fees import generate

OLD, NEW = datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 9, 15, tzinfo=UTC)
ADA, TUNDE, KEMI = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)


def family() -> list[ChildDebt]:
    """Ada: 40k from last term + 120k now (born 2014). Tunde: 90k now (born 2016). Kemi: 30k now, no birth date."""
    return [
        ChildDebt(ADA, date(2014, 3, 1), 8, [Bill(OLD, 40_000, False), Bill(NEW, 120_000, True)]),
        ChildDebt(TUNDE, date(2016, 6, 1), 6, [Bill(NEW, 90_000, True)]),
        ChildDebt(KEMI, None, 10, [Bill(NEW, 30_000, True)]),
    ]


def test_clear_most_pays_old_debts_then_the_smallest_balances():
    shares = split(150_000, family(), FamilySplitRule.CLEAR_MOST)
    # Ada's 40k arrears, then Kemi's 30k (smallest, cleared), then Tunde's 90k... 80k left of it
    assert shares == {ADA: 40_000, KEMI: 30_000, TUNDE: 80_000}
    assert split(160_000, family(), FamilySplitRule.CLEAR_MOST) == {ADA: 40_000, KEMI: 30_000, TUNDE: 90_000}


def test_proportional_is_exact_to_the_kobo():
    shares = split(100_001, family(), FamilySplitRule.PROPORTIONAL)
    assert sum(shares.values()) == 100_001
    owed = {c.student_id: c.owed_kobo for c in family()}  # 160k, 90k, 30k of 280k
    assert all(abs(shares[s] - 100_001 * owed[s] / 280_000) < 1 for s in shares)


def test_eldest_and_youngest_first_use_birth_dates_then_class():
    # Kemi has no birth date, so she counts by class after those with dates; Ada is the eldest by date
    assert split(170_000, family(), FamilySplitRule.ELDEST_FIRST) == {ADA: 160_000, TUNDE: 10_000, KEMI: 0}
    assert split(100_000, family(), FamilySplitRule.YOUNGEST_FIRST) == {KEMI: 30_000, TUNDE: 70_000, ADA: 0}


@pytest.mark.parametrize("rule", list(FamilySplitRule))
def test_every_rule_shares_exactly_and_never_more_than_owed(rule):
    for amount in (1, 29_999, 150_000, 280_000, 999_999):
        shares = split(amount, family(), rule)
        owed = {c.student_id: c.owed_kobo for c in family()}
        assert sum(shares.values()) == min(amount, 280_000)
        assert all(0 <= shares[s] <= owed[s] for s in shares)


async def test_one_tap_pays_everything_owed_with_a_payment_per_child(client, world, db):
    await generate(client, world)
    token = await staff_link(client, world)
    view = (await page(client, token)).json()
    assert view["split_rule"] == "clear_most"
    total = sum(c["owed_kobo"] for c in view["children"])
    started = await client.post(f"/api/v1/public/pay/{token}/checkout", json={"total_kobo": total})
    assert started.status_code == 201, started.text
    FakeGateway.shared().complete_checkout(started.json()["reference"])
    await client.post("/api/v1/payments/verify", json={"reference": started.json()["reference"]})
    assert await owed(client, world, "ada") == 0 and await owed(client, world, "bayo") == 0
    scope_to_school(db, world["school"].id)
    assert len(list(db.scalars(select(Payment)))) == 2  # one per child, each with its own receipt


async def test_a_partial_amount_follows_the_schools_rule(client, world, db):
    """Ada owes 155k, Bayo 185k. 200k: clear_most clears Ada (the smaller balance), proportional shares it."""
    await generate(client, world)
    token = await staff_link(client, world)
    preview = (await client.post(f"/api/v1/public/pay/{token}/split", json={"total_kobo": 200_000 * NAIRA})).json()
    shares = {s["student_id"]: (s["amount_kobo"], s["clears"]) for s in preview["shares"]}
    assert shares[str(world["ada"].id)] == (155_000 * NAIRA, True)
    assert shares[str(world["bayo"].id)] == (45_000 * NAIRA, False)

    proprietor = headers_for(db, make_user(db, world["school"], Role.PROPRIETOR))
    db.commit()
    changed = await client.put(
        "/api/v1/payment-settings/family-split-rule", headers=proprietor, json={"rule": "proportional"}
    )
    assert changed.json() == {"rule": "proportional"}
    preview = (await client.post(f"/api/v1/public/pay/{token}/split", json={"total_kobo": 200_000 * NAIRA})).json()
    amounts = {s["student_id"]: s["amount_kobo"] for s in preview["shares"]}
    assert sum(amounts.values()) == 200_000 * NAIRA
    assert 0 < amounts[str(world["ada"].id)] < amounts[str(world["bayo"].id)]
    scope_to_school(db, world["school"].id)
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == AuditAction.FAMILY_SPLIT_RULE_CHANGED.value))


async def test_amounts_and_who_may_change_the_rule_are_checked(client, world):
    await generate(client, world)
    token = await staff_link(client, world)
    too_much = await client.post(f"/api/v1/public/pay/{token}/checkout", json={"total_kobo": 1_000_000 * NAIRA})
    assert too_much.status_code == 400 and too_much.json()["error"]["field"] == "total_kobo"
    tiny = await client.post(f"/api/v1/public/pay/{token}/checkout", json={"total_kobo": 500 * NAIRA})
    assert tiny.status_code == 400
    both = await client.post(
        f"/api/v1/public/pay/{token}/checkout",
        json={"total_kobo": 100_000, "items": [{"student_id": str(world["ada"].id), "amount_kobo": 100_000}]},
    )
    assert both.status_code == 422
    # Only the proprietor chooses the rule; the bursar can read it
    assert (
        await client.put(
            "/api/v1/payment-settings/family-split-rule", headers=world["bursar"], json={"rule": "eldest_first"}
        )
    ).status_code == 403
    assert (await client.get("/api/v1/payment-settings/family-split-rule", headers=world["bursar"])).json() == {
        "rule": "clear_most"
    }

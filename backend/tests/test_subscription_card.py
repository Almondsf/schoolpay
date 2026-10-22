"""Schools paying their subscription bill by card: to the platform's own balance, recorded exactly once."""

from sqlalchemy import select

from app.core.tenancy import scope_platform_wide
from app.modules.audit.models import AuditAction, AuditLog
from app.modules.payments import webhooks
from app.modules.payments.gateways import FakeGateway, GatewayCode
from app.modules.payments.models import WebhookEvent, WebhookStatus
from app.modules.platform.models import SubscriptionPayment
from tests.test_platform_billing import NAIRA, generate, world  # noqa: F401 (world is a fixture)

VERIFY = "/api/v1/subscription/checkouts/verify"


async def alpha_bill(client, world):  # noqa: F811
    await generate(client, world)
    [bill] = (await client.get("/api/v1/subscription", headers=world["alpha_bursar"])).json()["invoices"]
    return bill


async def start(client, headers, bill):
    return await client.post(f"/api/v1/subscription/invoices/{bill['id']}/checkout", headers=headers)


async def test_a_bursar_pays_the_whole_bill_by_card_to_the_platforms_own_balance(client, world, db):  # noqa: F811
    bill = await alpha_bill(client, world)
    started = await start(client, world["alpha_bursar"], bill)
    assert started.status_code == 201, started.text
    reference = started.json()["reference"]
    assert reference.startswith("WS") and started.json()["amount_kobo"] == bill["balance_kobo"]
    assert started.json()["authorization_url"].endswith(reference)

    assert (await client.post(VERIFY, headers=world["alpha_bursar"], json={"reference": reference})).json()[
        "status"
    ] == "pending"
    FakeGateway.shared().complete_checkout(reference)
    done = await client.post(VERIFY, headers=world["alpha_bursar"], json={"reference": reference})
    assert done.json()["status"] == "succeeded"
    again = await client.post(VERIFY, headers=world["alpha_bursar"], json={"reference": reference})
    assert again.json()["status"] == "succeeded"

    [paid] = (await client.get("/api/v1/subscription", headers=world["alpha_bursar"])).json()["invoices"]
    assert paid["status"] == "paid" and paid["balance_kobo"] == 0
    detail = (await client.get(f"/api/v1/platform/billing/invoices/{bill['id']}", headers=world["admin"])).json()
    [payment] = detail["payments"]
    assert payment["gateway"] == "fake" and payment["reference"] == reference
    scope_platform_wide(db)
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.target_id.in_([bill["id"]]))))
    assert AuditAction.SUBSCRIPTION_PAYMENT_RECORDED.value in actions
    # A paid bill can't be paid again
    assert (await start(client, world["alpha_bursar"], paid)).status_code == 409


async def test_the_webhook_records_it_once_alongside_the_redirect(client, world, db):  # noqa: F811
    bill = await alpha_bill(client, world)
    reference = (await start(client, world["alpha_bursar"], bill)).json()["reference"]
    headers, body = FakeGateway.shared().complete_checkout(reference)
    await client.post(VERIFY, headers=world["alpha_bursar"], json={"reference": reference})
    assert (await client.post("/api/v1/webhooks/fake", headers=headers, content=body)).status_code == 200
    scope_platform_wide(db)
    event_id = db.scalar(select(WebhookEvent.id).where(WebhookEvent.reference == reference))
    db.rollback()
    assert webhooks.process_event(event_id) == WebhookStatus.PROCESSED
    assert db.scalar(select(WebhookEvent.school_id).where(WebhookEvent.id == event_id)) == world["alpha"].id
    payments = list(db.scalars(select(SubscriptionPayment).where(SubscriptionPayment.gateway_reference == reference)))
    assert len(payments) == 1 and payments[0].recorded_by is None and payments[0].gateway == GatewayCode.FAKE


async def test_a_short_or_too_late_payment_is_parked_for_review(client, world):  # noqa: F811
    bill = await alpha_bill(client, world)
    short = (await start(client, world["alpha_bursar"], bill)).json()["reference"]
    FakeGateway.shared().complete_checkout(short, amount_kobo=100)
    result = (await client.post(VERIFY, headers=world["alpha_bursar"], json={"reference": short})).json()
    assert result["status"] == "needs_review" and "expected" in result["review_reason"]

    # Two checkouts open; the platform records a transfer meanwhile; the second card payment can't fit
    late = (await start(client, world["alpha_bursar"], bill)).json()["reference"]
    await client.post(
        f"/api/v1/platform/billing/invoices/{bill['id']}/payments",
        headers=world["admin"],
        json={"amount_kobo": bill["total_kobo"], "received_on": "2026-10-01"},
    )
    FakeGateway.shared().complete_checkout(late)
    result = (await client.post(VERIFY, headers=world["alpha_bursar"], json={"reference": late})).json()
    assert result["status"] == "needs_review" and "left on the bill" in result["review_reason"]
    detail = (await client.get(f"/api/v1/platform/billing/invoices/{bill['id']}", headers=world["admin"])).json()
    assert len(detail["payments"]) == 1  # only the transfer


async def test_failed_card_payment_records_nothing(client, world):  # noqa: F811
    bill = await alpha_bill(client, world)
    reference = (await start(client, world["alpha_bursar"], bill)).json()["reference"]
    FakeGateway.shared().complete_checkout(reference, succeed=False)
    result = await client.post(VERIFY, headers=world["alpha_bursar"], json={"reference": reference})
    assert result.json()["status"] == "failed"
    [unpaid] = (await client.get("/api/v1/subscription", headers=world["alpha_bursar"])).json()["invoices"]
    assert unpaid["status"] == "unpaid"


async def test_staff_pay_only_their_own_schools_bill(client, world):  # noqa: F811
    bill = await alpha_bill(client, world)
    # Beta's bursar can neither pay nor check Alpha's bill
    assert (await start(client, world["beta_bursar"], bill)).status_code == 404
    reference = (await start(client, world["alpha_bursar"], bill)).json()["reference"]
    assert (await client.post(VERIFY, headers=world["beta_bursar"], json={"reference": reference})).status_code == 404
    # A platform admin has no school to pay for, and a school-fee reference is refused outright
    assert (await start(client, world["admin"], bill)).status_code == 403
    bad = await client.post(VERIFY, headers=world["alpha_bursar"], json={"reference": "SP" + "0" * 20})
    assert bad.status_code == 422

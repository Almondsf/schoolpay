"""The platform's Problems page: stuck payment notifications, checkouts in review and failed tasks."""

import uuid

from sqlalchemy import select

from app.core.outbox import OutboxStatus, OutboxTask
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from app.modules.audit.models import AuditAction, PlatformAuditLog
from app.modules.payments.gateways import FakeGateway
from app.modules.payments.models import WebhookEvent, WebhookStatus
from tests.factories import QueryCounter, headers_for, make_user
from tests.test_payments import NAIRA
from tests.test_payments import world as world  # the fixture
from tests.test_platform_billing import world as billing_world  # noqa: F401 (a fixture)
from tests.test_subscription_card import VERIFY, alpha_bill, start

PROBLEMS = "/api/v1/platform/problems"


def platform_admin(db):
    admin = make_user(db, None, Role.PLATFORM_ADMIN)
    db.commit()
    return headers_for(db, admin)


async def park_unknown_transfer(client):
    headers, body = FakeGateway.shared().simulate_transfer("9999999999", 5_000 * NAIRA)
    assert (await client.post("/api/v1/webhooks/fake", headers=headers, content=body)).status_code == 200


def audited(db, action):
    scope_platform_wide(db)
    return list(db.scalars(select(PlatformAuditLog).where(PlatformAuditLog.action == action.value)))


async def test_a_parked_transfer_is_listed_then_resolved_with_a_note(client, world, db):
    await park_unknown_transfer(client)
    admin = platform_admin(db)
    found = (await client.get(PROBLEMS, headers=admin)).json()
    [parked] = found["webhooks"]
    assert parked["status"] == "needs_review" and parked["school_name"] is None
    assert parked["claimed_amount_kobo"] == 5_000 * NAIRA and "Unknown receiving account" in parked["note"]
    overview = (await client.get("/api/v1/platform/overview", headers=admin)).json()
    assert overview["problems_open"] == 1

    short = await client.post(f"{PROBLEMS}/webhooks/{parked['id']}/resolve", headers=admin, json={"note": "ok"})
    assert short.status_code == 422  # a real note, please
    done = await client.post(
        f"{PROBLEMS}/webhooks/{parked['id']}/resolve", headers=admin, json={"note": "Refunded by Paystack"}
    )
    assert done.status_code == 204
    assert (await client.get(PROBLEMS, headers=admin)).json()["webhooks"] == []
    again = await client.post(f"{PROBLEMS}/webhooks/{parked['id']}/resolve", headers=admin, json={"note": "Twice"})
    assert again.status_code == 409
    [entry] = audited(db, AuditAction.PROBLEM_RESOLVED)
    assert entry.changes["note"] == "Refunded by Paystack"


async def test_retrying_a_parked_transfer_processes_it_again(client, world, db):
    await park_unknown_transfer(client)
    admin = platform_admin(db)
    [parked] = (await client.get(PROBLEMS, headers=admin)).json()["webhooks"]
    assert (await client.post(f"{PROBLEMS}/webhooks/{parked['id']}/retry", headers=admin)).status_code == 204
    scope_platform_wide(db)
    event = db.get(WebhookEvent, uuid.UUID(parked["id"]))
    db.refresh(event)
    # Processed again (the account is still unknown, so it's parked again, with a fresh attempt count)
    assert event.status == WebhookStatus.NEEDS_REVIEW and event.attempts == 1
    assert len(audited(db, AuditAction.PROBLEM_RETRIED)) == 1


async def test_a_subscription_card_payment_in_review_is_resolved(client, billing_world, db):  # noqa: F811
    bill = await alpha_bill(client, billing_world)
    reference = (await start(client, billing_world["alpha_bursar"], bill)).json()["reference"]
    FakeGateway.shared().complete_checkout(reference, amount_kobo=100)
    await client.post(VERIFY, headers=billing_world["alpha_bursar"], json={"reference": reference})
    admin = billing_world["admin"]
    [checkout] = (await client.get(PROBLEMS, headers=admin)).json()["checkouts"]
    assert checkout["kind"] == "subscription" and checkout["school_name"] == "Alpha College"
    assert checkout["amount_kobo"] == bill["balance_kobo"] and "expected" in checkout["review_reason"]
    resolved = await client.post(
        f"{PROBLEMS}/checkouts/{reference}/resolve", headers=admin, json={"note": "Refunded the ₦1 by Paystack"}
    )
    assert resolved.status_code == 204
    assert (await client.get(PROBLEMS, headers=admin)).json()["checkouts"] == []
    bad = await client.post(f"{PROBLEMS}/checkouts/NOT-A-REF/resolve", headers=admin, json={"note": "Nope"})
    assert bad.status_code == 422


async def test_a_failed_background_task_is_put_back_in_the_queue(client, db):
    task = OutboxTask(id=uuid.uuid4(), task_name="email.send", payload={}, status=OutboxStatus.FAILED, attempts=8)
    task.last_error = "SMTP down"
    db.add(task)
    db.commit()
    admin = platform_admin(db)
    [listed] = (await client.get(PROBLEMS, headers=admin)).json()["tasks"]
    assert (listed["task_name"], listed["last_error"]) == ("email.send", "SMTP down")
    assert (await client.post(f"{PROBLEMS}/tasks/{task.id}/retry", headers=admin)).status_code == 204
    db.refresh(task)
    assert task.status == OutboxStatus.PENDING and task.attempts == 0
    assert (await client.post(f"{PROBLEMS}/tasks/{task.id}/retry", headers=admin)).status_code == 404


async def test_problems_are_for_platform_admins_and_a_fixed_number_of_queries(client, world, db, connection):
    for _ in range(3):
        await park_unknown_transfer(client)
    assert (await client.get(PROBLEMS, headers=world["proprietor"])).status_code == 403
    admin = platform_admin(db)
    with QueryCounter(connection) as counter:
        found = (await client.get(PROBLEMS, headers=admin)).json()
    assert len(found["webhooks"]) == 3
    with QueryCounter(connection) as more:
        await park_unknown_transfer(client)
    with QueryCounter(connection) as again:
        (await client.get(PROBLEMS, headers=admin)).json()
    assert len(again.statements) == len(counter.statements) and more.statements

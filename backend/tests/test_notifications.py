"""Messages to parents: receipts, fee notices, reminders, wording, opt-outs and limits."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.accounts.models import Role
from app.modules.notifications import service, sms, whatsapp
from app.modules.notifications.models import Channel, MessageKind, MessageLog, MessageStatus
from app.modules.notifications.service import LAGOS, NewMessage
from app.modules.notifications.templates import render, validate
from tests.factories import (
    QueryCounter,
    headers_for,
    make_calendar,
    make_levels,
    make_school,
    make_student,
    make_user,
)
from tests.test_fees import add_fees, generate
from tests.test_parents import link_guardian
from tests.test_payments import connect_gateway, pay_cash

PHONE = "+2348031112222"


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    """No quiet hours unless a test asks for them, so results don't depend on when the suite runs."""
    monkeypatch.setattr(get_settings(), "quiet_hours_start", 0)
    monkeypatch.setattr(get_settings(), "quiet_hours_end", 0)


@pytest.fixture
def hold_messages(monkeypatch):
    """Queue messages without sending them, so a test can change things before delivery."""
    monkeypatch.setattr(service, "next_daytime", lambda moment: moment + timedelta(hours=1))


def lagos_today() -> date:
    return datetime.now(LAGOS).date()


def at(day: date, hour: int = 9) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=LAGOS)


@pytest.fixture
def world(db):
    school = make_school(db, "Alpha College")
    bursar, admin = make_user(db, school, Role.BURSAR), make_user(db, school, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(school)
    levels = make_levels(school)
    add_fees(school, terms[0], levels)
    student = make_student(school, terms[0], levels[0].arms[0], first="Tolu", last="Adeyemi")
    guardian = link_guardian(school, [student])
    connect_gateway(school)
    db.commit()
    return {
        "school": school,
        "terms": terms,
        "levels": levels,
        "student": student,
        "guardian": guardian,
        "bursar": headers_for(db, bursar),
        "admin": headers_for(db, admin),
    }


async def messages(client, world, **query):
    response = await client.get("/api/v1/messages", headers=world["bursar"], params=query)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def message_rows(db, school):
    scope_to_school(db, school.id)
    db.expire_all()
    return list(db.scalars(select(MessageLog).order_by(MessageLog.created_at)))


# --- Wording ---------------------------------------------------------------------------------------


def test_wording_is_checked_and_filled():
    assert validate("  Hi {parent_name}  ") == "Hi {parent_name}"
    with pytest.raises(DomainError, match=r"Unknown placeholder: \{bvn\}"):
        validate("Hi {parent_name} {bvn}")
    with pytest.raises(DomainError, match="curly brackets"):
        validate("Hi {parent_name")
    with pytest.raises(DomainError, match="under 640"):
        validate("x" * 641)
    # Attribute and index lookups can't reach anything: only plain names are allowed
    with pytest.raises(DomainError):
        validate("{parent_name.__class__}")
    assert render("Hi {parent_name}, {balance} owed", {"parent_name": "Mrs A"}) == "Hi Mrs A,  owed"


async def test_school_wording_preview_and_reset(client, world):
    url = "/api/v1/message-templates"
    listed = (await client.get(url, headers=world["bursar"])).json()
    assert {t["kind"] for t in listed["templates"]} == {"receipt", "fee_notice", "reminder"}
    assert "balance" in listed["placeholders"]

    bad = await client.put(f"{url}/reminder", headers=world["bursar"], json={"body": "Pay {now}"})
    assert bad.status_code == 400 and bad.json()["error"]["field"] == "body"

    saved = await client.put(f"{url}/reminder", headers=world["bursar"], json={"body": "Pay {balance}, {parent_name}"})
    assert saved.json()["is_custom"] is True
    preview = await client.post(f"{url}/preview", headers=world["bursar"], json={"body": "Pay {balance}"})
    assert preview.json()["text"] == "Pay ₦55,000.00"

    reset = await client.put(f"{url}/reminder", headers=world["bursar"], json={"body": None})
    assert reset.json()["is_custom"] is False and reset.json()["body"] == reset.json()["default_body"]


async def test_only_staff_with_messaging_rights(client, world):
    for path in ("/api/v1/messages", "/api/v1/message-templates", "/api/v1/reminder-settings"):
        assert (await client.get(path, headers=world["admin"])).status_code == 403
    assert (
        await client.post(f"/api/v1/students/{world['student'].id}/remind", headers=world["admin"])
    ).status_code == 403


# --- Receipts and fee notices ----------------------------------------------------------------------


async def test_payment_sends_a_receipt_by_whatsapp(client, world):
    await generate(client, world, due_date=str(lagos_today() + timedelta(days=30)))
    payment = await pay_cash(client, world, 50_000)

    assert len(whatsapp.sent_whatsapp) == 1
    sent = whatsapp.sent_whatsapp[0]
    assert sent.to == PHONE and sent.template == "payment_receipt"
    parent, amount, child, school, number, balance, verify = sent.parameters
    assert (parent, amount, child, school) == ("Mrs Adeyemi", "₦50,000.00", "Tolu Adeyemi", "Alpha College")
    assert number.isdigit() and balance == "₦105,000.00" and "/verify/" in verify

    [logged] = await messages(client, world)
    assert logged["kind"] == "receipt" and logged["status"] == "sent" and logged["channel"] == "whatsapp"
    assert "₦50,000.00" in logged["body"] and logged["student_name"] == "Tolu Adeyemi"
    assert payment["payment_id"]


async def test_generating_bills_can_notify_parents_once(client, world):
    report = await generate(client, world, due_date=str(lagos_today() + timedelta(days=14)), notify_parents=True)
    assert report["notices_queued"] == 1
    [sent] = whatsapp.sent_whatsapp
    assert sent.template == "fee_notice"
    assert sent.parameters[4] == "₦155,000.00"  # amount
    # Re-running generation skips invoiced students, and the dedupe key stops a second notice anyway
    again = await generate(client, world, notify_parents=True)
    assert again["notices_queued"] == 0 and len(whatsapp.sent_whatsapp) == 1


async def test_bills_without_notify_send_nothing(client, world):
    report = await generate(client, world)
    assert report["notices_queued"] == 0 and whatsapp.sent_whatsapp == [] and sms.sent_sms == []


# --- Channels --------------------------------------------------------------------------------------


async def test_falls_back_to_sms_then_email(client, world, db, monkeypatch):
    def no_whatsapp(_message):
        raise whatsapp.WhatsAppError("not on WhatsApp")

    monkeypatch.setattr(whatsapp, "send_template", no_whatsapp)
    await pay_cash(client, world, 1_000)
    [logged] = await messages(client, world)
    assert logged["channel"] == "sms" and logged["error"] == ""
    assert sms.sent_sms[-1].to == PHONE and "received ₦1,000.00" in sms.sent_sms[-1].text

    def no_sms(_message):
        raise sms.SmsError("provider down")

    monkeypatch.setattr(service, "send_sms", no_sms)
    sent_email = []
    monkeypatch.setattr(service, "deliver_email", sent_email.append)
    from app.modules.students.models import Guardian
    from tests.factories import tenant_writer

    with tenant_writer() as w:
        w.get(Guardian, world["guardian"].id).email = "parent@example.ng"
    await pay_cash(client, world, 2_000)
    # Both messages share the test transaction's timestamp, so find the second payment's by its amount
    newest = next(m for m in await messages(client, world) if "₦2,000.00" in m["body"])
    assert newest["channel"] == "email"
    assert sent_email[0].to == "parent@example.ng" and sent_email[0].subject == "Payment received: Tolu Adeyemi"


async def test_every_channel_failing_is_retried_then_marked_failed(client, world, db, monkeypatch, hold_messages):
    def no_whatsapp(_message):
        raise whatsapp.WhatsAppError("not on WhatsApp")

    def no_sms(_message):
        raise sms.SmsError("down")

    monkeypatch.setattr(whatsapp, "send_template", no_whatsapp)
    monkeypatch.setattr(service, "send_sms", no_sms)
    await pay_cash(client, world, 1_000)  # receipts aren't held: delivered (and failed) straight away
    [row] = message_rows(db, world["school"])
    assert row.status == MessageStatus.QUEUED and row.attempts == 1 and "sms: down" in row.error

    for _ in range(2):
        service.deliver(world["school"].id, row.id)
    db.expire_all()
    assert row.status == MessageStatus.FAILED and row.attempts == 3


# --- Checks at sending time ------------------------------------------------------------------------


async def test_opt_out_is_checked_when_sending_and_receipts_still_go(client, world, db, hold_messages):
    await generate(client, world, due_date=str(lagos_today()))
    queued = await client.post(f"/api/v1/students/{world['student'].id}/remind", headers=world["bursar"])
    assert queued.status_code == 202 and queued.json() == {"queued": 1}

    # The parent opts out in the portal after the reminder was queued
    from tests.test_parents import bearer, sign_in

    parent = bearer(await sign_in(client))
    assert (await client.get("/api/v1/parent/preferences", headers=parent)).json()["reminders"] is True
    off = await client.put("/api/v1/parent/preferences", headers=parent, json={"reminders": False})
    assert off.json()["reminders"] is False

    [reminder] = [m for m in message_rows(db, world["school"]) if m.kind == MessageKind.REMINDER]
    assert service.deliver(world["school"].id, reminder.id) == MessageStatus.SKIPPED
    db.expire_all()
    assert reminder.skip_reason == "opted_out" and whatsapp.sent_whatsapp == []

    await pay_cash(client, world, 1_000)
    assert [m.template for m in whatsapp.sent_whatsapp] == ["payment_receipt"]

    # Staff can't push a manual reminder past the opt-out either
    refused = await client.post(f"/api/v1/students/{world['student'].id}/remind", headers=world["bursar"])
    assert refused.status_code == 409


async def test_reminder_is_dropped_if_paid_before_sending(client, world, db, hold_messages):
    await generate(client, world, due_date=str(lagos_today()))
    await client.post(f"/api/v1/students/{world['student'].id}/remind", headers=world["bursar"])
    [reminder] = message_rows(db, world["school"])
    await pay_cash(client, world, 155_000)
    assert service.deliver(world["school"].id, reminder.id) == MessageStatus.SKIPPED
    db.expire_all()
    assert reminder.skip_reason == "paid"


async def test_stale_messages_are_not_sent_late(client, world, db, hold_messages):
    await generate(client, world, due_date=str(lagos_today()))
    await client.post(f"/api/v1/students/{world['student'].id}/remind", headers=world["bursar"])
    [reminder] = message_rows(db, world["school"])
    db.execute(update(MessageLog).values(created_at=datetime.now(UTC) - timedelta(hours=25)))
    db.commit()
    assert service.deliver(world["school"].id, reminder.id) == MessageStatus.SKIPPED
    db.expire_all()
    assert reminder.skip_reason == "too_old"


async def test_quiet_hours_hold_notices_but_not_receipts(client, world, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "quiet_hours_start", 20)
    monkeypatch.setattr(get_settings(), "quiet_hours_end", 7)
    late = datetime(2026, 10, 20, 22, 30, tzinfo=LAGOS)
    assert service.next_daytime(late) == datetime(2026, 10, 21, 7, tzinfo=LAGOS)
    early = datetime(2026, 10, 20, 5, 0, tzinfo=LAGOS)
    assert service.next_daytime(early) == datetime(2026, 10, 20, 7, tzinfo=LAGOS)
    noon = datetime(2026, 10, 20, 12, 0, tzinfo=LAGOS)
    assert service.next_daytime(noon) == noon

    # At night: the receipt goes, the reminder waits for the morning
    monkeypatch.setattr(service, "_in_quiet_hours", lambda _moment: True)
    await generate(client, world, due_date=str(lagos_today()))
    await pay_cash(client, world, 1_000)
    await client.post(f"/api/v1/students/{world['student'].id}/remind", headers=world["bursar"])
    rows = {m.kind: m for m in message_rows(db, world["school"])}
    assert rows[MessageKind.RECEIPT].status == MessageStatus.SENT
    assert rows[MessageKind.REMINDER].status == MessageStatus.QUEUED
    assert service.deliver(world["school"].id, rows[MessageKind.REMINDER].id) == MessageStatus.QUEUED


# --- Manual reminders ------------------------------------------------------------------------------


async def test_manual_reminder_once_a_day_and_only_when_owed(client, world):
    url = f"/api/v1/students/{world['student'].id}/remind"
    nothing = await client.post(url, headers=world["bursar"])
    assert nothing.status_code == 409 and "Nothing is owed" in nothing.json()["error"]["message"]

    await generate(client, world, due_date=str(lagos_today()))
    assert (await client.post(url, headers=world["bursar"])).status_code == 202
    [sent] = whatsapp.sent_whatsapp
    assert sent.template == "fee_reminder" and sent.parameters[2] == "₦155,000.00"
    again = await client.post(url, headers=world["bursar"])
    assert again.status_code == 409 and "already" in again.json()["error"]["message"]


async def test_staff_can_turn_a_parents_reminders_off(client, world, db):
    url = f"/api/v1/guardians/{world['guardian'].id}/reminders"
    assert (await client.put(url, headers=world["admin"], json={"reminders": False})).json()["reminders"] is False
    detail = (await client.get(f"/api/v1/students/{world['student'].id}", headers=world["admin"])).json()
    assert detail["guardians"][0]["reminders"] is False
    assert (await client.put(url, headers=world["admin"], json={"reminders": True})).json()["reminders"] is True


# --- Scheduled reminders ---------------------------------------------------------------------------


async def save_schedule(client, world, **changes):
    body = {
        "enabled": True,
        "days_before_due": [7, 1],
        "days_after_due": [3, 14, 30],
        "send_hour": 9,
        "max_per_invoice": 4,
        "min_gap_days": 3,
    } | changes
    response = await client.put("/api/v1/reminder-settings", headers=world["bursar"], json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def test_reminder_settings_are_validated(client, world):
    saved = await save_schedule(client, world, days_before_due=[1, 7, 7], days_after_due=[30, 3])
    assert saved["days_before_due"] == [7, 1] and saved["days_after_due"] == [3, 30]
    for bad in ({"send_hour": 22}, {"max_per_invoice": 50}, {"days_after_due": [0]}, {"min_gap_days": 0}):
        response = await client.put(
            "/api/v1/reminder-settings",
            headers=world["bursar"],
            json={
                "enabled": True,
                "days_before_due": [],
                "days_after_due": [3],
                "send_hour": 9,
                "max_per_invoice": 3,
                "min_gap_days": 3,
            }
            | bad,
        )
        assert response.status_code in (400, 422), bad


async def test_reminders_follow_the_schedule(client, world, db):
    due = lagos_today() + timedelta(days=7)
    await generate(client, world, due_date=str(due))

    assert service.schedule_reminders(at(due - timedelta(days=7), hour=8)) == 0  # not the sending hour
    assert service.schedule_reminders(at(due - timedelta(days=5))) == 0  # not a reminder day
    assert service.schedule_reminders(at(due - timedelta(days=7))) == 1
    assert service.schedule_reminders(at(due - timedelta(days=7))) == 0  # same day again: deduped
    assert [m.template for m in whatsapp.sent_whatsapp] == ["fee_reminder"]

    assert service.schedule_reminders(at(due - timedelta(days=1))) == 1
    assert service.schedule_reminders(at(due + timedelta(days=3))) == 1


async def test_reminders_respect_cap_gap_and_payment(client, world, db):
    await save_schedule(client, world, days_before_due=[3, 2], days_after_due=[1, 5], max_per_invoice=2, min_gap_days=2)
    due = lagos_today() + timedelta(days=3)
    await generate(client, world, due_date=str(due))

    assert service.schedule_reminders(at(due - timedelta(days=3))) == 1
    assert service.schedule_reminders(at(due - timedelta(days=2))) == 0  # only a day since the last one
    assert service.schedule_reminders(at(due + timedelta(days=1))) == 1
    assert service.schedule_reminders(at(due + timedelta(days=5))) == 0  # cap of 2 reached

    other = make_student(world["school"], world["terms"][0], world["levels"][0].arms[0], first="Paid")
    link_guardian(world["school"], [other], phone="+2348077776666", name="Mr Paid")
    await generate(client, world, due_date=str(due))
    await pay_cash(client, world, 155_000, student=other)
    assert service.schedule_reminders(at(due + timedelta(days=5))) == 0  # paid up: no reminder


async def test_reminders_skip_opted_out_disabled_and_suspended(client, world, db):
    due = lagos_today() + timedelta(days=7)
    await generate(client, world, due_date=str(due))
    url = f"/api/v1/guardians/{world['guardian'].id}/reminders"
    await client.put(url, headers=world["admin"], json={"reminders": False})
    assert service.schedule_reminders(at(due - timedelta(days=7))) == 0
    await client.put(url, headers=world["admin"], json={"reminders": True})

    await save_schedule(client, world, enabled=False)
    assert service.schedule_reminders(at(due - timedelta(days=7))) == 0
    await save_schedule(client, world, enabled=True)

    from app.modules.schools.models import School, SchoolStatus

    scope_platform_wide(db)
    db.execute(update(School).where(School.id == world["school"].id).values(status=SchoolStatus.SUSPENDED))
    db.commit()
    assert service.schedule_reminders(at(due - timedelta(days=7))) == 0


async def test_scheduling_is_a_fixed_number_of_queries(client, world, connection, hold_messages):
    due = lagos_today() + timedelta(days=7)
    school, term, arm = world["school"], world["terms"][0], world["levels"][0].arms[0]
    for i in range(2):
        link_guardian(school, [make_student(school, term, arm, first=f"S{i}")], phone=f"+23480300000{i:02d}")
    await generate(client, world, due_date=str(due))
    with QueryCounter(connection) as small:
        assert service.schedule_reminders(at(due - timedelta(days=7))) == 3

    for i in range(25):
        link_guardian(school, [make_student(school, term, arm, first=f"M{i}")], phone=f"+23480400000{i:02d}")
    await generate(client, world, due_date=str(due))
    with QueryCounter(connection) as big:
        assert service.schedule_reminders(at(due - timedelta(days=1))) == 28
    assert big.count == small.count


# --- Isolation -------------------------------------------------------------------------------------


async def test_schools_cannot_see_or_touch_each_others_messaging(client, world, db):
    other = make_school(db, "Beta Academy")
    other_bursar = make_user(db, other, Role.BURSAR)
    _, other_terms = make_calendar(other)
    other_levels = make_levels(other)
    add_fees(other, other_terms[0], other_levels)
    their_student = make_student(other, other_terms[0], other_levels[0].arms[0], first="Theirs")
    their_guardian = link_guardian(other, [their_student], phone="+2348055554444", name="Their Parent")
    connect_gateway(other)
    db.commit()
    theirs = {"student": their_student, "admin": headers_for(db, make_user(db, other, Role.SCHOOL_ADMIN))}
    theirs["bursar"] = headers_for(db, other_bursar)
    db.commit()

    await client.put("/api/v1/message-templates/receipt", headers=theirs["bursar"], json={"body": "Beta got {amount}"})
    await pay_cash(client, theirs, 1_000)

    assert await messages(client, world) == []
    mine = (await client.get("/api/v1/message-templates", headers=world["bursar"])).json()["templates"]
    assert all(not t["is_custom"] for t in mine)
    assert (
        await client.put(
            f"/api/v1/guardians/{their_guardian.id}/reminders", headers=world["admin"], json={"reminders": False}
        )
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/students/{their_student.id}/remind", headers=world["bursar"])
    ).status_code == 404
    assert (await messages(client, world, student_id=str(their_student.id))) == []

    # Each school's message uses its own wording
    await pay_cash(client, world, 1_000)
    scope_platform_wide(db)
    bodies = {m.school_id: m.body for m in db.scalars(select(MessageLog))}
    assert bodies[other.id] == "Beta got ₦1,000.00"
    assert bodies[world["school"].id].startswith("Dear Mrs Adeyemi")


def test_queue_ignores_repeats(db, world):
    message = NewMessage(world["guardian"].id, world["student"].id, MessageKind.RECEIPT, f"test:{uuid.uuid4()}")
    scope_to_school(db, world["school"].id)
    assert service.queue(db, world["school"].id, [message, message]) == 1
    assert service.queue(db, world["school"].id, [message]) == 0
    db.rollback()


def test_channel_enum_values_are_stable():
    # Stored in the database and shown in the console: renaming one needs a migration
    assert [c.value for c in Channel] == ["whatsapp", "sms", "email", "voice"]

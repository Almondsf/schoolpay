"""Phone calls to parents who prefer them: wording, when calls are made, the provider's callback, preferences."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.tenancy import scope_to_school
from app.modules.accounts.models import Role
from app.modules.notifications import service, voice
from app.modules.notifications.models import Channel, MessageKind, MessageLog, MessageStatus
from app.modules.payments.gateways import GatewayCode
from app.modules.payments.models import VirtualAccount
from app.modules.students.models import Guardian, Language
from tests.factories import headers_for, make_school, make_user, tenant_writer
from tests.test_fees import generate
from tests.test_notifications import PHONE, at, lagos_today, save_schedule
from tests.test_notifications import world as world  # the fixture
from tests.test_payments import pay_cash


@pytest.fixture(autouse=True)
def _any_hour(monkeypatch):
    """No quiet or calling hours unless a test asks, so results don't depend on when the suite runs."""
    for name, value in (
        ("quiet_hours_start", 0),
        ("quiet_hours_end", 0),
        ("call_hours_start", 0),
        ("call_hours_end", 24),
    ):
        monkeypatch.setattr(get_settings(), name, value)


async def calls_on(client, world, **changes):
    await save_schedule(client, world, calls_enabled=True, **changes)


async def prefers_calls(client, world, language="en"):
    response = await client.put(
        f"/api/v1/guardians/{world['guardian'].id}/calls",
        headers=world["admin"],
        json={"prefers_calls": True, "language": language},
    )
    assert response.status_code == 200, response.text
    return response.json()


def give_account_number(world):
    with tenant_writer() as w:
        w.add(
            VirtualAccount(
                school_id=world["school"].id,
                student_id=world["student"].id,
                gateway=GatewayCode.FAKE,
                account_number="9820000768",
                account_name="ALPHA/ADEYEMI TOLU",
                bank_name="Wema Bank",
                provider_reference="ref",
            )
        )


def call_rows(db, world):
    scope_to_school(db, world["school"].id)
    db.expire_all()
    return list(
        db.scalars(select(MessageLog).where(MessageLog.kind.in_(service.CALL_KINDS)).order_by(MessageLog.created_at))
    )


def test_numbers_are_said_slowly_and_amounts_plainly():
    assert voice.spoken_digits("9820000768") == "9 8 2 0, 0 0 0, 7 6 8"
    assert voice.spoken_naira(6_800_000) == "68,000 naira"
    assert voice.spoken_naira(6_800_050) == "68,000 naira and 50 kobo"
    english = voice.reminder_lines(
        Language.ENGLISH, school="Alpha", child="Tolu", owed_kobo=6_800_000, account_number="9820000768", bank="Wema"
    )
    assert sum("9 8 2 0, 0 0 0, 7 6 8" in line for line in english) == 2  # said twice
    assert any("POS agent" in line for line in english)
    pidgin = voice.reminder_lines(
        Language.PIDGIN, school="Alpha", child="Tolu", owed_kobo=100, account_number=None, bank=None
    )
    assert any("Na Alpha dey call" in line for line in pidgin) and any("bursary" in line for line in pidgin)


async def test_payment_confirmation_call_only_when_wanted_and_switched_on(client, world, db):
    await generate(client, world)
    await pay_cash(client, world, 50_000)
    assert voice.placed_calls == []  # nobody asked for calls

    await prefers_calls(client, world)
    await pay_cash(client, world, 10_000)
    assert voice.placed_calls == []  # the school hasn't switched calls on

    await calls_on(client, world)
    await pay_cash(client, world, 20_000)
    [call] = voice.placed_calls
    assert call.to == PHONE
    said = " ".join(call.lines)
    assert "received 20,000 naira for Tolu" in said and "75,000 naira is still owed" in said
    [row] = call_rows(db, world)
    assert row.kind == MessageKind.CALL_RECEIPT and row.channel == Channel.VOICE and row.status == MessageStatus.SENT


async def test_overdue_reminders_also_call_capped_per_bill(client, world, db):
    await calls_on(client, world, days_before_due=[1], days_after_due=[3, 10, 20], max_calls_per_invoice=2)
    await prefers_calls(client, world, language="pcm")
    give_account_number(world)
    due = lagos_today() + timedelta(days=1)
    await generate(client, world, due_date=str(due))

    assert service.schedule_reminders(at(due - timedelta(days=1))) == 1  # before the due date: text only
    assert voice.placed_calls == []
    assert service.schedule_reminders(at(due + timedelta(days=3))) == 2  # overdue: text and call
    [call] = voice.placed_calls
    said = " ".join(call.lines)
    assert "155,000 naira still remain for Tolu" in said and said.count("9 8 2 0, 0 0 0, 7 6 8") == 2
    assert service.schedule_reminders(at(due + timedelta(days=10))) == 2
    assert service.schedule_reminders(at(due + timedelta(days=20))) == 1  # two calls already: text only
    assert len(voice.placed_calls) == 2


async def test_call_is_rechecked_when_it_goes_out(client, world, db, monkeypatch):
    await calls_on(client, world, days_before_due=[1], days_after_due=[3])
    await prefers_calls(client, world)
    due = lagos_today() + timedelta(days=1)
    await generate(client, world, due_date=str(due))
    monkeypatch.setattr(service, "next_call_time", lambda moment: moment + timedelta(hours=1))  # hold the call
    service.schedule_reminders(at(due + timedelta(days=3)))
    [held] = call_rows(db, world)

    await pay_cash(client, world, 155_000)  # paid before the call went
    assert service.deliver(world["school"].id, held.id) == MessageStatus.SKIPPED
    assert next(r for r in call_rows(db, world) if r.id == held.id).skip_reason == "paid"
    assert voice.placed_calls == []


async def test_no_calls_outside_calling_hours(client, world, db, monkeypatch):
    await calls_on(client, world)
    await prefers_calls(client, world)
    await generate(client, world)
    monkeypatch.setattr(get_settings(), "call_hours_start", 23)
    monkeypatch.setattr(get_settings(), "call_hours_end", 23)  # never
    await pay_cash(client, world, 10_000)
    assert voice.placed_calls == []
    [row] = call_rows(db, world)
    assert row.status == MessageStatus.QUEUED


async def test_turning_calls_off_stops_queued_ones(client, world, db, monkeypatch):
    await calls_on(client, world)
    await prefers_calls(client, world)
    await generate(client, world)
    monkeypatch.setattr(service, "next_call_time", lambda moment: moment + timedelta(hours=1))
    await pay_cash(client, world, 10_000)
    [held] = call_rows(db, world)
    with tenant_writer() as w:
        w.execute(update(Guardian).values(prefers_calls=False))
    assert service.deliver(world["school"].id, held.id) == MessageStatus.SKIPPED
    assert call_rows(db, world)[0].skip_reason == "calls_off"


async def test_provider_callback_reads_the_words_for_the_right_number_only(client, world, db, monkeypatch):
    await calls_on(client, world)
    await prefers_calls(client, world)
    await generate(client, world)
    monkeypatch.setattr(voice, "place_call", lambda call: "ATVId_123")
    await pay_cash(client, world, 10_000)

    answered = await client.post(
        "/api/v1/voice/africastalking",
        data={"isActive": "1", "sessionId": "ATVId_123", "destinationNumber": PHONE},
    )
    assert answered.status_code == 200 and answered.headers["content-type"].startswith("application/xml")
    assert "<Say" in answered.text and "received 10,000 naira for Tolu" in answered.text

    for form in (
        {"isActive": "1", "sessionId": "ATVId_123", "destinationNumber": "+2348000000000"},  # someone else's number
        {"isActive": "1", "sessionId": "ATVId_unknown", "destinationNumber": PHONE},
    ):
        response = await client.post("/api/v1/voice/africastalking", data=form)
        assert "<Reject/>" in response.text and "naira" not in response.text

    ended = await client.post(
        "/api/v1/voice/africastalking",
        data={"isActive": "0", "sessionId": "ATVId_123", "status": "NotAnswered", "durationInSeconds": "0"},
    )
    assert ended.status_code == 200
    assert "not completed" in call_rows(db, world)[0].error


async def test_preferences_for_staff_and_parents(client, world, db):
    assert (await prefers_calls(client, world, language="pcm")) == {"prefers_calls": True, "language": "pcm"}
    detail = await client.get(f"/api/v1/students/{world['student'].id}", headers=world["admin"])
    [guardian] = detail.json()["guardians"]
    assert guardian["prefers_calls"] is True and guardian["language"] == "pcm"

    stranger = make_user(db, make_school(db), Role.SCHOOL_ADMIN)
    db.commit()
    other = await client.put(
        f"/api/v1/guardians/{world['guardian'].id}/calls",
        headers=headers_for(db, stranger),
        json={"prefers_calls": False},
    )
    assert other.status_code == 404  # another school's parent


async def test_call_wording_is_fixed_and_settings_saved(client, world):
    templates = (await client.get("/api/v1/message-templates", headers=world["bursar"])).json()["templates"]
    assert {t["kind"] for t in templates} == {"receipt", "fee_notice", "reminder"}
    refused = await client.put("/api/v1/message-templates/call_reminder", headers=world["bursar"], json={"body": "Hi"})
    assert refused.status_code == 400
    await calls_on(client, world, max_calls_per_invoice=3)
    saved = (await client.get("/api/v1/reminder-settings", headers=world["bursar"])).json()
    assert saved["calls_enabled"] is True and saved["max_calls_per_invoice"] == 3

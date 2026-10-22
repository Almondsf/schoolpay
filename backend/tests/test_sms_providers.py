"""Switching the SMS provider at runtime (Termii or Africa's Talking), and the Africa's Talking sender."""

import logging
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.core import outbound
from app.core.config import get_settings
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role
from app.modules.audit.models import PlatformAuditLog
from app.modules.notifications import providers, sms
from app.modules.notifications.sms import Sms, SmsError, send_sms
from tests.factories import headers_for, make_user
from tests.test_gateways import mock_api

PHONE = "+2348031234567"


@pytest.fixture
def both_configured(monkeypatch):
    """Keys for both providers and real sending (no memory backend); the provider cache starts empty."""
    settings = get_settings()
    monkeypatch.setattr(settings, "sms_backend", "termii")
    monkeypatch.setattr(settings, "termii_api_key", "termii-key")
    monkeypatch.setattr(settings, "africastalking_username", "wardpay")
    monkeypatch.setattr(settings, "africastalking_api_key", "at-key")
    monkeypatch.setattr(settings, "africastalking_sms_sender", "WardPay")
    providers.forget_cached_provider()
    yield
    providers.forget_cached_provider()


def at_reply(status_code: int = 101, status: str = "Success") -> httpx.Response:
    recipient = {"statusCode": status_code, "number": PHONE, "status": status, "messageId": "ATXid_1"}
    return httpx.Response(201, json={"SMSMessageData": {"Message": "Sent to 1/1", "Recipients": [recipient]}})


def test_africastalking_sends_in_the_sms_alphabet_with_the_sender_id(monkeypatch, both_configured):
    calls = mock_api(monkeypatch, sms, lambda r: at_reply())
    monkeypatch.setattr(providers, "current_sms_provider", lambda: "africastalking")
    monkeypatch.setattr(sms, "current_sms_provider", lambda: "africastalking")
    send_sms(Sms(to=PHONE, text="Paid ₦5,000. Thank you"))
    [request] = calls
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert request.url.path == "/version1/messaging" and request.headers["apikey"] == "at-key"
    assert form == {"username": "wardpay", "to": PHONE, "message": "Paid NGN 5,000. Thank you", "from": "WardPay"}


@pytest.mark.parametrize(("code", "status"), [(403, "InvalidPhoneNumber"), (406, "UserInBlacklist")])
def test_africastalking_refusals_raise_so_delivery_retries_or_fails(monkeypatch, both_configured, code, status):
    mock_api(monkeypatch, sms, lambda r: at_reply(code, status))
    monkeypatch.setattr(sms, "current_sms_provider", lambda: "africastalking")
    with pytest.raises(SmsError, match=status):
        send_sms(Sms(to=PHONE, text="Hello"))


def test_africastalking_sandbox_username_uses_the_sandbox(monkeypatch, both_configured):
    monkeypatch.setattr(get_settings(), "africastalking_username", "sandbox")
    calls = mock_api(monkeypatch, sms, lambda r: at_reply())
    monkeypatch.setattr(sms, "current_sms_provider", lambda: "africastalking")
    send_sms(Sms(to=PHONE, text="Hello"))
    assert calls[0].url.host == "api.sandbox.africastalking.com"


def test_the_providers_reply_never_logs_the_parents_number(caplog):
    client = httpx.Client(
        base_url="https://api.africastalking.com",
        event_hooks=outbound._hooks("africastalking", frozenset({"number"})),  # as _send_africastalking asks
        transport=httpx.MockTransport(lambda r: at_reply()),
    )
    with caplog.at_level(logging.INFO, logger="outbound.africastalking"), client:
        client.post("/version1/messaging", data={"to": PHONE})
    assert caplog.records and PHONE not in caplog.text and "ATXid_1" in caplog.text


async def test_a_platform_admin_switches_texts_to_africastalking(client, db, monkeypatch, both_configured):
    admin = make_user(db, None, Role.PLATFORM_ADMIN)
    db.commit()
    headers = headers_for(db, admin)
    view = (await client.get("/api/v1/platform/providers/sms", headers=headers)).json()
    assert view["active_provider"] == "termii" and view["available"] == ["termii", "africastalking"] and view["sending"]

    termii_calls = mock_api(monkeypatch, sms, lambda r: httpx.Response(200, json={"message_id": "1"}))
    send_sms(Sms(to=PHONE, text="Before"))
    assert termii_calls[0].url.host == "api.ng.termii.com"

    switched = await client.put("/api/v1/platform/providers/sms", headers=headers, json={"provider": "africastalking"})
    assert switched.status_code == 200 and switched.json()["active_provider"] == "africastalking"
    at_calls = mock_api(monkeypatch, sms, lambda r: at_reply())
    send_sms(Sms(to=PHONE, text="After"))  # the switch clears this process's cache
    assert at_calls[0].url.host == "api.africastalking.com"
    scope_platform_wide(db)
    entry = db.scalar(
        select(PlatformAuditLog)
        .where(PlatformAuditLog.action == "platform.provider_changed")
        .order_by(PlatformAuditLog.created_at.desc())
    )
    assert entry is not None and entry.changes["after"] == "africastalking"


async def test_only_configured_providers_and_only_platform_admins(client, db, monkeypatch, both_configured):
    monkeypatch.setattr(get_settings(), "africastalking_api_key", "")
    admin = make_user(db, None, Role.PLATFORM_ADMIN)
    db.commit()
    refused = await client.put(
        "/api/v1/platform/providers/sms", headers=headers_for(db, admin), json={"provider": "africastalking"}
    )
    assert refused.status_code == 400 and refused.json()["error"]["field"] == "provider"
    assert (
        await client.put("/api/v1/platform/providers/sms", headers=headers_for(db, admin), json={"provider": "other"})
    ).status_code == 422


def test_without_keys_nothing_is_sent(db, monkeypatch, both_configured):
    monkeypatch.setattr(get_settings(), "termii_api_key", "")
    monkeypatch.setattr(get_settings(), "africastalking_api_key", "")
    providers.forget_cached_provider()
    with pytest.raises(SmsError, match="No SMS provider"):
        send_sms(Sms(to=PHONE, text="Hello"))

"""Error reports never carry personal data, sign-in codes or tokens."""

from app.core.config import get_settings
from app.core.monitoring import init_monitoring, scrub


def test_error_events_are_scrubbed():
    event = {
        "message": "Transfer to 0123456789 failed for Bearer eyJhbGciOi.payload.sig",
        "user": {"email": "parent@example.com", "ip_address": "203.0.113.5"},
        "request": {
            "url": "https://app.example/api/v1/students?search=Chiamaka",
            "data": {"phone": "08031112222", "code": "123456"},
            "cookies": {"sp_refresh": "secret"},
            "headers": {"Authorization": "Bearer abc"},
            "query_string": "search=Chiamaka",
        },
        "exception": {"values": [{"type": "ValueError", "value": "otp=482913 rejected, bvn: 22233344455"}]},
        "extra": {"payload": {"token": "abc", "amount_kobo": 500, "note": "card 4084084084084081"}},
        "breadcrumbs": {"values": [{"message": "password=hunter2"}]},
    }
    cleaned = scrub(event)
    text = str(cleaned)
    for leak in ("0123456789", "eyJhbGciOi", "parent@example.com", "08031112222", "123456", "Chiamaka", "482913",
                 "22233344455", "hunter2", "4084084084084081", "sp_refresh"):  # fmt: skip
        assert leak not in text, leak
    assert cleaned["request"] == {"url": "https://app.example/api/v1/students"}
    assert cleaned["extra"]["payload"]["amount_kobo"] == 500  # harmless values stay useful
    assert "user" not in cleaned


def test_monitoring_is_off_without_a_dsn():
    assert get_settings().sentry_dsn == ""
    assert init_monitoring(get_settings(), component="api") is False

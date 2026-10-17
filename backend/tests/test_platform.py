"""Chunk 0: the platform plumbing every feature relies on."""

import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import RedactingFilter, redact
from app.core.redaction import REDACTED
from app.core.redaction import redact as redact_mapping


async def test_liveness(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_checks_database(client):
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True, "redis": True}


async def test_security_headers(client):
    response = await client.get("/api/v1/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


async def test_unknown_route_uses_error_envelope(client):
    response = await client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_cors_allows_only_configured_origins(client):
    allowed = await client.options(
        "/api/v1/health", headers={"Origin": "http://testserver.local", "Access-Control-Request-Method": "GET"}
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://testserver.local"
    denied = await client.options(
        "/api/v1/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in denied.headers


async def test_oversized_body_is_rejected(client):
    response = await client.post("/api/v1/health", content=b"x" * (11 * 1024 * 1024))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


@pytest.mark.parametrize(
    ("message", "leak"),
    [
        ("otp=123456 sent", "123456"),
        ('{"password": "hunter2"}', "hunter2"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("paystack_secret_key=sk_live_abc", "sk_live_abc"),
        ("credited 0123456789", "0123456789"),
        ("bvn: 22212345678", "22212345678"),
    ],
)
def test_log_redaction(message, leak):
    assert leak not in redact(message)


def test_log_redaction_keeps_harmless_keys():
    assert redact("status_code=200 school_code=ABC") == "status_code=200 school_code=ABC"


def test_redacting_filter_rewrites_records():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "token=%s", ("secret-value",), None)
    RedactingFilter().filter(record)
    assert "secret-value" not in record.getMessage()


def test_recursive_redaction():
    data = {"amount": 5000, "customer": {"bvn": "22212345678", "name": "A"}, "items": [{"otp": "1"}]}
    assert redact_mapping(data) == {
        "amount": 5000,
        "customer": {"bvn": REDACTED, "name": "A"},
        "items": [{"otp": REDACTED}],
    }


def _settings(**overrides):
    values = {
        "secret_key": "x" * 40,
        "database_url": "postgresql://u:p@localhost/db",
        "field_encryption_keys": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        "rate_limit_storage_url": "redis://localhost:6379/1",
        "environment": "production",
        # Override what the test environment sets, so only the explicit values count
        "debug": False,
        "email_backend": "console",
        "celery_task_always_eager": False,
        "cors_origins": "https://app.example",
    }
    return Settings(**{**values, **overrides})


def test_production_settings_are_safe_by_default():
    settings = _settings()
    assert settings.debug is False
    assert settings.secure_cookies is True
    assert settings.database_url.startswith("postgresql+psycopg://")


@pytest.mark.parametrize(
    "overrides",
    [
        {"debug": True},
        {"celery_task_always_eager": True},
        {"cors_origins": "*"},
        {"rate_limit_storage_url": "memory://"},
        {"secret_key": "short"},
        {"database_url": "sqlite:///x.db"},
    ],
)
def test_production_refuses_unsafe_settings(overrides):
    with pytest.raises(ValidationError):
        _settings(**overrides)

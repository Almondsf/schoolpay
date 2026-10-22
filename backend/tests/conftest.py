"""
Test harness.

- Configuration is set here explicitly; the developer's .env is never read.
- Tests run against TEST_DATABASE_URL, which must be a local database whose
  name ends in `_test`. Anything else is refused, so a misconfigured run can
  never touch real data.
- Each run migrates a fresh, randomly named schema (exercising the real
  Alembic migrations, triggers included) and drops it afterwards.
- Each test runs inside a transaction that is rolled back; code under test
  can still commit (commits become savepoints).
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from sqlalchemy.engine import make_url

TEST_SCHEMA = f"test_{uuid.uuid4().hex[:12]}"
_raw_url = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://wardpay@localhost:5433/wardpay_test")
_parsed = make_url(_raw_url)
if _parsed.host not in {"localhost", "127.0.0.1", "::1"} or not (_parsed.database or "").endswith("_test"):
    raise RuntimeError(
        f"Refusing to run tests against {_parsed.host}/{_parsed.database}: "
        "TEST_DATABASE_URL must be a local database whose name ends in _test."
    )
_SCHEMA_URL = _parsed.update_query_dict({"options": f"-csearch_path={TEST_SCHEMA}"}).render_as_string(
    hide_password=False
)

os.environ.update(
    {
        "WARDPAY_ENV_FILE": str(Path(__file__).with_name(".env.does-not-exist")),
        "ENVIRONMENT": "test",
        "DEBUG": "false",
        "SECRET_KEY": "test-signing-key-only-used-by-the-test-suite-0123456789",
        "DATABASE_URL": _SCHEMA_URL,
        "MIGRATION_DATABASE_URL": _SCHEMA_URL,
        "FIELD_ENCRYPTION_KEYS": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        "CELERY_TASK_ALWAYS_EAGER": "true",
        "RATE_LIMIT_STORAGE_URL": "memory://",
        "RATE_LIMITS_ENABLED": "true",
        "CORS_ORIGINS": "http://testserver.local",
        "ENFORCE_MFA": "true",
        "LOG_LEVEL": "WARNING",
        "EMAIL_BACKEND": "memory",
        "PAYMENT_GATEWAYS": "fake",
        "SMS_BACKEND": "memory",
        "WHATSAPP_BACKEND": "memory",
        "VOICE_BACKEND": "memory",
        "FRONTEND_URL": "https://app.test",
    }
)

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import Connection, create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import db as db_module  # noqa: E402
from app.core import email  # noqa: E402
from app.core.migrate import upgrade  # noqa: E402
from app.core.ratelimit import get_limiter  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.notifications import sms, voice, whatsapp  # noqa: E402
from app.modules.payments.gateways import FakeGateway  # noqa: E402

_admin_url = _parsed.render_as_string(hide_password=False)


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    admin = create_engine(_admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA "{TEST_SCHEMA}"'))
    try:
        upgrade()
        yield
    finally:
        db_module.engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
        admin.dispose()


@pytest.fixture
def connection() -> Iterator[Connection]:
    """One connection per test, inside a transaction that's always rolled back."""
    conn = db_module.engine.connect()
    outer = conn.begin()
    db_module.SessionLocal.configure(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield conn
    finally:
        db_module.SessionLocal.configure(bind=db_module.engine, join_transaction_mode="conservative_savepoint")
        outer.rollback()
        conn.close()


@pytest.fixture
def db(connection: Connection) -> Iterator[Session]:
    """A session for arranging and checking data. Scope it with app.core.tenancy before tenant queries."""
    with db_module.SessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
def _reset_shared_state() -> None:
    get_limiter().reset()
    email.sent_messages.clear()
    FakeGateway.reset()
    sms.sent_sms.clear()
    whatsapp.sent_whatsapp.clear()
    voice.placed_calls.clear()


@pytest.fixture(scope="session")
def app():  # type: ignore[no-untyped-def]
    return create_app()


@pytest.fixture
async def client(app, connection: Connection) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c

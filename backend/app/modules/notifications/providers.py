"""
Which SMS provider texts go through, switched at runtime by a platform admin
(`PUT /platform/providers/sms`), like the payment provider. Only providers whose keys are set
can be chosen. With SMS_BACKEND=console or memory (development, tests) nothing leaves and the
choice is ignored.

Every send reads the choice; it's cached for a few seconds so a burst of reminders doesn't
query it per message. A switch reaches the API at once and the worker within CACHE_SECONDS.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import DomainError
from app.core.http import RequestMeta
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record
from app.modules.payments.models import ProviderSetting

SMS = "sms"
CACHE_SECONDS = 30
SmsProvider = Literal["termii", "africastalking"]
ALL_SMS_PROVIDERS: tuple[SmsProvider, ...] = ("termii", "africastalking")

_cache: tuple[float, SmsProvider | None] | None = None


def configured_sms_providers() -> list[SmsProvider]:
    """Providers whose keys are set on this server, in a fixed order."""
    settings = get_settings()
    keys = {
        "termii": bool(settings.termii_api_key),
        "africastalking": bool(settings.africastalking_username and settings.africastalking_api_key),
    }
    return [p for p in ALL_SMS_PROVIDERS if keys[p]]


def _default() -> SmsProvider | None:
    configured = configured_sms_providers()
    backend = get_settings().sms_backend
    if backend in configured:
        return backend
    return configured[0] if configured else None


def active_sms_provider(session: Session) -> SmsProvider | None:
    """The saved choice if it's still configured, else SMS_BACKEND, else the first configured. None: no keys."""
    saved = session.scalar(select(ProviderSetting.active_provider).where(ProviderSetting.service == SMS))
    if saved in configured_sms_providers():
        return saved
    return _default()


def current_sms_provider() -> SmsProvider | None:
    """For sending: the active provider, read at most every CACHE_SECONDS."""
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > CACHE_SECONDS:
        with SessionLocal() as session:
            _cache = (now, active_sms_provider(session))
    return _cache[1]


def forget_cached_provider() -> None:
    global _cache
    _cache = None


@dataclass(frozen=True)
class SmsProviderView:
    service: str
    active_provider: SmsProvider | None
    available: list[SmsProvider]
    updated_at: datetime | None
    sending: bool  # False in development (SMS_BACKEND=console/memory): nothing really leaves


def sms_provider(session: Session) -> SmsProviderView:
    row = session.scalar(select(ProviderSetting).where(ProviderSetting.service == SMS))
    return SmsProviderView(
        SMS,
        active_sms_provider(session),
        configured_sms_providers(),
        row.updated_at if row else None,
        get_settings().sms_backend not in ("console", "memory"),
    )


def set_sms_provider(
    session: Session, provider: SmsProvider, *, actor: Actor, meta: RequestMeta, user_id: uuid.UUID
) -> SmsProviderView:
    configured = configured_sms_providers()
    if provider not in configured:
        available = ", ".join(configured) or "none"
        raise DomainError(f"That provider isn't configured on this platform. Available: {available}.", field="provider")
    before = active_sms_provider(session)
    session.execute(
        insert(ProviderSetting)
        .values(id=uuid.uuid4(), service=SMS, active_provider=provider, updated_by=user_id)
        .on_conflict_do_update(
            index_elements=["service"],
            set_={"active_provider": provider, "updated_by": user_id, "updated_at": datetime.now(UTC)},
        )
    )
    record(
        session,
        AuditAction.PROVIDER_CHANGED,
        school_id=None,
        actor=actor,
        meta=meta,
        changes={"service": SMS, "before": before, "after": provider},
    )
    session.commit()
    forget_cached_provider()
    return sms_provider(session)

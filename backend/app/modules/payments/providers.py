"""
Which payment provider new checkouts go through, switched at runtime by a platform admin
(`PUT /platform/providers/payment`), the way diplace does it. Only providers configured in
`PAYMENT_GATEWAYS` (keys set) can be chosen. Payments already started finish on the provider
they began with, and transfers into students' account numbers keep arriving from theirs.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.http import RequestMeta
from app.modules.audit.models import AuditAction
from app.modules.audit.service import Actor, record

from .gateways import GatewayCode, enabled_gateways
from .models import ProviderSetting

PAYMENT = "payment"


def default_payment_gateway() -> GatewayCode:
    """With no choice saved: the first configured provider that isn't the simulator, else the simulator."""
    enabled = enabled_gateways()
    return next((g for g in enabled if g is not GatewayCode.FAKE), enabled[0])


def active_payment_gateway(session: Session) -> GatewayCode:
    saved = session.scalar(select(ProviderSetting.active_provider).where(ProviderSetting.service == PAYMENT))
    enabled = enabled_gateways()
    if saved in {g.value for g in enabled}:
        return GatewayCode(saved)
    return default_payment_gateway()  # nothing saved, or a provider no longer configured


@dataclass(frozen=True)
class ProviderView:
    service: str
    active_provider: GatewayCode
    available: list[GatewayCode]
    updated_at: datetime | None


def payment_provider(session: Session) -> ProviderView:
    row = session.scalar(select(ProviderSetting).where(ProviderSetting.service == PAYMENT))
    return ProviderView(PAYMENT, active_payment_gateway(session), enabled_gateways(), row.updated_at if row else None)


def set_payment_provider(
    session: Session, provider: GatewayCode, *, actor: Actor, meta: RequestMeta, user_id: uuid.UUID
) -> ProviderView:
    if provider not in enabled_gateways():
        available = ", ".join(g.value for g in enabled_gateways())
        raise DomainError(f"That provider isn't configured on this platform. Available: {available}.", field="provider")
    before = active_payment_gateway(session)
    session.execute(
        insert(ProviderSetting)
        .values(id=uuid.uuid4(), service=PAYMENT, active_provider=provider.value, updated_by=user_id)
        .on_conflict_do_update(
            index_elements=["service"],
            set_={"active_provider": provider.value, "updated_by": user_id, "updated_at": datetime.now(UTC)},
        )
    )
    record(
        session,
        AuditAction.PROVIDER_CHANGED,
        school_id=None,
        actor=actor,
        meta=meta,
        changes={"service": PAYMENT, "before": before, "after": provider},
    )
    session.commit()
    return payment_provider(session)

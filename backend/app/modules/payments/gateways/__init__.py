"""Gateway lookup. Only gateways enabled in settings (PAYMENT_GATEWAYS) are handed out."""

from app.core.config import get_settings

from .base import (
    GatewayCode,
    GatewayError,
    GatewayRejected,
    InvalidSignature,
    PaymentGateway,
    TransactionStatus,
    Verification,
    WebhookEvent,
    WebhookKind,
)
from .fake import FakeGateway
from .flutterwave import FlutterwaveGateway
from .paystack import PaystackGateway

__all__ = [
    "FakeGateway",
    "GatewayCode",
    "GatewayError",
    "GatewayRejected",
    "InvalidSignature",
    "PaymentGateway",
    "TransactionStatus",
    "Verification",
    "WebhookEvent",
    "WebhookKind",
    "enabled_gateways",
    "get_gateway",
]


def enabled_gateways() -> list[GatewayCode]:
    return [GatewayCode(code) for code in get_settings().payment_gateways]


def get_gateway(code: GatewayCode | str) -> PaymentGateway:
    code = GatewayCode(code)
    if code not in enabled_gateways():
        raise GatewayRejected(f"Payments through {code.value.title()} aren't available.")
    if code is GatewayCode.PAYSTACK:
        return PaystackGateway()
    if code is GatewayCode.FLUTTERWAVE:
        return FlutterwaveGateway()
    return FakeGateway.shared()

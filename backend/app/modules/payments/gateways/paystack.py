"""
Paystack (https://paystack.com/docs/api/). Amounts are already in kobo.

- Subaccount: the school's bank account; `percentage_charge` is the platform's share.
- Dedicated virtual accounts (DVA): one per student ("customer"), linked to the
  school's subaccount so transfers settle to the school. Paystack may require
  extra business approval and customer identification for DVAs (PLAN §3.2).
- Webhooks are signed: HMAC-SHA512 of the raw body with the secret key,
  in `x-paystack-signature`.
"""

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.outbound import outbound_client

from .base import (
    Bank,
    Checkout,
    CustomerDetails,
    GatewayCode,
    GatewayError,
    GatewayRejected,
    InvalidSignature,
    IssuedVirtualAccount,
    ResolvedAccount,
    Subaccount,
    TransactionStatus,
    Verification,
    WebhookEvent,
    WebhookKind,
)

API = "https://api.paystack.co"
_STATUSES = {
    "success": TransactionStatus.SUCCESS,
    "failed": TransactionStatus.FAILED,
    "abandoned": TransactionStatus.FAILED,
    "reversed": TransactionStatus.FAILED,
}


class PaystackGateway:
    code = GatewayCode.PAYSTACK

    def __init__(self, secret_key: str | None = None) -> None:
        self.secret_key = secret_key if secret_key is not None else get_settings().paystack_secret_key

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.secret_key}"}
        try:
            with outbound_client("paystack", base_url=API, headers=headers) as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise GatewayError(f"Couldn't reach Paystack ({type(e).__name__}). Please try again.") from e
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 500:
            raise GatewayError("Paystack is having problems. Please try again shortly.")
        if response.status_code >= 400 or body.get("status") is False:
            raise GatewayRejected(f"Paystack: {body.get('message') or f'HTTP {response.status_code}'}")
        return body.get("data")

    def list_banks(self) -> list[Bank]:
        data = self._request("GET", "/bank", params={"country": "nigeria", "perPage": 200})
        return [Bank(code=b["code"], name=b["name"]) for b in data or [] if b.get("active", True)]

    def resolve_account(self, *, bank_code: str, account_number: str) -> ResolvedAccount:
        data = self._request("GET", "/bank/resolve", params={"account_number": account_number, "bank_code": bank_code})
        return ResolvedAccount(account_number=data["account_number"], account_name=data["account_name"])

    def create_subaccount(
        self, *, business_name: str, bank_code: str, account_number: str, platform_fee_bp: int
    ) -> Subaccount:
        data = self._request(
            "POST",
            "/subaccount",
            json={
                "business_name": business_name,
                "settlement_bank": bank_code,
                "account_number": account_number,
                "percentage_charge": platform_fee_bp / 100,
            },
        )
        return Subaccount(code=data["subaccount_code"])

    def create_virtual_account(self, *, customer: CustomerDetails, subaccount_code: str) -> IssuedVirtualAccount:
        created = self._request(
            "POST",
            "/customer",
            json={
                "email": customer.email,
                "first_name": customer.first_name,
                "last_name": customer.last_name,
                "phone": customer.phone,
                "metadata": {"student_id": customer.reference},
            },
        )
        data = self._request(
            "POST",
            "/dedicated_account",
            json={
                "customer": created["customer_code"],
                "preferred_bank": get_settings().paystack_preferred_bank,
                "subaccount": subaccount_code,
            },
        )
        return IssuedVirtualAccount(
            account_number=data["account_number"],
            account_name=data["account_name"],
            bank_name=data["bank"]["name"],
            provider_reference=created["customer_code"],
        )

    def initialize_payment(
        self, *, reference: str, amount_kobo: int, email: str, callback_url: str, subaccount_code: str, description: str
    ) -> Checkout:
        data = self._request(
            "POST",
            "/transaction/initialize",
            json={
                "reference": reference,
                "amount": amount_kobo,
                "currency": "NGN",
                "email": email,
                "callback_url": callback_url,
                "subaccount": subaccount_code,
                "bearer": "subaccount",
                "channels": ["card", "bank_transfer", "ussd"],
                "metadata": {"description": description},
            },
        )
        return Checkout(authorization_url=data["authorization_url"])

    def verify(self, reference: str) -> Verification:
        data = self._request("GET", f"/transaction/verify/{reference}") or {}
        authorization = data.get("authorization") or {}
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return Verification(
            reference=data.get("reference", reference),
            status=_STATUSES.get(data.get("status", ""), TransactionStatus.PENDING),
            amount_kobo=int(data.get("amount") or 0),
            currency=data.get("currency", ""),
            channel=data.get("channel", ""),
            receiver_account_number=authorization.get("receiver_bank_account_number")
            or (metadata or {}).get("receiver_account_number"),
            paid_at=data.get("paid_at"),
        )

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent:
        signature = headers.get("x-paystack-signature", "")
        expected = hmac.new(self.secret_key.encode(), body, hashlib.sha512).hexdigest()
        if not self.secret_key or not hmac.compare_digest(signature, expected):
            raise InvalidSignature
        try:
            event = json.loads(body)
        except ValueError as e:
            raise InvalidSignature from e
        event_type = str(event.get("event", ""))
        data = event.get("data") or {}
        reference = str(data.get("reference", "")) or None
        if event_type != "charge.success" or not reference:
            return WebhookEvent(WebhookKind.IGNORED, event_type, f"{event_type}:{reference or data.get('id', '')}")
        authorization = data.get("authorization") or {}
        return WebhookEvent(
            kind=WebhookKind.CHARGE,
            event_type=event_type,
            event_key=f"charge:{reference}",
            reference=reference,
            receiver_account_number=authorization.get("receiver_bank_account_number"),
            amount_kobo=int(data.get("amount") or 0),
            payload=event,
        )

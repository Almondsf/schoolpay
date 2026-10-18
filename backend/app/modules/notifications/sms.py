"""
Text messages.

Backends (SMS_BACKEND):
- console: logs that a message went to a masked number. Never the text: it can hold a sign-in code.
- memory: keeps messages in `sent_sms` (tests)
- termii: sends through Termii (https://developers.termii.com). The "dnd" route
  reaches numbers on Do-Not-Disturb, which matters for sign-in codes.

Callers never send directly; they go through the outbox so a message only
leaves if the transaction that caused it commits.
"""

import logging
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.config import get_settings
from app.core.outbound import outbound_client
from app.core.phone import mask_phone

logger = logging.getLogger(__name__)
TERMII_API = "https://api.ng.termii.com"


class SmsError(Exception):
    """The provider refused or couldn't be reached. The task retries."""


@dataclass(frozen=True)
class Sms:
    to: str  # E.164
    text: str
    route: Literal["dnd", "generic"] = "generic"


sent_sms: list[Sms] = []


def send_sms(message: Sms) -> None:
    settings = get_settings()
    if settings.sms_backend == "memory":
        sent_sms.append(message)
        return
    if settings.sms_backend == "console":
        logger.info("SMS (console backend) to %s, %d characters", mask_phone(message.to), len(message.text))
        return
    try:
        with outbound_client("termii", base_url=TERMII_API) as client:
            response = client.post(
                "/api/sms/send",
                json={
                    "api_key": settings.termii_api_key,
                    "to": message.to.lstrip("+"),
                    "from": settings.termii_sender_id,
                    "sms": message.text,
                    "type": "plain",
                    "channel": message.route,
                },
            )
    except httpx.HTTPError as e:
        raise SmsError(f"Couldn't reach Termii ({type(e).__name__})") from e
    if response.status_code >= 400:
        raise SmsError(f"Termii refused the message (HTTP {response.status_code})")

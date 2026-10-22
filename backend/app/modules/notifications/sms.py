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
import re
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

# The GSM 03.38 alphabet. A text that uses only these characters fits 160 to an SMS page; a single
# character outside it (the naira sign, an emoji, a curly quote from a phone keyboard) makes the
# whole text Unicode, which fits only 70 to a page and so costs two to three times as much.
GSM_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXTENDED = frozenset("^{}\\[~]|€")  # each takes two characters' room
# Phone keyboards' look-alikes, by code point: naira sign, curly quotes, dashes, ellipsis, bullets,
# times sign and unusual spaces, each to its plain SMS equivalent
_PLAIN = {
    0x20A6: "NGN ",  # owner, 2026-10-22: NGN, not N
    **dict.fromkeys((0x2018, 0x2019, 0x201A, 0x201B, 0x0060, 0x00B4), "'"),
    **dict.fromkeys((0x201C, 0x201D, 0x201E), '"'),
    **dict.fromkeys((0x2013, 0x2014, 0x2010, 0x2212, 0x2022, 0x00B7), "-"),
    0x2026: "...",
    0x00D7: "x",
    **dict.fromkeys((0x00A0, 0x2009, 0x202F, 0x0009), " "),
}


def to_gsm(text: str) -> str:
    """
    The text in the SMS alphabet: ₦ becomes NGN, curly quotes and long dashes plain ones, and anything
    else outside it (emoji) is dropped, so a message never silently switches to dearer Unicode pages.
    """
    plain = "".join(ch for ch in text.translate(_PLAIN) if ch in GSM_BASIC or ch in GSM_EXTENDED)
    return re.sub(r" {2,}", " ", plain)  # where something was dropped


def sms_pages(text: str) -> int:
    """How many SMS pages a (GSM) text is billed as: 160 characters for one, 153 each when split."""
    length = sum(2 if ch in GSM_EXTENDED else 1 for ch in text)
    return 1 if length <= 160 else -(-length // 153)


def send_sms(message: Sms) -> None:
    message = Sms(to=message.to, text=to_gsm(message.text), route=message.route)
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

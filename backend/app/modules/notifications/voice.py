"""
Phone calls to parents who prefer listening to reading: what's said, and placing the call.

Backends (VOICE_BACKEND):
- console: logs that a call went to a masked number (never what was said)
- memory: keeps calls in `placed_calls` (tests)
- africastalking: Africa's Talking Voice. We ask it to dial; when the parent answers, it
  fetches what to say from our callback (`POST /voice/africastalking`), which looks the call
  up by its session id and reads the wording fresh, so the amount is current.

Numbers are said the way people say them in English, which is how Nigerians usually
say amounts even in Pidgin. Account numbers are read digit by digit, in groups, twice.
"""

import logging
from dataclasses import dataclass
from xml.sax.saxutils import escape

import httpx

from app.core.config import get_settings
from app.core.outbound import outbound_client
from app.core.phone import mask_phone
from app.modules.students.models import Language

logger = logging.getLogger(__name__)
AFRICASTALKING_VOICE_API = "https://voice.africastalking.com"
TTS_VOICE = "en-US-Standard-C"


class VoiceError(Exception):
    """The provider refused the call or couldn't be reached. Delivery retries."""


@dataclass(frozen=True)
class Call:
    to: str  # E.164
    lines: list[str]


placed_calls: list[Call] = []


def place_call(call: Call) -> str:
    """Start the call. Returns the provider's id for it (how its callback finds the words)."""
    settings = get_settings()
    if settings.voice_backend == "memory":
        placed_calls.append(call)
        return f"memory-{len(placed_calls)}"
    if settings.voice_backend == "console":
        logger.info("Call (console backend) to %s, %d lines", mask_phone(call.to), len(call.lines))
        return ""
    try:
        with outbound_client("africastalking", base_url=AFRICASTALKING_VOICE_API) as client:
            response = client.post(
                "/call",
                headers={"apiKey": settings.africastalking_api_key, "Accept": "application/json"},
                data={
                    "username": settings.africastalking_username,
                    "from": settings.africastalking_voice_number,
                    "to": call.to,
                },
            )
    except httpx.HTTPError as e:
        raise VoiceError(f"Couldn't reach Africa's Talking ({type(e).__name__})") from e
    if response.status_code >= 400:
        raise VoiceError(f"Africa's Talking refused the call (HTTP {response.status_code})")
    entries = response.json().get("entries") or [{}]
    if entries[0].get("status") != "Queued" or not entries[0].get("sessionId"):
        raise VoiceError(f"Africa's Talking didn't queue the call ({str(entries[0].get('status'))[:40]})")
    return str(entries[0]["sessionId"])


def call_xml(lines: list[str]) -> str:
    """What Africa's Talking says when the parent answers."""
    said = "".join(f'<Say voice="{TTS_VOICE}" playBeep="false">{escape(line)}</Say>' for line in lines)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{said}</Response>'


def spoken_naira(kobo: int) -> str:
    naira, rest = divmod(kobo, 100)
    return f"{naira:,} naira" + (f" and {rest} kobo" if rest else "")


def spoken_digits(number: str) -> str:
    """'9820000768' -> '9 8 2 0, 0 0 0, 7 6 8': slow enough to write down."""
    groups = [number[:4], number[4:7], number[7:]] if len(number) == 10 else [number]
    return ", ".join(" ".join(group) for group in groups if group)


def reminder_lines(
    language: Language, *, school: str, child: str, owed_kobo: int, account_number: str | None, bank: str | None
) -> list[str]:
    amount = spoken_naira(owed_kobo)
    if language == Language.PIDGIN:
        lines = [
            f"Good day. Na {school} dey call.",
            f"We dey remind you say {amount} still remain for {child} school fees.",
        ]
        if account_number:
            digits = spoken_digits(account_number)
            lines += [
                f"To pay, send the money go {child} account number: {digits}.",
                f"Make I talk am again: {digits}.",
                f"The bank na {bank}.",
                "We don send dis number for text message too. You fit show am to any POS agent or bank.",
            ]
        else:
            lines.append("Abeg pay for the school bursary, or ask the school for the account number.")
        return [*lines, "If you don pay already, thank you, no mind dis call."]
    lines = [
        f"Good day. This is {school}.",
        f"This is a reminder that {amount} is still owed for {child}'s school fees.",
    ]
    if account_number:
        digits = spoken_digits(account_number)
        lines += [
            f"To pay, transfer to {child}'s account number: {digits}.",
            f"I repeat: {digits}.",
            f"The bank is {bank}.",
            "We have also sent this number by text message. You can show it to any POS agent or bank.",
        ]
    else:
        lines.append("Please pay at the school bursary, or ask the school for the account number.")
    return [*lines, "If you have already paid, thank you, and please ignore this call."]


def receipt_lines(language: Language, *, school: str, child: str, paid_kobo: int, owed_kobo: int) -> list[str]:
    paid, owed = spoken_naira(paid_kobo), spoken_naira(owed_kobo)
    if language == Language.PIDGIN:
        lines = [f"Good day. Na {school} dey call.", f"We don receive {paid} for {child}. Thank you well well."]
        lines.append(f"{owed} still remain." if owed_kobo > 0 else f"{child} school fees don complete.")
        return [*lines, "We go send the receipt for text message."]
    lines = [f"Good day. This is {school}.", f"We have received {paid} for {child}. Thank you."]
    lines.append(f"{owed} is still owed." if owed_kobo > 0 else f"{child}'s fees are fully paid.")
    return [*lines, "The receipt is on its way by text message."]

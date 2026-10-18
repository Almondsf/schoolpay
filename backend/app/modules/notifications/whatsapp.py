"""
WhatsApp through Meta's Cloud API (https://developers.facebook.com/docs/whatsapp/cloud-api).

Business-started conversations must use approved templates, so this sends a
template by name with body parameters. Backends (WHATSAPP_BACKEND):
- off: WhatsApp isn't used; messages go by SMS, then email
- console: logs a masked number and the template name
- memory: keeps messages in `sent_whatsapp` (tests)
- cloud: the Cloud API (needs WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID)
"""

import logging
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.outbound import outbound_client
from app.core.phone import mask_phone

logger = logging.getLogger(__name__)


class WhatsAppError(Exception):
    """Not delivered (no WhatsApp account on the number, template problem, or provider down)."""


@dataclass(frozen=True)
class WhatsAppTemplateMessage:
    to: str  # E.164
    template: str
    parameters: list[str]


sent_whatsapp: list[WhatsAppTemplateMessage] = []


def enabled() -> bool:
    return get_settings().whatsapp_backend != "off"


def send_template(message: WhatsAppTemplateMessage) -> str:
    """Send; returns the provider's message id. Raises WhatsAppError on failure."""
    settings = get_settings()
    if settings.whatsapp_backend == "memory":
        sent_whatsapp.append(message)
        return f"memory-{len(sent_whatsapp)}"
    if settings.whatsapp_backend == "console":
        logger.info("WhatsApp (console backend) %s to %s", message.template, mask_phone(message.to))
        return "console"
    if settings.whatsapp_backend != "cloud":
        raise WhatsAppError("WhatsApp is turned off.")
    body = {
        "messaging_product": "whatsapp",
        "to": message.to.lstrip("+"),
        "type": "template",
        "template": {
            "name": message.template,
            "language": {"code": settings.whatsapp_language},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": value or "-"} for value in message.parameters]}
            ],
        },
    }
    try:
        with outbound_client(
            "whatsapp",
            base_url=f"https://graph.facebook.com/{settings.whatsapp_api_version}",
            headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
        ) as client:
            response = client.post(f"/{settings.whatsapp_phone_number_id}/messages", json=body)
    except httpx.HTTPError as e:
        raise WhatsAppError(f"Couldn't reach WhatsApp ({type(e).__name__})") from e
    if response.status_code >= 400:
        raise WhatsAppError(f"WhatsApp refused the message (HTTP {response.status_code})")
    messages = response.json().get("messages") or [{}]
    return str(messages[0].get("id", ""))

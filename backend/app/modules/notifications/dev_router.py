"""
Development and browser tests only: read the last text message sent to a number.

Exists only with SMS_BACKEND=memory, which settings refuse in production, so
sign-in codes can never be read this way on a live system.
"""

from fastapi import APIRouter, Query

from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.phone import InvalidPhone, normalize_phone

from .sms import sent_sms

router = APIRouter(prefix="/dev/sms", tags=["development"], include_in_schema=False)


@router.get("/latest")
def latest(to: str = Query(max_length=25)) -> dict[str, str]:
    settings = get_settings()
    if settings.is_production or settings.sms_backend != "memory":
        raise NotFound("Not found.")
    try:
        number = normalize_phone(to)
    except InvalidPhone as e:
        raise NotFound("Not found.") from e
    message = next((m for m in reversed(sent_sms) if m.to == number), None)
    if message is None:
        raise NotFound("No message.")
    return {"text": message.text}

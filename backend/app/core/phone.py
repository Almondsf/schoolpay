"""
Phone numbers, normalised to E.164 (+2348031234567).

Schools' spreadsheets hold numbers in every shape: 08031234567, 8031234567,
2348031234567, +234 803 123 4567, 0803-123-4567. Normalising once on the way
in means lookups (parent sign-in, duplicate guardians) and messaging work on
one form.
"""

import re

_SEPARATORS = re.compile(r"[\s\-().]")
# Nigerian mobile and fixed numbers: 10 significant digits after the country code
_NG_NATIONAL = re.compile(r"^0?([1-9]\d{9})$")
_INTERNATIONAL = re.compile(r"^\+[1-9]\d{7,14}$")


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: str, default_country: str = "234") -> str:
    value = _SEPARATORS.sub("", (raw or "").strip())
    if value.startswith("00"):
        value = "+" + value[2:]
    if value.startswith("+"):
        if value.startswith("+2340"):  # +234 0803... is a common mistake
            value = "+234" + value[5:]
        if not _INTERNATIONAL.match(value):
            raise InvalidPhone(f"{raw!r} isn't a valid phone number.")
        return value
    if value.startswith(default_country) and len(value) == len(default_country) + 10:
        return "+" + value
    match = _NG_NATIONAL.match(value)
    if match:
        return f"+{default_country}{match.group(1)}"
    raise InvalidPhone(f"{raw!r} isn't a valid phone number.")


def mask_phone(e164: str) -> str:
    """+2348031234567 -> +234 *** *** 4567 (for places that don't need the full number)."""
    return f"{e164[:4]} *** *** {e164[-4:]}" if len(e164) > 8 else "***"

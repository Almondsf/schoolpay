"""
Message wording.

SMS and email use the school's own wording (or the defaults below), filled
from a fixed set of placeholders. A school's template can only use these
names; anything else is refused when it's saved.

WhatsApp is different: a business may only start a WhatsApp conversation with a
template Meta has approved, so WhatsApp sends the platform's approved
templates (`WHATSAPP_TEMPLATES`) with these values as parameters, in the order
given by `WHATSAPP_PARAMETERS`. Register templates with matching parameters in
WhatsApp Manager (see docs/DEPLOYMENT.md).
"""

import string
from typing import Final

from app.core.errors import DomainError

from .models import MessageKind

PLACEHOLDERS: Final = {
    "parent_name": "The parent's name",
    "student_name": "The child's full name",
    "school_name": "Your school's name",
    "amount": "The amount paid or billed, e.g. ₦150,000.00",
    "balance": "What's still owed for the child",
    "term": "The term, e.g. 2026/2027 First term",
    "due_date": "When the bill is due",
    "due_phrase": "e.g. ' (due 30 Oct)' or ' (was due 30 Oct)'",
    "receipt_number": "The receipt number",
    "verify_url": "Link to check the receipt is genuine",
    "pay_to": "How to pay: the child's account number and bank, when they have one",
    "portal_url": "Link to the parent portal",
    "pay_link": "The parent's own link to pay for all their children, no sign-in needed",
}

DEFAULTS: Final = {
    MessageKind.RECEIPT: (
        "Dear {parent_name}, {school_name} has received {amount} for {student_name}. Receipt no. {receipt_number}. "
        "Outstanding balance: {balance}. Check this receipt at {verify_url}"
    ),
    MessageKind.FEE_NOTICE: (
        "Dear {parent_name}, {student_name}'s {term} fees at {school_name} are {amount}{due_phrase}. "
        "{pay_to} Or pay online for all your children: {pay_link}. Thank you."
    ),
    MessageKind.REMINDER: (
        "Dear {parent_name}, a gentle reminder from {school_name}: {balance} is outstanding for {student_name}"
        "{due_phrase}. {pay_to} Pay online: {pay_link}. If you have already paid, thank you, and please "
        "ignore this message."
    ),
}

WHATSAPP_PARAMETERS: Final = {
    MessageKind.RECEIPT: [
        "parent_name",
        "amount",
        "student_name",
        "school_name",
        "receipt_number",
        "balance",
        "verify_url",
    ],
    MessageKind.FEE_NOTICE: ["parent_name", "student_name", "term", "school_name", "amount", "pay_to", "pay_link"],
    MessageKind.REMINDER: ["parent_name", "school_name", "balance", "student_name", "pay_to", "pay_link"],
}

MAX_LENGTH = 640  # four SMS segments


def validate(body: str) -> str:
    body = body.strip()
    if not body:
        raise DomainError("Write the message.", field="body")
    if len(body) > MAX_LENGTH:
        raise DomainError(f"Keep it under {MAX_LENGTH} characters.", field="body")
    try:
        names = {name for _, name, spec, conv in string.Formatter().parse(body) if name is not None}
    except ValueError as e:
        raise DomainError("Check the curly brackets: use {placeholder} exactly.", field="body") from e
    unknown = sorted(n for n in names if n not in PLACEHOLDERS)
    if unknown:
        raise DomainError(f"Unknown placeholder: {', '.join('{' + n + '}' for n in unknown)}.", field="body")
    return body


def render(body: str, values: dict[str, str]) -> str:
    """Fill a (validated) template. Missing values become empty rather than failing a send."""
    return string.Formatter().vformat(body, (), {name: values.get(name, "") for name in PLACEHOLDERS})

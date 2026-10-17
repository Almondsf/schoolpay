"""
Logging setup with a redaction filter.

Rule: logs never contain BVNs, OTPs, passwords, tokens or full account
numbers. Code should not log them in the first place; this filter is the
safety net. It masks `key=value` / `"key": "value"` pairs for sensitive keys
and any long digit runs (account numbers, BVNs, card numbers).
"""

import logging
import re
import sys

from app.core.redaction import SENSITIVE_FRAGMENTS, SENSITIVE_KEYS

_KEY_NAMES = "|".join(
    [r"\w*(?:" + "|".join(SENSITIVE_FRAGMENTS) + r")\w*", *sorted(SENSITIVE_KEYS, key=len, reverse=True)]
)
_KEY_VALUE = re.compile(
    r"""(?P<key>(?<![\w-])["']?(?:""" + _KEY_NAMES + r""")["']?\s*[:=]\s*)(?P<value>"[^"]*"|'[^']*'|[^\s,;&}]+)""",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)
# 10+ consecutive digits: NUBAN account numbers (10), BVN/NIN (11), card PANs
# (not after a "." so decimal fractions like timings stay readable)
_LONG_DIGITS = re.compile(r"(?<![\d.])(\d{6,})(\d{4})(?!\d)")


def redact(message: str) -> str:
    message = _BEARER.sub(r"\1[REDACTED]", message)  # first: key=value alone would stop at "Bearer"
    message = _KEY_VALUE.sub(
        lambda m: m.group(0) if m.group("value").lower() == "bearer" else f"{m.group('key')}[REDACTED]",
        message,
    )
    return _LONG_DIGITS.sub(lambda m: "*" * len(m.group(1)) + m.group(2), message)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # a bad format string shouldn't drop the record
            return True
        redacted = redact(rendered)
        if redacted != rendered:
            record.msg, record.args = redacted, None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Tracebacks are formatted late, so redact the final text too
        return redact(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Uvicorn's access log prints full URLs; ours never carry secrets in query strings, but redact anyway
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

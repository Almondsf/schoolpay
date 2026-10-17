"""
What counts as sensitive, in one place. Used by log redaction, outbound API
logging, audit `changes` and stored gateway payloads, so a key added here is
hidden everywhere at once.
"""

from typing import Any

REDACTED = "[REDACTED]"

# Matched case-insensitively against whole dict keys
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "otp",
        "otp_code",
        "verification_code",
        "recovery_code",
        "recovery_codes",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "mfa_ticket",
        "ticket",
        "secret",
        "secret_key",
        "mfa_secret",
        "api_key",
        "authorization",
        "cookie",
        "bvn",
        "nin",
        "account_number",
        "accountnumber",
        "card",
        "card_number",
        "pan",
        "cvv",
        "pin",
        "authorization_code",
        "provisioning_uri",
    }
)

# Substrings that mark a key sensitive wherever they appear (e.g. "paystack_secret_key")
SENSITIVE_FRAGMENTS = ("password", "secret", "token", "otp", "bvn", "account_number", "api_key")


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_KEYS or any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS)


def redact(obj: Any, extra_keys: frozenset[str] = frozenset()) -> Any:
    """Recursively replace the values of sensitive keys. `extra_keys` adds keys for one call only."""
    if isinstance(obj, dict):
        return {
            k: REDACTED
            if (isinstance(k, str) and (is_sensitive_key(k) or k.lower() in extra_keys))
            else redact(v, extra_keys)
            for k, v in obj.items()
        }
    if isinstance(obj, list | tuple):
        return [redact(item, extra_keys) for item in obj]
    return obj


def truncate(value: str, limit: int = 4000) -> str:
    return value if len(value) <= limit else value[:limit] + "...(truncated)"

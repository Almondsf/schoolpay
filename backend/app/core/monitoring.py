"""
Error monitoring with Sentry, only when SENTRY_DSN is set.

Children's and parents' data must not leave the platform in an error report,
so every event goes through `scrub()` before it is sent:

- no request bodies, cookies or headers (sign-in codes, tokens and personal
  details travel in those), and no user details (`send_default_pii=False`);
- query strings dropped (they can hold search terms such as names);
- every remaining string (messages, exception values, breadcrumbs, local
  variables) passes the same redaction as the logs: sensitive keys, bearer
  tokens, and long digit runs such as account numbers and BVNs.
"""

from typing import Any

from app.core.config import Settings
from app.core.logging import redact as redact_text
from app.core.redaction import REDACTED, is_sensitive_key

_DROP_REQUEST_KEYS = ("data", "cookies", "headers", "query_string", "env")


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if isinstance(k, str) and is_sensitive_key(k) else _clean(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def scrub(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    request = event.get("request")
    if isinstance(request, dict):
        for key in _DROP_REQUEST_KEYS:
            request.pop(key, None)
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
    event.pop("user", None)
    cleaned: dict[str, Any] = _clean(event)
    return cleaned


def init_monitoring(settings: Settings, *, component: str) -> bool:
    """Start Sentry for the API or the worker. Returns whether it's on."""
    if not settings.sentry_dsn:
        return False
    import sentry_sdk  # imported only when used

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.release or None,
        server_name=component,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=scrub,  # type: ignore[arg-type]
        before_send_transaction=scrub,  # type: ignore[arg-type]
    )
    return True

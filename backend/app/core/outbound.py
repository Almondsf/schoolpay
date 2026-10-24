"""
HTTP client for third-party APIs (payment gateways, messaging providers).

Every request and response is logged once, with secrets removed: the
Authorization header is never logged, JSON bodies pass through the shared
redaction rules, and bodies are truncated. 2xx/3xx log at INFO, 4xx at
WARNING, 5xx at ERROR. Providers explain failures in the response body, so it's
kept (redacted) for diagnosis.

    with outbound_client("paystack", base_url=..., headers={"Authorization": ...}) as client:
        client.get("/transaction/verify/REF")

Connections are reused: there is one long-lived httpx client (with its own connection pool) per
provider and base URL, created on first use. Headers such as API keys are added to each request
rather than stored on the shared client, so the cache never grows. A forked child process (Celery's
workers) starts with an empty cache instead of sharing its parent's sockets, and `close_all()`
closes everything at shutdown. httpx clients are safe to share between threads.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from app.core.redaction import redact, truncate

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
# Per provider: enough for a batch job and a few requests at once, idle connections kept 30 s
LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0)


def _body_preview(raw: bytes, extra_keys: frozenset[str] = frozenset()) -> str:
    if not raw:
        return ""
    try:
        return truncate(json.dumps(redact(json.loads(raw), extra_keys)), 2000)
    except (ValueError, UnicodeDecodeError):
        return "<non-JSON body>"


def _hooks(provider: str, redact_keys: frozenset[str] = frozenset()) -> dict[str, list[Any]]:
    log = logging.getLogger(f"outbound.{provider}")

    def on_request(request: httpx.Request) -> None:
        request.extensions["started"] = time.perf_counter()

    def on_response(response: httpx.Response) -> None:
        response.read()
        started = response.request.extensions.get("started")
        elapsed = f"{(time.perf_counter() - started) * 1000:.0f}ms" if started else "?"
        line = (
            f"{provider} {response.request.method} {response.request.url.path} -> {response.status_code} "
            f"in {elapsed} body={_body_preview(response.content, redact_keys)}"
        )
        level = (
            logging.ERROR
            if response.status_code >= 500
            else logging.WARNING
            if response.status_code >= 400
            else logging.INFO
        )
        log.log(level, line)

    return {"request": [on_request], "response": [on_response]}


_clients: dict[tuple[str, str, frozenset[str]], httpx.Client] = {}
_lock = threading.Lock()


def _shared(provider: str, base_url: str, redact_keys: frozenset[str]) -> httpx.Client:
    key = (provider, base_url, redact_keys)
    client = _clients.get(key)
    if client is None:
        with _lock:  # two threads asking at once must not both build (and leak) a client
            client = _clients.get(key)
            if client is None:
                client = httpx.Client(
                    base_url=base_url, timeout=TIMEOUT, limits=LIMITS, event_hooks=_hooks(provider, redact_keys)
                )
                _clients[key] = client
    return client


class ProviderClient:
    """The shared client for one provider, sending this caller's headers (API keys) with every request."""

    def __init__(self, client: httpx.Client, headers: dict[str, str]) -> None:
        self._client, self._headers = client, headers

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        kwargs["headers"] = {**self._headers, **(kwargs.get("headers") or {})}
        return self._client.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)


@contextmanager
def outbound_client(
    provider: str,
    *,
    base_url: str,
    headers: dict[str, str] | None = None,
    redact_keys: frozenset[str] = frozenset(),
) -> Iterator[ProviderClient]:
    """
    The provider's shared client (its connections stay open for the next call). `redact_keys` hides
    more of this provider's replies, e.g. the parents' numbers an SMS provider echoes.
    """
    yield ProviderClient(_shared(provider, base_url, redact_keys), headers or {})


def close_all() -> None:
    """Close every shared client (application or worker shutdown)."""
    with _lock:
        clients = list(_clients.values())
        _clients.clear()
    for client in clients:
        client.close()


def _forget_after_fork() -> None:
    # The child must not use the parent's sockets; just drop the references (closing them here could
    # disturb the parent's connections) and let the child build its own.
    global _lock
    _clients.clear()
    _lock = threading.Lock()


_register_at_fork = getattr(os, "register_at_fork", None)  # Linux (production); Windows has no fork
if _register_at_fork is not None:
    _register_at_fork(after_in_child=_forget_after_fork)

"""
HTTP client for third-party APIs (payment gateways, messaging providers).

Every request and response is logged once, with secrets removed: the
Authorization header is never logged, JSON bodies pass through the shared
redaction rules, and bodies are truncated. 2xx/3xx log at INFO, 4xx at
WARNING, 5xx at ERROR. Providers explain failures in the response body, so it's
kept (redacted) for diagnosis.

    with outbound_client("paystack", base_url=...) as client:
        client.get("/transaction/verify/REF")
"""

import json
import logging
import time
from typing import Any

import httpx

from app.core.redaction import redact, truncate

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _body_preview(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        return truncate(json.dumps(redact(json.loads(raw))), 2000)
    except (ValueError, UnicodeDecodeError):
        return "<non-JSON body>"


def _hooks(provider: str) -> dict[str, list[Any]]:
    log = logging.getLogger(f"outbound.{provider}")

    def on_request(request: httpx.Request) -> None:
        request.extensions["started"] = time.perf_counter()

    def on_response(response: httpx.Response) -> None:
        response.read()
        started = response.request.extensions.get("started")
        elapsed = f"{(time.perf_counter() - started) * 1000:.0f}ms" if started else "?"
        line = (
            f"{provider} {response.request.method} {response.request.url.path} -> {response.status_code} "
            f"in {elapsed} body={_body_preview(response.content)}"
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


def outbound_client(provider: str, *, base_url: str, headers: dict[str, str] | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers=headers or {}, timeout=TIMEOUT, event_hooks=_hooks(provider))

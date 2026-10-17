"""Request helpers and HTTP middleware (security headers, request IDs)."""

import secrets
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings


def client_ip(request: Request) -> str:
    """
    The caller's IP. X-Forwarded-For is trusted only for the NUM_PROXIES hops we
    actually run behind; otherwise any client could spoof it.
    """
    num_proxies = get_settings().num_proxies
    forwarded = request.headers.get("x-forwarded-for")
    if num_proxies and forwarded:
        addresses = [a.strip() for a in forwarded.split(",") if a.strip()]
        if addresses:
            return addresses[-min(num_proxies, len(addresses))]
    return request.client.host if request.client else ""


def user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:255]


@dataclass(frozen=True)
class RequestMeta:
    """Who's calling from where; recorded on audit entries and sessions."""

    ip: str = ""
    user_agent: str = ""
    request_id: str = ""


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip=client_ip(request)[:45],
        user_agent=user_agent(request),
        request_id=getattr(request.state, "request_id", ""),
    )


class BodyLimitMiddleware:
    """
    Rejects request bodies over `max_bytes` with 413, whether or not the client
    sends Content-Length (chunked uploads are counted as they stream in).
    Spreadsheet imports set the largest legitimate size.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.max_bytes + 1
                if declared > self.max_bytes:
                    await _too_large(scope, receive, send)
                    return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _too_large(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
    body = {
        "error": {"code": "payload_too_large", "message": "Request body is too large.", "field": None, "details": None}
    }
    await JSONResponse(body, status_code=413)(scope, receive, send)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Headers for a JSON API: nothing here should ever be framed, sniffed or cached by proxies."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = request.headers.get("x-request-id", "")[:64] or secrets.token_hex(8)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers["X-Request-ID"] = request.state.request_id
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        response.headers.setdefault("Cache-Control", "no-store")
        if get_settings().is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
        return response

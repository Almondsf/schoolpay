"""
Domain errors and the single JSON error shape the API returns:

    {"error": {"code": "not_found", "message": "Student not found.", "field": null}}

Services raise these; routers don't catch them. Unexpected exceptions become a
generic 500 with no internals in the body (details go to the log).
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    field: str | None = None
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class DomainError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "invalid"

    def __init__(self, message: str, *, field: str | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        if code:
            self.code = code


class NotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class Conflict(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class NotAuthenticated(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"


class PermissionDenied(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class RateLimited(DomainError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many attempts. Please wait and try again.")
        self.retry_after = retry_after


class ServiceUnavailable(DomainError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "unavailable"


def _body(code: str, message: str, field: str | None = None, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "field": field, "details": details}}


# Shared OpenAPI declaration for routers: every error uses ErrorResponse
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 422, 429)
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_request: Request, exc: DomainError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
        return JSONResponse(_body(exc.code, exc.message, exc.field), exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        # Never echo submitted values back (they may be passwords or OTPs)
        details = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in errors]
        first = errors[0] if errors else {}
        loc = [str(p) for p in first.get("loc", ()) if p not in ("body", "query", "path")]
        return JSONResponse(
            _body(
                "validation_error",
                first.get("msg", "Invalid request."),
                ".".join(loc) or None,
                jsonable_encoder(details),
            ),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(_body("server_error", "Something went wrong. Please try again."), 500)

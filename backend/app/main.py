"""
App factory. Run with:  uvicorn app.main:app --port 8002

All routes live under /api/v1. Interactive docs (/api/docs) are served outside
production only; the schema is still exported for the frontend's generated
types with `python -m app.openapi`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import install_error_handlers
from app.core.http import BodyLimitMiddleware, SecurityHeadersMiddleware
from app.core.logging import configure_logging
from app.models import load_all_models
from app.modules.accounts.router import auth_router, staff_router
from app.modules.audit.router import audit_router, platform_audit_router
from app.modules.health.router import router as health_router
from app.modules.schools.router import platform_schools_router, school_router

API_PREFIX = "/api/v1"


def build_api_router() -> APIRouter:
    api = APIRouter(prefix=API_PREFIX)
    for router in (
        health_router,
        auth_router,
        school_router,
        staff_router,
        audit_router,
        platform_schools_router,
        platform_audit_router,
    ):
        api.include_router(router)
    return api


def operation_id(route: APIRoute) -> str:
    """Readable operation IDs (e.g. `health_liveness`) for the generated frontend types."""
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    # Close pooled connections on shutdown so restarts don't leave idle sessions on the server
    engine.dispose()


def create_app() -> FastAPI:
    load_all_models()
    settings = get_settings()
    configure_logging(settings.log_level)
    docs = not settings.is_production
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        debug=settings.debug,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
        lifespan=lifespan,
        generate_unique_id_function=operation_id,
    )
    install_error_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,  # the refresh token travels in an httpOnly cookie
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
    app.include_router(build_api_router())
    return app


app = create_app()

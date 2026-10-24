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

from app.core import outbound
from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import install_error_handlers
from app.core.http import BodyLimitMiddleware, SecurityHeadersMiddleware
from app.core.logging import configure_logging
from app.core.monitoring import init_monitoring
from app.models import load_all_models
from app.modules.accounts.router import auth_router, staff_router
from app.modules.audit.router import audit_router, platform_audit_router
from app.modules.expenses.router import router as expenses_router
from app.modules.fees.router import router as fees_router
from app.modules.health.router import router as health_router
from app.modules.notifications.dev_router import router as dev_sms_router
from app.modules.notifications.router import parent_router as parent_messaging_router
from app.modules.notifications.router import router as messaging_router
from app.modules.notifications.router import voice_router
from app.modules.parents.router import auth_router as parent_auth_router
from app.modules.parents.router import router as parent_router
from app.modules.payments.family_router import public_router as public_family_router
from app.modules.payments.family_router import router as family_router
from app.modules.payments.router import dev_router, webhook_router
from app.modules.payments.router import router as payments_router
from app.modules.platform.router import public_router as signup_router
from app.modules.platform.router import router as platform_router
from app.modules.platform.router import school_router as subscription_router
from app.modules.privacy.router import parent_router as parent_privacy_router
from app.modules.privacy.router import router as privacy_router
from app.modules.receipts.router import public_router as public_receipts_router
from app.modules.receipts.router import router as receipts_router
from app.modules.reports.router import router as reports_router
from app.modules.schools.academics_router import router as academics_router
from app.modules.schools.router import platform_schools_router, school_router
from app.modules.students.router import router as students_router

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
        academics_router,
        students_router,
        fees_router,
        payments_router,
        expenses_router,
        receipts_router,
        parent_auth_router,
        parent_router,
        messaging_router,
        parent_messaging_router,
        reports_router,
        privacy_router,
        parent_privacy_router,
        platform_router,
        signup_router,
        subscription_router,
        public_receipts_router,
        family_router,
        public_family_router,
        webhook_router,
        voice_router,
    ):
        api.include_router(router)
    # Development and browser-test helpers (simulated transfers, reading texted codes) don't exist in
    # production at all; each also refuses on its own if its fake backend isn't configured
    if not get_settings().is_production:
        api.include_router(dev_router)
        api.include_router(dev_sms_router)
    return api


def operation_id(route: APIRoute) -> str:
    """Readable operation IDs (e.g. `health_liveness`) for the generated frontend types."""
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    # Close pooled connections on shutdown so restarts don't leave idle sessions on the server
    engine.dispose()
    outbound.close_all()


def create_app() -> FastAPI:
    load_all_models()
    settings = get_settings()
    configure_logging(settings.log_level)
    init_monitoring(settings, component="api")
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

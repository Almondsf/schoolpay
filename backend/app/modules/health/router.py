import logging
from collections.abc import Callable
from typing import Literal

import redis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


class Health(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ok", "unavailable"]
    database: bool
    redis: bool


@router.get("", response_model=Health, summary="Liveness: the API process is up")
def liveness() -> Health:
    return Health(status="ok")


@router.get(
    "/ready",
    response_model=Readiness,
    summary="Readiness: database and Redis reachable",
    responses={503: {"model": Readiness}},
)
def readiness(session: Session = Depends(get_db)) -> Readiness | JSONResponse:
    settings = get_settings()
    database = _check("database", lambda: session.execute(text("SELECT 1")))
    if settings.celery_task_always_eager:
        redis_ok = True  # no broker in use (development/test)
    else:
        redis_ok = _check("redis", lambda: redis.Redis.from_url(settings.redis_url, socket_timeout=2).ping())
    result = Readiness(status="ok" if database and redis_ok else "unavailable", database=database, redis=redis_ok)
    if result.status != "ok":
        return JSONResponse(result.model_dump(), status_code=503)
    return result


def _check(name: str, probe: Callable[[], object]) -> bool:
    try:
        probe()
    except Exception as e:
        logger.warning("Readiness check failed for %s: %s", name, e.__class__.__name__)
        return False
    return True

"""
Engine, session factory and the request session dependency.
Services own transactions: they call `session.commit()` themselves.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.base import Base
from app.core.config import get_settings

__all__ = ["Base", "SessionLocal", "engine", "get_db"]


def _make_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """
    One session per request. It starts unscoped: the auth dependencies scope it
    to the caller's school (or mark it platform-wide) before any tenant data
    can be read. See app.core.tenancy.
    """
    with SessionLocal() as session:
        yield session

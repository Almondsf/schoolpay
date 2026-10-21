"""
Engine, session factory and the request session dependency.
Services own transactions: they call `session.commit()` themselves.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.base import Base
from app.core.config import Settings, get_settings

__all__ = ["Base", "SessionLocal", "engine", "get_db"]


def make_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # A failed statement's error carries its bound values (emails, phone numbers). Keep them out of
        # production logs and error reports; development keeps them for debugging.
        hide_parameters=settings.is_production,
    )


engine = make_engine(get_settings())
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """
    One session per request. It starts unscoped: the auth dependencies scope it
    to the caller's school (or mark it platform-wide) before any tenant data
    can be read. See app.core.tenancy.
    """
    with SessionLocal() as session:
        yield session

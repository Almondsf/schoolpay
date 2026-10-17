"""
Alembic environment. Prefer `python -m app.core.migrate` (advisory lock,
migration role); plain `alembic` commands work too for development.
Needs only MIGRATION_DATABASE_URL or DATABASE_URL, never the API's secrets.
"""

from typing import Any, Literal

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from app.core.base import Base
from app.core.crypto import EncryptedString
from app.core.migrate import migration_url
from app.models import load_all_models

load_all_models()
config = context.config
target_metadata = Base.metadata


def _render_item(type_: str, obj: Any, autogen_context: Any) -> str | Literal[False]:
    # Encrypted columns are plain text in the database; keep app types out of migrations
    if type_ == "type" and isinstance(obj, EncryptedString):
        return "sa.Text()"
    return False


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        render_item=_render_item,
        **kwargs,  # type: ignore[arg-type]
    )


def run_migrations_offline() -> None:
    _configure(url=migration_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # handed over by app.core.migrate, which holds the lock
        _run(connection)
        return
    engine = create_engine(migration_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

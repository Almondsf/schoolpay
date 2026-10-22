"""
Apply migrations once per release, safely:

    python -m app.core.migrate

- Uses MIGRATION_DATABASE_URL: the migration role owns the schema (DDL);
  the API's DATABASE_URL role only reads and writes rows. In development the
  runner falls back to DATABASE_URL when no migration URL is set.
- Holds a Postgres advisory lock, so two releases (Compose restarts,
  Kubernetes Jobs, a manual run) never migrate at the same time.
- Needs no runtime secrets: only the database URL.
"""

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.core.config import normalize_database_url

# Stable across releases ("SCHLPAY" in hex). The session lock is released on disconnect too.
MIGRATION_LOCK_ID = 0x5343484C504159
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def migration_url() -> str:
    env_file = Path(os.environ.get("WARDPAY_ENV_FILE", BACKEND_ROOT / ".env"))
    file_values = dotenv_values(env_file) if env_file.is_file() else {}
    url = (
        os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or file_values.get("MIGRATION_DATABASE_URL")
        or file_values.get("DATABASE_URL")
    )
    if not url:
        raise SystemExit("Set MIGRATION_DATABASE_URL (or DATABASE_URL in development) to migrate.")
    return normalize_database_url(url)


def upgrade(revision: str = "head") -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    engine = create_engine(migration_url(), poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET lock_timeout = '120s'"))
            connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
            connection.commit()
            config.attributes["connection"] = connection
            try:
                command.upgrade(config, revision)
            finally:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
                connection.commit()
    finally:
        engine.dispose()


if __name__ == "__main__":
    upgrade(sys.argv[1] if len(sys.argv) > 1 else "head")

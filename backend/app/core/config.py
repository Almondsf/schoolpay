"""
Settings, read once from the environment (and `.env` in development).

Every variable is documented in `.env.example`. Defaults are the safe ones:
DEBUG is off, cookies are secure, CORS allows nothing. Development and test
loosen what they need explicitly.
"""

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]


def normalize_database_url(value: str) -> str:
    """Accept the common postgres:// spellings and pin the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+psycopg://" + value.removeprefix(prefix)
    if value.startswith("postgresql+psycopg://"):
        return value
    raise ValueError("Database URLs must be PostgreSQL (postgresql+psycopg://...)")


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Tests point SCHOOLPAY_ENV_FILE at a missing file so a developer's .env never leaks in
        env_file=os.environ.get("SCHOOLPAY_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = "production"
    debug: bool = False
    app_name: str = "SchoolPay"

    # Signing key for JWTs and other short-lived tokens. Rotate by deploying a new value
    # (all sessions end). Never reuse it as the field-encryption key.
    secret_key: str = Field(min_length=32)

    # Runtime role: DML only. Migrations use MIGRATION_DATABASE_URL (see app.core.migrate).
    database_url: str
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    redis_url: str = "redis://localhost:6379/0"
    # Run Celery tasks in-process (development without Redis, and tests)
    celery_task_always_eager: bool = False
    # Rate-limit counters; use the Redis URL in production so limits hold across workers
    rate_limit_storage_url: str = "memory://"
    rate_limits_enabled: bool = True

    # Fernet keys (url-safe base64, 32 bytes). The first encrypts; all decrypt (rotation).
    field_encryption_keys: Annotated[list[str], NoDecode] = Field(min_length=1)

    cors_origins: Annotated[list[str], NoDecode] = []
    frontend_url: str = "http://localhost:5175"
    # Number of reverse proxies in front of the API whose X-Forwarded-For we trust
    num_proxies: int = Field(default=0, ge=0, le=5)
    # Largest request body accepted (spreadsheet imports are the biggest legitimate uploads)
    max_request_body_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)

    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    mfa_issuer: str = "SchoolPay"
    # Proprietors and bursars must enrol in two-factor sign-in before doing anything else
    enforce_mfa: bool = True

    # Rate limits on sign-in endpoints ("N/period"; see app.core.ratelimit)
    login_rate_per_ip: str = "20/minute"
    login_rate_per_account: str = "10/hour"
    password_reset_rate: str = "5/hour"

    # Outgoing email: console (log only), memory (tests) or smtp
    email_backend: Literal["console", "memory", "smtp"] = "console"
    email_from: str = "SchoolPay <no-reply@schoolpay.ng>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # How long invite and password-reset links stay valid
    password_link_hours: int = Field(default=48, ge=1, le=168)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("cors_origins", "field_encryption_keys", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("database_url")
    @classmethod
    def _postgres_driver(cls, value: str) -> str:
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _production_is_strict(self) -> "Settings":
        if self.environment == "production":
            if self.debug:
                raise ValueError("DEBUG must be off in production")
            if self.celery_task_always_eager:
                raise ValueError("CELERY_TASK_ALWAYS_EAGER must be off in production")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS can't be '*' in production")
            if self.rate_limit_storage_url.startswith("memory"):
                raise ValueError("RATE_LIMIT_STORAGE_URL must be shared storage (Redis) in production")
            if self.email_backend == "memory":
                raise ValueError("EMAIL_BACKEND=memory is for tests only")
            if self.email_backend == "smtp" and not self.smtp_host:
                raise ValueError("SMTP_HOST is required when EMAIL_BACKEND=smtp")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        # Browsers drop Secure cookies on plain-http localhost, so only development relaxes it
        return self.environment != "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment

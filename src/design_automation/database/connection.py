"""Environment-backed SQLAlchemy connection and session setup."""

import math

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from design_automation.core import (
    DATABASE_CONNECT_TIMEOUT_SECONDS,
    DATABASE_POOL_RECYCLE_SECONDS,
    DATABASE_POOL_TIMEOUT_SECONDS,
    DATABASE_URL,
)


class DatabaseConfigurationError(ValueError):
    """Raised when application database configuration is missing."""


def build_engine(database_url: str | None = DATABASE_URL, **kwargs: object) -> Engine:
    """Create an engine without embedding credentials in application code."""

    if not database_url:
        raise DatabaseConfigurationError("DATABASE_URL must be configured")
    options = dict(kwargs)
    if database_url.startswith("postgresql"):
        options.setdefault("pool_pre_ping", True)
        options.setdefault("pool_timeout", DATABASE_POOL_TIMEOUT_SECONDS)
        options.setdefault("pool_recycle", DATABASE_POOL_RECYCLE_SECONDS)
        connect_args = dict(options.pop("connect_args", {}) or {})
        connect_args.setdefault(
            "connect_timeout",
            max(1, math.ceil(DATABASE_CONNECT_TIMEOUT_SECONDS)),
        )
        options["connect_args"] = connect_args
    return create_engine(database_url, **options)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the shared explicit SQLAlchemy session factory."""

    return sessionmaker(bind=engine, expire_on_commit=False)

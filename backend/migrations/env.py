"""Alembic environment for the RAGClaw backend (async SQLAlchemy).

Schema source of truth is ``app.database.Base.metadata``. The database URL is
taken from the application config so it always matches ``app.database``.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

from app.database import Base, DATABASE_URL
# Import the models package so every table is registered on Base.metadata.
import app.models  # noqa: F401

config = context.config

# Inject the database URL. Defaults to the application's configured URL, but can
# be overridden via ALEMBIC_DB_URL (e.g. to target a throwaway DB for testing).
db_url = os.environ.get("ALEMBIC_DB_URL", DATABASE_URL)
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        # Logging config is optional; ignore if it cannot be applied.
        pass

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DB connection)."""
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the async engine."""
    connectable = create_async_engine(db_url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

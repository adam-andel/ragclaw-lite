"""SQLite database setup via SQLAlchemy async.

Schema is owned by Alembic (see ``migrations/``). On startup ``init_db`` runs
``alembic upgrade head`` to bring the schema to the latest version, then seeds
idempotent default data (admin user, default MCP server).

All tables are defined as SQLAlchemy models under ``app/models``; the single
baseline Alembic migration (``migrations/versions/*_initial_schema.py``) creates
the full schema from those models. Future schema changes are made by adding new
Alembic revisions — there is no hand-rolled migration chain.
"""

import asyncio
import secrets
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
settings.skills_dir.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{settings.sqlite_path}"

engine = create_async_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


class _AsyncSessionProxy:
    """Late-bound async session factory.

    Resolves the engine from the *current* ``settings.sqlite_path`` on
    every call, so redirects of the database path (e.g. test fixtures
    that point ``settings.sqlite_path`` at a temp dir) take effect for
    every caller — including modules that imported ``async_session``
    early at import time. In production ``settings.sqlite_path`` is
    fixed, so a single engine is created and cached (no behaviour
    change there).
    """

    _engines: dict[str, object] = {}

    def _get_engine(self):
        url = f"sqlite+aiosqlite:///{settings.sqlite_path}"
        eng = self._engines.get(url)
        if eng is None:
            eng = create_async_engine(
                url, echo=False, connect_args={"check_same_thread": False}
            )
            self._engines[url] = eng
        return eng

    def __call__(self, *args, **kwargs):
        maker = async_sessionmaker(
            self._get_engine(), class_=AsyncSession, expire_on_commit=False
        )
        return maker(*args, **kwargs)


# Public session factory (late-bound — see _AsyncSessionProxy).
async_session = _AsyncSessionProxy()


class Base(DeclarativeBase):
    pass


def _gen_uuid():
    return str(uuid.uuid4())


def allocate_repl_uid(existing: Iterable[int]) -> int:
    """Randomly allocate a UID in [repl_uid_range_min+1, repl_uid_range_max) not in ``existing``.

    Pure function — does not touch the database. The caller is responsible for
    passing in the currently occupied UIDs. MIN is reserved for the bootstrap
    admin (the first user self-registered via ``POST /api/auth/register`` in
    routers/auth.py takes this UID), so regular-user allocation starts at MIN+1
    and can never collide with the admin's fixed UID. Actual uniqueness is
    ultimately guaranteed by the UNIQUE constraint on users.repl_uid plus a
    commit-time retry (the pre-check may be stale under concurrency — see
    routers/users.py create_user). Raises RuntimeError when the candidate space
    is exhausted.
    """
    lo = settings.repl_uid_range_min + 1
    hi = settings.repl_uid_range_max
    taken = set(existing)
    for _ in range(100):
        cand = secrets.randbelow(hi - lo) + lo
        if cand not in taken:
            return cand
    raise RuntimeError("repl_uid candidate space exhausted, cannot allocate a new UID")


# ─── Public API ───

async def init_db():
    """Apply database migrations (Alembic) and seed idempotent default data."""
    await asyncio.to_thread(_run_alembic_upgrade)
    await asyncio.to_thread(_seed_db)


def _run_alembic_upgrade():
    """Run all pending Alembic migrations against the configured database."""
    from alembic import command
    from alembic.config import Config

    base_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(base_dir / "alembic.ini"))
    # Point Alembic at our env/versions and the application's database URL.
    cfg.set_main_option("script_location", str(base_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(cfg, "head")
    # Alembic's env.py calls fileConfig(alembic.ini), which attaches its own
    # timestamp-less "console" handler to the root logger — duplicating every
    # ragclaw.* line our handler emits. Re-apply our logging setup immediately
    # so startup-time ragclaw INFO logs (MCP push, BGE warmup, ...) are not
    # doubled before the first HTTP request reaches the per-request middleware.
    from app.logging_config import setup_logging
    setup_logging()


def _seed_db():
    """Seed idempotent default data (default MCP server).

    The admin user is intentionally NOT auto-seeded: the first user registers
    themselves as the super admin via ``POST /api/auth/register`` on first
    launch (see ``app/routers/auth.py``). That keeps the bootstrap credentials
    user-chosen instead of a hardcoded ``admin/admin123``.
    """
    import sqlite3

    raw = sqlite3.connect(str(settings.sqlite_path))
    try:
        _seed_defaults(raw)
        raw.commit()
    finally:
        raw.close()


def _seed_defaults(raw):
    """Seed default MCP Server (idempotent).

    Creates:
    1. Default MCP Server 'Python Executor' (if not exists) — provides the
       native file/code execution tools (e.g. run_python) that claw exposes as
       always-available meta tools (see agent_nodes._build_all_meta_tools).
    """
    print("[seed] Checking default MCP Server...")
    import hashlib

    # Deterministic UUIDs
    mcp_id = str(uuid.UUID(hashlib.md5(b"ragclaw-default-python-repl").hexdigest()))
    now = datetime.now(timezone.utc).isoformat()

    # Default MCP Server: Python Executor (platform-mandated, built-in).
    existing = raw.execute("SELECT id FROM mcp_servers WHERE id = ?", (mcp_id,)).fetchone()
    if not existing:
        raw.execute(
            "INSERT INTO mcp_servers(id, name, transport_type, endpoint, timeout_seconds, is_active, is_builtin, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (mcp_id, "Python Executor", "http", "http://mcp-repl:9200/mcp", 30, 1, 1, now),
        )
        print("[seed] MCP Server 'Python Executor' created (built-in)")
    else:
        print("[seed] MCP Server 'Python Executor' already exists")

    # Idempotently (re)assert the built-in flag on the existing row so the
    # Python Executor is always treated as a platform-managed server, even if
    # the column was just added by a migration on an older database.
    raw.execute(
        "UPDATE mcp_servers SET is_builtin = 1 WHERE id = ? AND COALESCE(is_builtin, 0) = 0",
        (mcp_id,),
    )

    print("[seed] defaults done")


async def get_db() -> AsyncSession:
    """FastAPI dependency: yield an async DB session."""
    async with async_session() as session:
        yield session

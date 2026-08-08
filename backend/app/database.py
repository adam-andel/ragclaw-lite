"""SQLite database setup via SQLAlchemy async.

Schema is declarative: ``Base.metadata.create_all(checkfirst=True)`` compares
the ORM models against the live database and only creates missing tables/columns.
No migration files, no revision chain — the ORM models *are* the schema source
of truth. On startup ``init_db`` creates any missing tables, then seeds idempotent
default data.
"""

import asyncio
import secrets
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
settings.skills_dir.mkdir(parents=True, exist_ok=True)

# Database URL is supplied via settings (DATABASE_URL), defaulting to the local
# SQLite file so the project still runs with a one-command `docker compose up`
# and no external Postgres. Swap to e.g.
#   postgresql+asyncpg://user:pass@host:5432/ragclaw
# for production-grade concurrent access. No SQLite-specific connect_args are
# attached here — both aiosqlite and asyncpg are happy without them.
DATABASE_URL = settings.database_url or f"sqlite+aiosqlite:///{settings.sqlite_path}"

engine = create_async_engine(DATABASE_URL, echo=False)

# ─── Write concurrency limiter ───
# SQLite serializes all writes behind a single file lock, so a burst of
# concurrent write requests (upload / process / delete / reindex triggered
# from the API) can hit "database is locked". This semaphore caps how many
# write requests may enter the DB at once from the API process. Background
# writers (doc_processor task, reindex daemon thread) are NOT covered here —
# they run serially on their own and simply queue behind the SQLite lock.
# Raising this above 1 on SQLite gives little benefit (the lock is still
# single-writer); it mainly smooths request handling and prevents lock
# contention spikes. On Postgres this becomes a soft concurrency cap.
WRITE_CONCURRENCY = 4
write_semaphore = asyncio.Semaphore(WRITE_CONCURRENCY)


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
        url = settings.database_url or f"sqlite+aiosqlite:///{settings.sqlite_path}"
        eng = self._engines.get(url)
        if eng is None:
            eng = create_async_engine(url, echo=False)
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
    """Create tables from ORM models (declarative) and seed idempotent default data."""
    await _create_tables()
    await _seed_db()


async def _create_tables():
    """Create any tables/columns defined by ORM models that are missing in the database.

    Uses ``Base.metadata.create_all(checkfirst=True)`` under an async connection.
    Importing ``app.models`` ensures every ORM model is registered on Base.metadata
    before the diff runs.
    """
    import app.models  # noqa: F401
    async with engine.begin() as conn:
        # WAL lets readers proceed while a writer is active and avoids the
        # "database is locked" busy errors under concurrent read/write on
        # SQLite. Harmless no-op on Postgres (skipped via the URL check).
        if DATABASE_URL.startswith("sqlite"):
            from sqlalchemy import text
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=5000"))
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)


async def _seed_db():
    """Seed idempotent default data (default MCP server).

    The admin user is intentionally NOT auto-seeded: the first user registers
    themselves as the super admin via ``POST /api/auth/register`` on first
    launch (see ``app/routers/auth.py``). That keeps the bootstrap credentials
    user-chosen instead of a hardcoded ``admin/admin123``.

    Runs through the async session so it works against both SQLite and
    Postgres without any raw driver or dialect-specific SQL.
    """
    import hashlib

    from sqlalchemy import select
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.skill import MCPServer

    # Deterministic UUID
    mcp_id = str(uuid.UUID(hashlib.md5(b"ragclaw-default-python-repl").hexdigest()))

    async with async_session() as session:
        existing = await session.scalar(
            select(MCPServer.id).where(MCPServer.id == mcp_id).limit(1)
        )
        if existing is None:
            session.add(MCPServer(
                id=mcp_id,
                name="Python Executor",
                transport_type="http",
                endpoint="http://mcp-repl:9200/mcp",
                timeout_seconds=30,
                is_active=True,
                is_builtin=True,
            ))
            print("[seed] MCP Server 'Python Executor' created (built-in)")
        else:
            # (Re)assert the built-in flag on the existing row so the Python
            # Executor is always treated as a platform-managed server, even if
            # the column was just added by a migration on an older database.
            # Use the per-dialect ON CONFLICT upsert so the same code path works
            # on both SQLite and Postgres.
            insert_stmt = (
                pg_insert(MCPServer) if DATABASE_URL.startswith("postgresql")
                else sqlite_insert(MCPServer)
            ).values(
                id=mcp_id, name="Python Executor", transport_type="http",
                endpoint="http://mcp-repl:9200/mcp", timeout_seconds=30,
                is_active=True, is_builtin=True,
            ).on_conflict_do_update(
                index_elements=[MCPServer.id],
                set_={MCPServer.is_builtin.key: True},
            )
            await session.execute(insert_stmt)
            print("[seed] MCP Server 'Python Executor' already exists")
        await session.commit()
    print("[seed] defaults done")


async def get_db() -> AsyncSession:
    """FastAPI dependency: yield an async DB session."""
    async with async_session() as session:
        yield session


async def serialize_writes():
    """FastAPI dependency: cap concurrent write requests to the database.

    SQLite allows only one writer at a time, so a burst of concurrent write
    endpoints (upload / process / delete / reindex) would otherwise contend on
    the file lock and raise "database is locked". Acquiring this semaphore
    before the handler body limits how many writes run simultaneously from the
    API process. Background writers (doc_processor, reindex daemon) are not
    routed through this dependency and serialize themselves.
    """
    async with write_semaphore:
        yield

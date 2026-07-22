"""SQLite database setup via SQLAlchemy async.

Schema is owned by Alembic (see ``migrations/``). On startup ``init_db`` runs
``alembic upgrade head`` to bring the schema to the latest version, then seeds
idempotent default data (admin user, default MCP server, doc-manager skill).

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
    admin (see ``_seed_admin_user``), so regular-user allocation starts at MIN+1
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


def _seed_db():
    """Seed idempotent default data (admin user, default MCP server, doc-manager skill)."""
    import sqlite3

    raw = sqlite3.connect(str(settings.sqlite_path))
    try:
        _seed_admin_user(raw)
        _seed_defaults(raw)
        raw.commit()
    finally:
        raw.close()


def _seed_admin_user(raw):
    """Seed the default admin user (idempotent, atomic).

    The admin gets a FIXED, reserved REPL sandbox UID (= ``repl_uid_range_min``)
    so its workspace / code sandbox are initialised out of the box and the UID
    is stable across restarts. Insertion is an idempotent upsert
    (``ON CONFLICT(username) DO NOTHING``) — safe under concurrent bootstrap and
    never raises if the admin row already exists.
    """
    import hashlib
    from app.services.auth import hash_password
    from app.models.user import UserRole

    admin_repl_uid = settings.repl_uid_range_min
    admin_user_id = str(uuid.UUID(hashlib.md5(b"ragclaw-default-admin-user").hexdigest()))
    now = datetime.now(timezone.utc).isoformat()

    raw.execute(
        "INSERT INTO users(id, username, hashed_password, display_name, role, is_active, repl_uid, created_at) "
        "VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(username) DO NOTHING",
        (admin_user_id, "admin", hash_password("admin123"), "Administrator",
         UserRole.ADMIN.value, 1, admin_repl_uid, now),
    )
    print("[seed] admin user ensured (admin / admin123)")


def _seed_defaults(raw):
    """Seed default MCP Server and doc-manager Skill folder (idempotent).

    Creates:
    1. Default MCP Server 'Python executor' (if not exists)
    2. doc-manager Skill folder with SKILL.md on disk + DB index row
    """
    print("[seed] Checking default MCP Server and Skill folder...")
    import hashlib

    # Deterministic UUIDs
    mcp_id = str(uuid.UUID(hashlib.md5(b"ragclaw-default-python-repl").hexdigest()))
    skill_id = str(uuid.UUID(hashlib.md5(b"ragclaw-default-doc-manager").hexdigest()))
    now = datetime.now(timezone.utc).isoformat()

    # Default MCP Server: Python executor
    existing = raw.execute("SELECT id FROM mcp_servers WHERE id = ?", (mcp_id,)).fetchone()
    if not existing:
        raw.execute(
            "INSERT INTO mcp_servers(id, name, transport_type, endpoint, timeout_seconds, is_active, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (mcp_id, "Python执行器", "http", "http://mcp-repl:9200/mcp", 30, 1, now),
        )
        print("[seed] MCP Server 'Python执行器' created")
    else:
        print("[seed] MCP Server 'Python执行器' already exists")

    # Default Skill: doc-manager (folder-based)
    skill_dir = settings.skills_dir / "doc-manager"
    skill_md_path = skill_dir / "SKILL.md"

    if not skill_dir.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md_path.write_text(_build_doc_gen_skill_md(), encoding="utf-8")
        print("[seed] Created doc-manager SKILL.md on disk")

    existing_skill = raw.execute("SELECT id FROM skills WHERE folder_name = ?", ("doc-manager",)).fetchone()
    if not existing_skill:
        raw.execute(
            "INSERT INTO skills(id, folder_name, name, description, is_active, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (skill_id, "doc-manager", "Document Manager",
             "Create, read, update, and delete workspace files (txt/csv/xlsx/pptx/png/pdf/html/markdown and more) via the Python executor.",
             1, now, now),
        )
        print("[seed] Skill 'doc-manager' DB index created")
    else:
        print("[seed] Skill 'doc-manager' DB index already exists")

    print("[seed] defaults done")


def _build_doc_gen_skill_md() -> str:
    """Build the SKILL.md content for the doc-manager seed skill."""
    return """---
name: Document Manager
description: "Create, read, update, and delete workspace files (txt/csv/xlsx/pptx/png/pdf/html/markdown and more) via the Python executor."
mcp_servers:
  - Python执行器
---

# Document Manager

## Core rule (must follow)
Your job is NOT to show code to the user — it is to actually operate on
workspace files. For every request you MUST call the `run_python` tool to
perform the file operation. Never just print code and stop.

## Operations
- **Create**: write a new file with `open(path, "w", encoding="utf-8")`.
- **Read**: read an existing file with `open(path, "r", encoding="utf-8").read()`
  (or `pathlib.Path(path).read_text()`) and print its content.
- **Update**: read first, then modify (replace / insert / append) and write back.
  Prefer targeted edits over full overwrite when content should be preserved.
- **Delete**: remove a file with `pathlib.Path(path).unlink()` / `os.remove(path)`.
  Use `shutil.rmtree(path)` only for directories and only when explicitly asked.

## Safety constraints
- Operate ONLY inside the workspace directory (run_python's cwd). No absolute
  paths, no `..` traversal escaping it.
- Before any Delete: confirm which file(s) will be removed and state it clearly.
  Never delete the whole workspace, unrelated files, or files outside the task.
- Before Update of an existing file: read it first so you don't destroy data.
- Do not run destructive ops on files you didn't create unless explicitly asked.

## Examples
**User:** generate a txt with content: 1
**Do:** run_python ->
  with open("output.txt","w",encoding="utf-8") as f: f.write("1")
  print("file created")

**User:** read output.txt
**Do:** run_python -> print(open("output.txt",encoding="utf-8").read())

**User:** append a line to output.txt
**Do:** run_python ->
  p="output.txt"; s=open(p,encoding="utf-8").read()+"\\nappended\\n"
  open(p,"w",encoding="utf-8").write(s); print("updated")

**User:** delete old.txt
**Do:** run_python -> import os; os.remove("old.txt"); print("deleted old.txt")

## Gotchas
- Use English filenames (output.txt, report.docx, chart.png).
- Installed libs: pandas, python-docx, python-pptx, PyPDF2.
- No network; no external process calls.
- Generated/changed files expire after 60 min and are auto-deleted.
- `print()` output is returned to you as the tool result.

## Constraints
- Avoid very large files or long-running ops (timeout risk).
- Save to the current directory; the tool auto-assigns the workspace subdir.
"""


async def get_db() -> AsyncSession:
    """FastAPI dependency: yield an async DB session."""
    async with async_session() as session:
        yield session

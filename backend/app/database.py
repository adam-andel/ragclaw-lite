"""SQLite database setup via SQLAlchemy async.

Schema is owned by Alembic (see ``migrations/``). On startup ``init_db`` runs
``alembic upgrade head`` to bring the schema to the latest version, then seeds
idempotent default data (admin user, default MCP server, doc-gen skill).

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
    """Seed idempotent default data (admin user, default MCP server, doc-gen skill)."""
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
    """Seed default MCP Server and doc-gen Skill folder (idempotent).

    Creates:
    1. Default MCP Server 'Python executor' (if not exists)
    2. doc-gen Skill folder with SKILL.md on disk + DB index row
    """
    print("[seed] Checking default MCP Server and Skill folder...")
    import hashlib

    # Deterministic UUIDs
    mcp_id = str(uuid.UUID(hashlib.md5(b"ragclaw-default-python-repl").hexdigest()))
    skill_id = str(uuid.UUID(hashlib.md5(b"ragclaw-default-doc-gen").hexdigest()))
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

    # Default Skill: doc-gen (folder-based)
    skill_dir = settings.skills_dir / "doc-gen"
    skill_md_path = skill_dir / "SKILL.md"

    if not skill_dir.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md_path.write_text(_build_doc_gen_skill_md(), encoding="utf-8")
        print("[seed] Created doc-gen SKILL.md on disk")

    existing_skill = raw.execute("SELECT id FROM skills WHERE folder_name = ?", ("doc-gen",)).fetchone()
    if not existing_skill:
        raw.execute(
            "INSERT INTO skills(id, folder_name, name, description, is_active, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (skill_id, "doc-gen", "文档生成助手",
             "生成文档、报表、图表、PPT、网页等文件，支持txt/csv/xlsx/pptx/png/pdf/html/markdown等格式",
             1, now, now),
        )
        print("[seed] Skill 'doc-gen' DB index created")
    else:
        print("[seed] Skill 'doc-gen' DB index already exists")

    print("[seed] defaults done")


def _build_doc_gen_skill_md() -> str:
    """Build the SKILL.md content for the doc-gen seed skill."""
    return """---
name: 文档生成助手
description: "生成文档、报表、图表、PPT、网页等文件，支持txt/csv/xlsx/pptx/png/pdf/html/markdown等格式"
mcp_servers:
  - Python执行器
---

# 文档生成助手

## 核心规则（必须严格遵守）

你的任务不是写代码给用户看，而是**真正生成文档文件**。收到文档生成请求后，你必须通过 `run_python` 工具执行 Python 代码来生成文件。

**禁止以下行为：**
- 只输出代码说明而不调用工具
- 先展示代码再等用户确认（除非用户明确要求"先让我看一下代码"）
- 告诉用户"可以用以下代码生成"——用户要的是文件，不是代码
- 以任何自然语言描述代替工具调用

## Examples

**用户说：** 生成一个txt，内容是：1
**你该做的：** 调用 run_python，code 参数为：
```python
with open("output.txt", "w", encoding="utf-8") as f:
    f.write("1")
print("文件已生成")
```

**用户说：** 生成一个包含姓名、年龄两列的 CSV
**你该做的：** 调用 run_python，code 参数为：
```python
import csv
with open("data.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow(["姓名", "年龄"])
print("CSV 已生成")
```

## Gotchas
- 使用英文文件名（如 output.txt、report.docx、chart.png）
- 同名文件用数字序号区分（如 output2.txt）
- 已安装三方库：pandas、python-docx、python-pptx、PyPDF2
- 网络访问被禁止、外部进程调用被禁止
- 生成文件 60 分钟内有效，超时自动删除
- 代码中 `print()` 的内容会返回给你作为工具输出

## Constraints
- 避免生成超大文件或耗时操作，防止超时
- 保存到当前目录即可，工具会自动分配 workspace 子目录
"""


async def get_db() -> AsyncSession:
    """FastAPI dependency: yield an async DB session."""
    async with async_session() as session:
        yield session

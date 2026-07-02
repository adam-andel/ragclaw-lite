"""SQLite database setup via SQLAlchemy async with auto-migration."""

import uuid
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
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _gen_uuid():
    return str(uuid.uuid4())


# ─── Migration helpers (operate on sync sqlite3 connection) ───

def _apply_migrations(raw):
    """Apply all pending migrations to a sync sqlite3 connection. Idempotent."""
    raw.execute(
        "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in raw.execute("SELECT name FROM _migrations").fetchall()}

    if "m2m_refactor" not in applied:
        _migrate_m2m(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('m2m_refactor', ?)",
                     (datetime.now(timezone.utc).isoformat(),))

    if "kb_id_nullable" not in applied:
        _migrate_nullable_kb_id(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('kb_id_nullable', ?)",
                     (datetime.now(timezone.utc).isoformat(),))

    if "skill_system" not in applied:
        _migrate_skill_system(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('skill_system', ?)",
                     (datetime.now(timezone.utc).isoformat(),))

    if "seed_defaults" not in applied:
        _seed_defaults(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('seed_defaults', ?)",
                     (datetime.now(timezone.utc).isoformat(),))

    if "seed_admin_user" not in applied:
        _seed_admin_user(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('seed_admin_user', ?)",
                     (datetime.now(timezone.utc).isoformat(),))

    if "parser_plugin_state" not in applied:
        _migrate_parser_plugin_state(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('parser_plugin_state', ?)",
                     (datetime.now(timezone.utc).isoformat(),))

    if "skill_folder_refactor" not in applied:
        _migrate_skill_folder_refactor(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('skill_folder_refactor', ?)",
                     (datetime.now(timezone.utc).isoformat(),))

    if "cron_jobs" not in applied:
        _migrate_cron_jobs(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('cron_jobs', ?)",
                     (datetime.now(timezone.utc).isoformat(),))

    raw.commit()


def _add_col_if_missing(raw, table, col_name, col_type):
    existing = {row[1] for row in raw.execute(f"PRAGMA table_info({table})").fetchall()}
    if col_name in existing:
        return False
    raw.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
    return True


def _migrate_m2m(raw):
    print("[migrate] Running m2m_refactor...")

    _add_col_if_missing(raw, "documents", "progress", "INTEGER DEFAULT 0")
    _add_col_if_missing(raw, "documents", "owner_id", "TEXT")
    _add_col_if_missing(raw, "documents", "tenant_id", "TEXT")
    _add_col_if_missing(raw, "documents", "updated_at", "TEXT")
    _add_col_if_missing(raw, "knowledge_bases", "updated_at", "TEXT")
    _add_col_if_missing(raw, "chunks", "embedding", "BLOB")

    raw.execute("""
        CREATE TABLE IF NOT EXISTS kb_documents (
            id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, doc_id TEXT NOT NULL, added_at TEXT,
            FOREIGN KEY(kb_id) REFERENCES knowledge_bases(id),
            FOREIGN KEY(doc_id) REFERENCES documents(id),
            UNIQUE(kb_id, doc_id)
        )
    """)
    raw.execute("CREATE INDEX IF NOT EXISTS idx_kb_docs_kb ON kb_documents(kb_id)")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_kb_docs_doc ON kb_documents(doc_id)")

    # Migrate existing doc→KB links
    migrated = skipped = 0
    for doc_id, kb_id in raw.execute("SELECT id, kb_id FROM documents WHERE kb_id IS NOT NULL").fetchall():
        if raw.execute("SELECT id FROM kb_documents WHERE kb_id=? AND doc_id=?", (kb_id, doc_id)).fetchone():
            skipped += 1
            continue
        raw.execute("INSERT INTO kb_documents(id, kb_id, doc_id, added_at) VALUES(?,?,?,?)",
                     (_gen_uuid(), kb_id, doc_id, datetime.now(timezone.utc).isoformat()))
        migrated += 1

    for doc_id, owner_id, tenant_id in raw.execute("""
        SELECT d.id, kb.owner_id, kb.tenant_id
        FROM documents d JOIN kb_documents kd ON d.id=kd.doc_id
        JOIN knowledge_bases kb ON kd.kb_id=kb.id WHERE d.owner_id IS NULL
    """).fetchall():
        raw.execute("UPDATE documents SET owner_id=?, tenant_id=? WHERE id=?", (owner_id, tenant_id, doc_id))

    raw.execute("UPDATE documents SET progress=100 WHERE status IN ('completed','failed')")
    now = datetime.now(timezone.utc).isoformat()
    raw.execute("UPDATE documents SET updated_at=? WHERE updated_at IS NULL", (now,))
    raw.execute("UPDATE knowledge_bases SET updated_at=? WHERE updated_at IS NULL", (now,))
    print(f"[migrate] m2m_refactor done: {migrated} links, {skipped} skipped")


def _migrate_nullable_kb_id(raw):
    print("[migrate] Running kb_id_nullable...")
    cols = {row[1]: row for row in raw.execute("PRAGMA table_info(documents)").fetchall()}
    if cols.get("kb_id", [None]*4)[3] == 0:  # notnull=0 → already nullable
        print("[migrate] kb_id already nullable, skipping")
        return

    col_names = [row[1] for row in raw.execute("PRAGMA table_info(documents)").fetchall()]

    raw.execute("""
        CREATE TABLE documents_new (
            id VARCHAR(36) PRIMARY KEY, kb_id VARCHAR(36),
            filename VARCHAR(255) NOT NULL, file_type VARCHAR(20) NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0, file_path VARCHAR(500) NOT NULL,
            status VARCHAR(9) NOT NULL, error_message TEXT,
            chunk_count INTEGER NOT NULL DEFAULT 0, created_at DATETIME NOT NULL,
            progress INTEGER DEFAULT 0, owner_id TEXT, tenant_id TEXT, updated_at TEXT
        )
    """)
    raw.execute(f"INSERT INTO documents_new SELECT {','.join(col_names)} FROM documents")
    count = raw.execute("SELECT COUNT(*) FROM documents_new").fetchone()[0]
    raw.execute("DROP TABLE documents")
    raw.execute("ALTER TABLE documents_new RENAME TO documents")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_documents_owner_id ON documents(owner_id)")
    print(f"[migrate] kb_id_nullable done: {count} rows")


def _migrate_skill_system(raw):
    """Create folder-based skills table and mcp_servers table (v0.7.0 refactor).

    Drops legacy skill_tools table and old skills table, creates new skills
    table with folder_name column for folder-based skill architecture.
    """
    print("[migrate] Running skill_system (folder-based refactor)...")

    # Drop legacy skill_tools table (no longer needed — MCP binding via SKILL.md front matter)
    raw.execute("DROP TABLE IF EXISTS skill_tools")

    # Recreate skills table with folder-based schema
    # If old skills table exists with different schema, drop and recreate
    cols = {row[1] for row in raw.execute("PRAGMA table_info(skills)").fetchall()} if _table_exists(raw, "skills") else set()
    if cols and "folder_name" not in cols:
        print("[migrate] Old skills table detected, dropping for folder-based refactor...")
        raw.execute("DROP TABLE IF EXISTS skills")
        cols = set()

    if not cols:
        raw.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                folder_name TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        raw.execute("CREATE INDEX IF NOT EXISTS idx_skills_tenant ON skills(tenant_id)")
        print("[migrate] Created folder-based skills table")

    # mcp_servers table (unchanged from before, create if not exists)
    raw.execute("""
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            name TEXT NOT NULL,
            transport_type TEXT NOT NULL DEFAULT 'http',
            endpoint TEXT,
            command TEXT,
            args_json TEXT,
            env_json TEXT,
            timeout_seconds INTEGER NOT NULL DEFAULT 30,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    raw.execute("CREATE INDEX IF NOT EXISTS idx_mcp_servers_tenant ON mcp_servers(tenant_id)")

    print("[migrate] skill_system done")


def _table_exists(raw, table_name: str) -> bool:
    return raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


def _migrate_skill_folder_refactor(raw):
    """Migrate skills table from DB-row schema to folder-based schema (v0.7.0).

    This migration runs ONCE for existing databases that already ran the old
    `skill_system` migration (which created the old schema with system_prompt).

    Steps:
    1. Drop skill_tools table (M2M no longer used)
    2. Drop old skills table (with system_prompt/created_by columns)
    3. Create new skills table (with folder_name, no system_prompt/created_by)
    4. Run seed_defaults to create doc-gen folder + DB index
    """
    print("[migrate] Running skill_folder_refactor...")

    # Drop legacy skill_tools table
    raw.execute("DROP TABLE IF EXISTS skill_tools")

    # Check if skills table has old schema (system_prompt column = old, folder_name = new)
    if _table_exists(raw, "skills"):
        cols = {row[1] for row in raw.execute("PRAGMA table_info(skills)").fetchall()}
        if "folder_name" in cols:
            print("[migrate] skills table already has folder_name, skipping")
            return
        if "system_prompt" in cols or "created_by" in cols:
            print("[migrate] Old skills table detected (has system_prompt/created_by), dropping for refactor...")
            raw.execute("DROP TABLE skills")

    # Create new folder-based skills table
    raw.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            folder_name TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    raw.execute("CREATE INDEX IF NOT EXISTS idx_skills_tenant ON skills(tenant_id)")
    print("[migrate] Created folder-based skills table")

    # Seed the default doc-gen skill
    _seed_defaults(raw)
    print("[migrate] skill_folder_refactor done")

def _seed_admin_user(raw):
    """Seed default admin user (v0.6.0)."""
    print("[seed] Checking default admin user...")
    import hashlib

    admin_user_id = str(uuid.UUID(hashlib.md5(b"erag-default-admin-user").hexdigest()))
    now = datetime.now(timezone.utc).isoformat()

    from app.services.auth import hash_password
    from app.models.user import UserRole
    existing = raw.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
    if not existing:
        raw.execute(
            "INSERT INTO users(id, username, hashed_password, display_name, role, is_active, created_at) VALUES(?,?,?,?,?,?,?)",
            (admin_user_id, "admin", hash_password("admin123"), "超级管理员", UserRole.ADMIN.value, 1, now),
        )
        print("[seed] Admin user 'admin' created")
    else:
        print("[seed] Admin user 'admin' already exists")


def _seed_defaults(raw):
    """Seed default MCP Server and doc-gen Skill folder (v0.7.0 folder-based).

    Creates:
    1. Default MCP Server 'Python执行器' (if not exists)
    2. doc-gen Skill folder with SKILL.md on disk + DB index row
    """
    print("[seed] Checking default MCP Server and Skill folder...")
    import hashlib

    # Deterministic UUIDs
    mcp_id = str(uuid.UUID(hashlib.md5(b"erag-default-python-repl").hexdigest()))
    skill_id = str(uuid.UUID(hashlib.md5(b"erag-default-doc-gen").hexdigest()))
    now = datetime.now(timezone.utc).isoformat()

    # Default MCP Server: Python执行器
    existing = raw.execute("SELECT id FROM mcp_servers WHERE id = ?", (mcp_id,)).fetchone()
    if not existing:
        raw.execute(
            "INSERT INTO mcp_servers(id, name, transport_type, endpoint, timeout_seconds, is_active, created_at) VALUES(?,?,?,?,?,?,?)",
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
        skill_md_content = _build_doc_gen_skill_md()
        skill_md_path.write_text(skill_md_content, encoding="utf-8")
        print("[seed] Created doc-gen SKILL.md on disk")

    # DB index row for doc-gen
    existing_skill = raw.execute("SELECT id FROM skills WHERE folder_name = ?", ("doc-gen",)).fetchone()
    if not existing_skill:
        raw.execute(
            "INSERT INTO skills(id, folder_name, name, description, is_active, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
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


def _migrate_cron_jobs(raw):
    """Create cron job tables for scheduled agent tasks."""
    print("[migrate] Running cron_jobs...")

    raw.execute("""
        CREATE TABLE IF NOT EXISTS cron_jobs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            user_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            cron_expr TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            max_runs INTEGER,
            run_count INTEGER NOT NULL DEFAULT 0,
            task_content TEXT NOT NULL,
            kb_id TEXT,
            skill_id TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled',
            next_run_at TEXT,
            last_run_at TEXT,
            last_result TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    raw.execute("CREATE INDEX IF NOT EXISTS idx_cron_jobs_tenant ON cron_jobs(tenant_id)")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_cron_jobs_user ON cron_jobs(user_id)")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_cron_jobs_next_run ON cron_jobs(next_run_at)")

    raw.execute("""
        CREATE TABLE IF NOT EXISTS cron_job_runs (
            id TEXT PRIMARY KEY,
            cron_job_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            output TEXT,
            result_json TEXT,
            error TEXT
        )
    """)
    raw.execute("CREATE INDEX IF NOT EXISTS idx_cron_job_runs_job ON cron_job_runs(cron_job_id)")

    print("[migrate] cron_jobs done")


def _migrate_parser_plugin_state(raw):
    """Create parser_plugin_state table for plugin enable/disable management."""
    print("[migrate] Running parser_plugin_state...")
    raw.execute("""
        CREATE TABLE IF NOT EXISTS parser_plugin_state (
            name TEXT PRIMARY KEY,
            disabled INTEGER NOT NULL DEFAULT 1,
            disabled_by TEXT,
            disabled_at TEXT,
            reason TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    print("[migrate] parser_plugin_state done")


# ─── Public API ───

async def init_db():
    """Create tables and run pending migrations on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Run migrations via a separate sync connection
    import sqlite3
    raw = sqlite3.connect(str(settings.sqlite_path))
    try:
        _apply_migrations(raw)
    finally:
        raw.close()


async def get_db() -> AsyncSession:
    """FastAPI dependency: yield an async DB session."""
    async with async_session() as session:
        yield session

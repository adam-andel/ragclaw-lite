"""SQLite database setup via SQLAlchemy async with auto-migration."""

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

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
                     (datetime.utcnow().isoformat(),))

    if "kb_id_nullable" not in applied:
        _migrate_nullable_kb_id(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('kb_id_nullable', ?)",
                     (datetime.utcnow().isoformat(),))

    if "skill_system" not in applied:
        _migrate_skill_system(raw)
        raw.execute("INSERT INTO _migrations(name, applied_at) VALUES ('skill_system', ?)",
                     (datetime.utcnow().isoformat(),))

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
                     (_gen_uuid(), kb_id, doc_id, datetime.utcnow().isoformat()))
        migrated += 1

    for doc_id, owner_id, tenant_id in raw.execute("""
        SELECT d.id, kb.owner_id, kb.tenant_id
        FROM documents d JOIN kb_documents kd ON d.id=kd.doc_id
        JOIN knowledge_bases kb ON kd.kb_id=kb.id WHERE d.owner_id IS NULL
    """).fetchall():
        raw.execute("UPDATE documents SET owner_id=?, tenant_id=? WHERE id=?", (owner_id, tenant_id, doc_id))

    raw.execute("UPDATE documents SET progress=100 WHERE status IN ('completed','failed')")
    now = datetime.utcnow().isoformat()
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
    """Create skill_tools, skills, and mcp_servers tables (v0.3.0)."""
    print("[migrate] Running skill_system...")

    raw.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            system_prompt TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    raw.execute("CREATE INDEX IF NOT EXISTS idx_skills_tenant ON skills(tenant_id)")

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

    raw.execute("""
        CREATE TABLE IF NOT EXISTS skill_tools (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            mcp_server_id TEXT NOT NULL,
            config_json TEXT,
            FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE,
            FOREIGN KEY(mcp_server_id) REFERENCES mcp_servers(id) ON DELETE CASCADE
        )
    """)
    raw.execute("CREATE INDEX IF NOT EXISTS idx_skill_tools_skill ON skill_tools(skill_id)")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_skill_tools_mcp ON skill_tools(mcp_server_id)")
    raw.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_tools_unique ON skill_tools(skill_id, tool_name, mcp_server_id)")

    print("[migrate] skill_system done")


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

"""Pytest fixtures for RAGClaw backend — isolated test environment.

Data isolation: every test gets its own tmp_path → SQLite + ChromaDB + uploads.
Overrides settings at module level via monkeypatch before test runs.
"""

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Ensure backend is importable
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.config import settings
from app.models.user import User, UserRole

# Fix passlib + bcrypt incompatibility: use plain sha256 for testing
import hashlib as _hashlib

def _test_hash_password(password: str) -> str:
    return "test$" + _hashlib.sha256(password.encode()).hexdigest()

def _test_verify_password(plain: str, hashed: str) -> bool:
    return hashed == _test_hash_password(plain)

import app.services.auth as _auth_mod
_auth_mod.hash_password = _test_hash_password
_auth_mod.verify_password = _test_verify_password

# JWT secret shim: under the test HTTP client (httpx.ASGITransport) the FastAPI
# lifespan never runs, so ConfigManager stays half-initialized — its async
# init() (which seeds jwt_secret) is never awaited and auth.get_jwt_secret() would
# raise "JWT secret is empty". Monkeypatch the function directly (token sign/verify
# route through it) so every test uses a fixed, in-memory test secret for the
# session, independent of ConfigManager's internal state.
_auth_mod.get_jwt_secret = lambda: "TEST_JWT_SECRET_0000000000000000000000"

def create_access_token(user_id, username, role, tenant_id):
    return _auth_mod.create_access_token(user_id, username, role, tenant_id)


# ---------------------------------------------------------------------------
# Environment override — runs before every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_data(monkeypatch, tmp_path):
    """Redirect all persistent stores into a unique tmp_path per test."""
    test_dir = tmp_path / "ragclaw_test"
    uploads = test_dir / "uploads"
    chroma = test_dir / "chroma"
    sqlite_db = test_dir / "sqlite" / "test.db"

    for d in (uploads, chroma, sqlite_db.parent):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(settings, "data_dir", test_dir)
    monkeypatch.setattr(settings, "upload_dir", uploads)
    monkeypatch.setattr(settings, "sqlite_path", sqlite_db)
    monkeypatch.setattr(settings, "chroma_path", chroma)

    # Reset ChromaDB singleton so it reconnects to the new temp path
    from app.services.vector_store import vector_store
    vector_store._client = None

    yield

    # Teardown: close ChromaDB client to release file locks before pytest cleans tmp_path
    if vector_store._client is not None:
        try:
            vector_store._client._system.stop()
        except Exception:
            pass
        vector_store._client = None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_db():
    """Create fresh tables in the isolated SQLite, drop after test."""
    from app.database import Base
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    db_url = f"sqlite+aiosqlite:///{settings.sqlite_path}"
    engine = create_async_engine(db_url, echo=False, connect_args={"check_same_thread": False})

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # `app.database.async_session` is a late-bound proxy (_AsyncSessionProxy)
    # that routes to the engine for the *current* settings.sqlite_path —
    # patched to a per-test temp dir by the `_isolate_data` autouse fixture.
    # Lazy imports (`from app.database import async_session`) therefore always
    # hit the test DB, so no module-level rebind is needed here.
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(test_db):
    """httpx AsyncClient wired to the FastAPI app — no real server needed."""
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Auth tokens
# ---------------------------------------------------------------------------

async def _create_user_tokens(
    username: str, password: str, role: UserRole, display: str
) -> tuple[str, str]:
    """Helper: insert user into DB and return (token, user_id)."""
    from app.database import async_session

    uid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    async with async_session() as db:
        user = User(
            id=uid, username=username,
            hashed_password=_test_hash_password(password),
            display_name=display, role=role,
            is_active=True, tenant_id=tid,
        )
        db.add(user)
        await db.commit()
    return _auth_mod.create_access_token(uid, username, role.value, tid), uid


@pytest_asyncio.fixture
async def admin_token(test_db):
    token, _ = await _create_user_tokens("admin_test", "admin123", UserRole.ADMIN, "Admin")
    return token


@pytest_asyncio.fixture
async def moderator_token(test_db):
    token, _ = await _create_user_tokens("mod_test", "mod123", UserRole.MODERATOR, "Moderator")
    return token


@pytest_asyncio.fixture
async def user_token(test_db):
    token, _ = await _create_user_tokens("user_test", "user123", UserRole.USER, "User")
    return token


@pytest_asyncio.fixture
async def user2_token(test_db):
    """Second normal user — for isolation / horizontal privilege tests."""
    token, _ = await _create_user_tokens("user2_test", "user456", UserRole.USER, "User2")
    return token


# ---------------------------------------------------------------------------
# Shortcut: authenticated client helpers
# ---------------------------------------------------------------------------

def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_client(client, admin_token):
    """Return (client, headers) tuple for admin-authenticated requests."""
    return client, _auth_headers(admin_token)


@pytest_asyncio.fixture
async def user_client(client, user_token):
    """Return (client, headers) tuple for normal-user-authenticated requests."""
    return client, _auth_headers(user_token)


# ---------------------------------------------------------------------------
# Knowledge Base fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_kb(admin_token, test_db):
    """Create a KB owned by admin, return kb dict {id, name}."""
    from app.database import async_session
    from app.models.knowledge_base import KnowledgeBase

    kid = str(uuid.uuid4())
    async with async_session() as db:
        # Get admin user id from token
        from app.services.auth import decode_token
        payload = decode_token(admin_token)
        kb = KnowledgeBase(
            id=kid, name="测试知识库",
            description="pytest fixture KB",
            tenant_id=payload.get("tenant_id"),
            owner_id=payload["sub"],
        )
        db.add(kb)
        await db.commit()
    return {"id": kid, "name": "测试知识库"}

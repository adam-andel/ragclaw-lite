"""Security: authentication & token validation tests.

Covers: missing auth, expired/forged/tampered JWT, disabled users,
Bearer prefix edge cases, optional auth, health endpoint, login brute force.
"""

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jose import jwt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.models.user import User
from app.services.auth import (
    get_jwt_secret, ALGORITHM, hash_password, create_access_token,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Missing / empty Authorization
# ---------------------------------------------------------------------------

class TestMissingAuth:
    """Protected endpoints must reject requests without Authorization header."""

    @pytest.mark.asyncio
    async def test_no_auth_kb_list(self, client):
        r = await client.get("/api/kb")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_users_list(self, client):
        r = await client.get("/api/users")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_chat_stream(self, client):
        r = await client.post("/api/chat/stream", json={
            "query": "test", "kb_id": "dummy",
        })
        assert r.status_code == 401


class TestEmptyBearer:
    """Empty Bearer token → 401."""

    @pytest.mark.asyncio
    async def test_empty_bearer_kb(self, client):
        r = await client.get("/api/kb", headers={"Authorization": "Bearer "})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_bearer_users(self, client):
        r = await client.get("/api/users", headers={"Authorization": "Bearer "})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Forged / garbage tokens
# ---------------------------------------------------------------------------

class TestFakeJWT:
    """Arbitrary strings as JWT → 401, no crash."""

    @pytest.mark.asyncio
    async def test_random_string(self, client):
        r = await client.get("/api/kb", headers=_auth("not-a-valid-jwt-at-all"))
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_garbage(self, client):
        r = await client.get("/api/users", headers=_auth("!@#$%^&*()___"))
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Expired token
# ---------------------------------------------------------------------------

class TestExpiredJWT:
    @pytest.mark.asyncio
    async def test_expired_token(self, client):
        payload = {
            "sub": str(uuid.uuid4()),
            "username": "expired",
            "role": "user",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        token = jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)
        r = await client.get("/api/kb", headers=_auth(token))
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Tampered JWT: real role but user doesn't exist
# ---------------------------------------------------------------------------

class TestTamperedJWT:
    @pytest.mark.asyncio
    async def test_role_tampering(self, client):
        """Token claims admin role but sub points to non-existent user."""
        payload = {
            "sub": str(uuid.uuid4()),
            "username": "ghost_admin",
            "role": "admin",
            "tenant_id": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)
        r = await client.get("/api/kb", headers=_auth(token))
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Disabled user
# ---------------------------------------------------------------------------

class TestDisabledUser:
    @pytest.mark.asyncio
    async def test_disabled_user_token_rejected(self, client, test_db):
        from app.database import async_session
        uid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        async with async_session() as db:
            user = User(
                id=uid, username="disabled_one",
                hashed_password=hash_password("pw"),
                display_name="Disabled", role="user",
                is_active=False, tenant_id=tid,
            )
            db.add(user)
            await db.commit()

        token = create_access_token(uid, "disabled_one", "user", tid)
        r = await client.get("/api/kb", headers=_auth(token))
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Missing Bearer prefix
# ---------------------------------------------------------------------------

class TestMissingBearerPrefix:
    @pytest.mark.asyncio
    async def test_raw_token_no_bearer(self, client, user_token):
        """Pass token directly without 'Bearer ' prefix → 401."""
        r = await client.get("/api/kb", headers={"Authorization": user_token})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Token missing sub claim
# ---------------------------------------------------------------------------

class TestMissingSubField:
    @pytest.mark.asyncio
    async def test_no_sub_in_payload(self, client):
        payload = {
            "username": "nosub",
            "role": "user",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)
        r = await client.get("/api/kb", headers=_auth(token))
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Multiple Bearer prefixes
# ---------------------------------------------------------------------------

class TestMultipleBearer:
    @pytest.mark.asyncio
    async def test_double_bearer(self, client):
        """Double 'Bearer' prefix — should return 401, not 500."""
        r = await client.get(
            "/api/kb",
            headers={"Authorization": "Bearer Bearer extratoken"},
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Health — no auth
# ---------------------------------------------------------------------------

class TestHealthNoAuth:
    @pytest.mark.asyncio
    async def test_health_without_token(self, client):
        r = await client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Login brute force / info leak
# ---------------------------------------------------------------------------

class TestLoginBruteForce:
    @pytest.mark.asyncio
    async def test_wrong_password_no_user_leak(self, client, test_db):
        """Existing user + wrong password -> 401 with the SAME generic code
        returned for a non-existent user (no account-enumeration leak)."""
        from app.database import async_session
        uid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        username = f"brute_{uid[:8]}"
        async with async_session() as db:
            db.add(User(
                id=uid, username=username,
                hashed_password=hash_password("correct_password"),
                display_name="Brute", role="user",
                is_active=True, tenant_id=tid,
            ))
            await db.commit()

        resp = await client.post("/api/auth/login", json={
            "username": username,
            "password": "definitely_wrong_password",
        })
        assert resp.status_code == 401
        # Must match the code from test_nonexistent_user_no_leak so an
        # attacker cannot tell "exists, bad pwd" apart from "no such user".
        detail = resp.json().get("detail", "")
        assert "INVALID_CREDENTIALS" in detail

    @pytest.mark.asyncio
    async def test_nonexistent_user_no_leak(self, client):
        """Non-existent user → 401 with same generic message."""
        resp = await client.post("/api/auth/login", json={
            "username": "no_such_user_xyz_123",
            "password": "anything",
        })
        assert resp.status_code == 401
        detail = resp.json().get("detail", "")
        # Same generic error as wrong password — no info leakage
        assert "INVALID_CREDENTIALS" in detail

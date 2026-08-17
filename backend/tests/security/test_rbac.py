"""Security: role-based access control (RBAC) tests.

Verifies USER / MODERATOR / ADMIN permission boundaries across all API endpoints.
"""

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.models.user import User, UserRole
from app.models.knowledge_base import KnowledgeBase
from app.services.auth import hash_password, decode_token, create_access_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _get_user_id(token: str) -> str:
    return decode_token(token)["sub"]


# ===========================================================================
# USER role — forbidden actions
# ===========================================================================

class TestUserCanCreateKB:
    @pytest.mark.asyncio
    async def test_user_post_kb_allowed(self, client, user_token):
        r = await client.post("/api/kb", json={
            "name": "user-kb", "description": "test",
        }, headers=_auth(user_token))
        assert r.status_code == 201


class TestUserCannotDeleteKB:
    @pytest.mark.asyncio
    async def test_user_delete_kb_forbidden(self, client, user_token, test_kb):
        r = await client.delete(f"/api/kb/{test_kb['id']}", headers=_auth(user_token))
        assert r.status_code == 403


class TestUserCannotListUsers:
    @pytest.mark.asyncio
    async def test_user_get_users_forbidden(self, client, user_token):
        r = await client.get("/api/users", headers=_auth(user_token))
        assert r.status_code == 403


class TestUserCannotCreateUser:
    @pytest.mark.asyncio
    async def test_user_post_users_forbidden(self, client, user_token):
        r = await client.post("/api/users", json={
            "username": "created_by_user", "password": "pass123",
        }, headers=_auth(user_token))
        assert r.status_code == 403


class TestUserCannotUpload:
    @pytest.mark.asyncio
    async def test_user_upload_forbidden(self, client, user_token, test_kb):
        files = {"file": ("test.txt", b"hello", "text/plain")}
        r = await client.post("/api/documents/upload",
            files=files, params={"kb_id": test_kb["id"]},
            headers=_auth(user_token))
        assert r.status_code == 403


class TestUserCannotAccessStats:
    @pytest.mark.asyncio
    async def test_user_stats_forbidden(self, client, user_token):
        r = await client.get("/api/stats/overview", headers=_auth(user_token))
        assert r.status_code == 403


class TestUserCannotSearch:
    @pytest.mark.asyncio
    async def test_user_retrieval_forbidden(self, client, user_token, test_kb):
        r = await client.post("/api/retrieval/search", json={
            "query": "test", "kb_id": test_kb["id"],
        }, headers=_auth(user_token))
        assert r.status_code == 403


# ===========================================================================
# MODERATOR — limited to USER management, own KB
# ===========================================================================

class TestModeratorCannotCreateAdmin:
    @pytest.mark.asyncio
    async def test_moderator_create_admin_forbidden(self, client, moderator_token):
        r = await client.post("/api/users", json={
            "username": "mod_created_admin",
            "password": "pass123",
            "role": "admin",
        }, headers=_auth(moderator_token))
        assert r.status_code == 403


class TestModeratorCannotElevateToAdmin:
    @pytest.mark.asyncio
    async def test_moderator_promote_user_to_admin(self, client, admin_token, moderator_token):
        """Moderator creates a USER, then tries to change their role to admin."""
        # Admin first creates a regular user for moderator to manage
        r = await client.post("/api/users", json={
            "username": "target_user", "password": "pass123",
            "role": "user",
        }, headers=_auth(admin_token))
        assert r.status_code == 201
        target_id = r.json()["id"]

        # Moderator tries to elevate to admin
        r2 = await client.put(f"/api/users/{target_id}", json={
            "role": "admin",
        }, headers=_auth(moderator_token))
        assert r2.status_code == 403


class TestModeratorCannotDeleteAdmin:
    @pytest.mark.asyncio
    async def test_moderator_delete_admin_forbidden(self, client, admin_token, moderator_token):
        """Moderator cannot delete an ADMIN user."""
        # Get the admin user's ID
        admin_id = _get_user_id(admin_token)

        r = await client.delete(f"/api/users/{admin_id}", headers=_auth(moderator_token))
        assert r.status_code == 403  # can_manage_user rejects MODERATOR → ADMIN


class TestModeratorOwnKB:
    @pytest.mark.asyncio
    async def test_moderator_delete_own_kb(self, client, moderator_token):
        """Moderator creates and deletes their own KB → 200."""
        # Create KB as moderator
        r = await client.post("/api/kb", json={
            "name": "moderator-kb", "description": "owned by mod",
        }, headers=_auth(moderator_token))
        assert r.status_code == 201
        kb_id = r.json()["id"]

        # Delete own KB
        r2 = await client.delete(f"/api/kb/{kb_id}", headers=_auth(moderator_token))
        assert r2.status_code == 200


class TestModeratorCannotDeleteOthersKB:
    @pytest.mark.asyncio
    async def test_moderator_delete_admin_kb_forbidden(self, client, admin_token, moderator_token, test_kb):
        """Moderator tries to delete admin's KB → 403."""
        # test_kb is owned by admin
        r = await client.delete(f"/api/kb/{test_kb['id']}", headers=_auth(moderator_token))
        assert r.status_code == 403

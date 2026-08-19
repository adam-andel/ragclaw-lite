# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""API contract tests: /api/auth endpoints."""

import pytest
import asyncio


@pytest.mark.asyncio
async def test_login_success(client, admin_token):
    """POST /api/auth/login — correct credentials return 200 + token + user."""
    res = await client.post("/api/auth/login", json={
        "username": "admin_test", "password": "admin123",
    })
    assert res.status_code == 200
    body = res.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "admin_test"
    assert body["user"]["role"] == "admin"
    assert body["user"]["is_active"] is True


@pytest.mark.asyncio
async def test_login_wrong_password(client, admin_token):
    """POST /api/auth/login — wrong password → 401."""
    res = await client.post("/api/auth/login", json={
        "username": "admin_test", "password": "wrongpass",
    })
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client, admin_token):
    """POST /api/auth/login — nonexistent user → 401."""
    res = await client.post("/api/auth/login", json={
        "username": "no_such_user_999", "password": "whatever",
    })
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_login_disabled_user(client):
    """POST /api/auth/login — disabled user → 403."""
    # Create a disabled user directly via DB
    from app.database import async_session
    from app.models.user import User, UserRole
    from app.services.auth import hash_password
    import uuid

    uid = str(uuid.uuid4())
    async with async_session() as db:
        u = User(
            id=uid, username="disabled_user",
            hashed_password=hash_password("pass1234"),
            display_name="Disabled", role=UserRole.USER,
            is_active=False, tenant_id=str(uuid.uuid4()),
        )
        db.add(u)
        await db.commit()

    res = await client.post("/api/auth/login", json={
        "username": "disabled_user", "password": "pass1234",
    })
    assert res.status_code == 403
    assert "USER_DISABLED" in res.json()["detail"]


@pytest.mark.asyncio
async def test_login_missing_username(client):
    """POST /api/auth/login — missing username → 422."""
    res = await client.post("/api/auth/login", json={
        "password": "admin123",
    })
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_login_password_too_short(client):
    """POST /api/auth/login — password ≤ 3 chars → 422 (min_length=4)."""
    res = await client.post("/api/auth/login", json={
        "username": "admin_test", "password": "abc",
    })
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_me(client, admin_token):
    """GET /api/auth/me — valid token → 200 + user info."""
    res = await client.get("/api/auth/me", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == "admin_test"
    assert body["role"] == "admin"
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_update_me_display_name(client, admin_token):
    """PUT /api/auth/me — update display_name → 200."""
    res = await client.put("/api/auth/me", json={
        "display_name": "New Display Name",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["display_name"] == "New Display Name"


@pytest.mark.asyncio
async def test_get_me_no_token(client):
    """GET /api/auth/me — no token → 401."""
    res = await client.get("/api/auth/me")
    assert res.status_code == 401

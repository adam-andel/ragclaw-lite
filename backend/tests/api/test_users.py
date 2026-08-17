"""API contract tests: /api/users endpoints."""

import pytest
import uuid


@pytest.mark.asyncio
async def test_list_users_admin(client, admin_token):
    """GET /api/users — admin sees all users including admin_test."""
    res = await client.get("/api/users", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    usernames = [u["username"] for u in body]
    assert "admin_test" in usernames


@pytest.mark.asyncio
async def test_list_users_moderator(client, moderator_token):
    """GET /api/users — moderator sees only USER-role users."""
    res = await client.get("/api/users", headers={
        "Authorization": f"Bearer {moderator_token}",
    })
    assert res.status_code == 200
    body = res.json()
    # Moderator should only see USER role
    for u in body:
        assert u["role"] == "user", f"moderator saw non-USER role: {u}"


@pytest.mark.asyncio
async def test_list_users_forbidden(client, user_token):
    """GET /api/users — normal user → 403."""
    res = await client.get("/api/users", headers={
        "Authorization": f"Bearer {user_token}",
    })
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_list_users_no_token(client):
    """GET /api/users — no token → 401."""
    res = await client.get("/api/users")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_create_user_admin(client, admin_token):
    """POST /api/users — admin creates a normal user → 201."""
    username = f"new_user_{uuid.uuid4().hex[:8]}"
    res = await client.post("/api/users", json={
        "username": username, "password": "pass1234",
        "display_name": "New User", "role": "user",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 201
    body = res.json()
    assert body["username"] == username
    assert body["role"] == "user"


@pytest.mark.asyncio
async def test_create_moderator_admin(client, admin_token):
    """POST /api/users — admin creates a moderator → 201."""
    username = f"new_mod_{uuid.uuid4().hex[:8]}"
    res = await client.post("/api/users", json={
        "username": username, "password": "pass1234",
        "display_name": "New Mod", "role": "moderator",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 201
    body = res.json()
    assert body["role"] == "moderator"


@pytest.mark.asyncio
async def test_create_admin_by_moderator_forbidden(client, moderator_token):
    """POST /api/users — moderator cannot create admin → 403."""
    username = f"bad_admin_{uuid.uuid4().hex[:8]}"
    res = await client.post("/api/users", json={
        "username": username, "password": "pass1234",
        "display_name": "Bad Admin", "role": "admin",
    }, headers={"Authorization": f"Bearer {moderator_token}"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_create_duplicate_username(client, admin_token):
    """POST /api/users — duplicate username → 400."""
    username = f"dup_{uuid.uuid4().hex[:8]}"
    # First creation succeeds
    res1 = await client.post("/api/users", json={
        "username": username, "password": "pass1234",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res1.status_code == 201
    # Second → conflict
    res2 = await client.post("/api/users", json={
        "username": username, "password": "pass1234",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res2.status_code == 400


@pytest.mark.asyncio
async def test_create_invalid_role(client, admin_token):
    """POST /api/users — invalid role → 400."""
    username = f"badrole_{uuid.uuid4().hex[:8]}"
    res = await client.post("/api/users", json={
        "username": username, "password": "pass1234",
        "role": "superadmin",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_get_user_by_id_admin(client, admin_token):
    """GET /api/users/{user_id} — admin can view any user → 200."""
    # First create a user, then look them up
    username = f"lookup_{uuid.uuid4().hex[:8]}"
    create_res = await client.post("/api/users", json={
        "username": username, "password": "pass1234",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    user_id = create_res.json()["id"]

    res = await client.get(f"/api/users/{user_id}", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    assert res.json()["username"] == username


@pytest.mark.asyncio
async def test_update_user_role_admin(client, admin_token):
    """PUT /api/users/{user_id} — admin changes user role → 200."""
    # Create a user
    username = f"rolechange_{uuid.uuid4().hex[:8]}"
    create_res = await client.post("/api/users", json={
        "username": username, "password": "pass1234",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    user_id = create_res.json()["id"]
    assert create_res.json()["role"] == "user"

    # Elevate to moderator
    res = await client.put(f"/api/users/{user_id}", json={
        "role": "moderator",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    assert res.json()["role"] == "moderator"


@pytest.mark.asyncio
async def test_delete_self_forbidden(client, admin_token):
    """DELETE /api/users/{self_id} — cannot delete self → 400."""
    # Decode token to get admin user id
    from app.services.auth import decode_token
    payload = decode_token(admin_token)
    admin_id = payload["sub"]

    res = await client.delete(f"/api/users/{admin_id}", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 400
    assert "USER_CANNOT_DELETE_SELF" in res.json()["detail"]

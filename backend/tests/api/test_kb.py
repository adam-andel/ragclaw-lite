"""API contract tests: /api/kb endpoints."""

import pytest
import uuid


@pytest.mark.asyncio
async def test_create_kb_admin(client, admin_token):
    """POST /api/kb — admin creates KB → 201 + id/name."""
    res = await client.post("/api/kb", json={
        "name": "测试知识库A",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 201
    body = res.json()
    assert body["id"]
    assert body["name"] == "测试知识库A"


@pytest.mark.asyncio
async def test_create_kb_user_forbidden(client, user_token):
    """POST /api/kb — normal user → 403."""
    res = await client.post("/api/kb", json={
        "name": "用户知识库",
    }, headers={"Authorization": f"Bearer {user_token}"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_create_kb_no_name(client, admin_token):
    """POST /api/kb — missing name → 422 (KBCreate.name min_length=1)."""
    res = await client.post("/api/kb", json={}, headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_list_kbs_admin(client, admin_token, test_kb):
    """GET /api/kb — admin sees full list including test_kb."""
    res = await client.get("/api/kb", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    kb_ids = [kb["id"] for kb in body]
    assert test_kb["id"] in kb_ids


@pytest.mark.asyncio
async def test_list_kbs_user_only_own(client, user_token, test_kb):
    """GET /api/kb — normal user sees only own KBs (test_kb owned by admin)."""
    res = await client.get("/api/kb", headers={
        "Authorization": f"Bearer {user_token}",
    })
    assert res.status_code == 200
    body = res.json()
    # Normal user should NOT see admin's test_kb unless explicitly shared
    kb_ids = [kb["id"] for kb in body]
    assert test_kb["id"] not in kb_ids


@pytest.mark.asyncio
async def test_get_kb_exists(client, admin_token, test_kb):
    """GET /api/kb/{kb_id} — existing KB → 200."""
    res = await client.get(f"/api/kb/{test_kb['id']}", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    assert res.json()["name"] == test_kb["name"]


@pytest.mark.asyncio
async def test_get_kb_not_found(client, admin_token):
    """GET /api/kb/{kb_id} — non-existent KB → 404."""
    fake_id = str(uuid.uuid4())
    res = await client.get(f"/api/kb/{fake_id}", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_kb_admin(client, admin_token):
    """DELETE /api/kb/{kb_id} — admin (owner) deletes → 200."""
    # Create KB first
    create_res = await client.post("/api/kb", json={
        "name": "待删除",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    kb_id = create_res.json()["id"]

    res = await client.delete(f"/api/kb/{kb_id}", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_kb_not_owner_moderator(client, admin_token, moderator_token):
    """DELETE /api/kb/{kb_id} — moderator (non-owner) cannot delete admin's KB → 403."""
    # Admin creates KB
    create_res = await client.post("/api/kb", json={
        "name": "Admin的KB",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    kb_id = create_res.json()["id"]

    # Moderator tries to delete it
    res = await client.delete(f"/api/kb/{kb_id}", headers={
        "Authorization": f"Bearer {moderator_token}",
    })
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_delete_kb_not_found(client, admin_token):
    """DELETE /api/kb/{kb_id} — non-existent KB → 404."""
    fake_id = str(uuid.uuid4())
    res = await client.delete(f"/api/kb/{fake_id}", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 404

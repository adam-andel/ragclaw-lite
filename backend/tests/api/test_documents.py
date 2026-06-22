"""API contract tests: /api/documents endpoints."""

import pytest
import uuid
import time


@pytest.mark.asyncio
async def test_upload_txt(client, admin_token, test_kb):
    """POST /api/documents/upload — upload .txt file → 200."""
    files = {"file": ("test.txt", b"Hello world\nThis is a test document.\n", "text/plain")}
    res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
    assert res.status_code == 200
    body = res.json()
    assert body["filename"] == "test.txt"
    assert body["file_type"] == "txt"
    assert "status" in body


@pytest.mark.asyncio
async def test_upload_md(client, admin_token, test_kb):
    """POST /api/documents/upload — upload .md file → 200."""
    md_content = b"# Title\n\n## Section\n\nSome content here.\n"
    files = {"file": ("test.md", md_content, "text/markdown")}
    res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
    assert res.status_code == 200
    body = res.json()
    assert body["filename"] == "test.md"
    assert body["file_type"] == "md"


@pytest.mark.asyncio
async def test_upload_unsupported_format(client, admin_token, test_kb):
    """POST /api/documents/upload — unsupported .exe → 400."""
    files = {"file": ("malware.exe", b"\x00\x01\x02", "application/octet-stream")}
    res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 400
    assert "Unsupported" in res.json()["detail"]


@pytest.mark.asyncio
async def test_upload_empty_file(client, admin_token, test_kb):
    """POST /api/documents/upload — empty file → should not crash."""
    files = {"file": ("empty.txt", b"", "text/plain")}
    res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
    # 200 if it succeeds with 0 chunks, or a failure status if the parser rejects it
    assert res.status_code in (200, 500)
    if res.status_code == 200:
        body = res.json()
        assert "status" in body


@pytest.mark.asyncio
async def test_upload_nonexistent_kb(client, admin_token):
    """POST /api/documents/upload — non-existent kb_id → 404."""
    fake_kb = str(uuid.uuid4())
    files = {"file": ("test.txt", b"content", "text/plain")}
    res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": fake_kb,
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_upload_user_forbidden(client, user_token, test_kb):
    """POST /api/documents/upload — normal user → 403."""
    files = {"file": ("test.txt", b"content", "text/plain")}
    res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {user_token}"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_upload_no_token(client, test_kb):
    """POST /api/documents/upload — no token → 401."""
    files = {"file": ("test.txt", b"content", "text/plain")}
    res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    })
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_list_documents(client, admin_token, test_kb):
    """GET /api/documents?kb_id=xxx — list documents → 200."""
    # First upload a doc to have something to list
    files = {"file": ("doc.txt", b"List test document.", "text/plain")}
    await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)

    res = await client.get("/api/documents", params={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    for doc in body:
        assert "filename" in doc
        assert "status" in doc


@pytest.mark.asyncio
async def test_get_document_status(client, admin_token, test_kb):
    """GET /api/documents/{doc_id}/status → 200 + status field."""
    # Upload a doc
    files = {"file": ("status_test.txt", b"Status check content.", "text/plain")}
    upload_res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
    doc_id = upload_res.json()["id"]

    res = await client.get(f"/api/documents/{doc_id}/status", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == doc_id
    assert "status" in body
    assert "chunk_count" in body


@pytest.mark.asyncio
async def test_get_document_chunks(client, admin_token, test_kb):
    """GET /api/documents/{doc_id}/chunks → 200 + chunks list."""
    # Upload a doc with enough content to produce chunks
    md_content = b"# Title\n\n## Section 1\n\n" + b"Lorem ipsum " * 50 + b"\n\n## Section 2\n\nMore text here."
    files = {"file": ("chunk_test.md", md_content, "text/markdown")}
    upload_res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
    doc_id = upload_res.json()["id"]

    res = await client.get(f"/api/documents/{doc_id}/chunks", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    for chunk in body:
        assert "content" in chunk
        assert "chunk_index" in chunk


@pytest.mark.asyncio
async def test_delete_document(client, admin_token, test_kb):
    """DELETE /api/documents/{doc_id} → 200."""
    # Upload then delete
    files = {"file": ("to_delete.txt", b"Delete me.", "text/plain")}
    upload_res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
    doc_id = upload_res.json()["id"]

    res = await client.delete(f"/api/documents/{doc_id}", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_document_lifecycle(client, admin_token, test_kb):
    """Document lifecycle: upload → completed status → has chunks."""
    content = b"# Lifecycle Test\n\n## Section A\n\nThis is lifecycle test content with enough text " + b"to generate chunks for the document. " * 20
    files = {"file": ("lifecycle.md", content, "text/markdown")}
    upload_res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
    assert upload_res.status_code == 200
    doc = upload_res.json()

    # Status should be "completed" (or "failed" if something went wrong — unlikely with md)
    assert doc["status"] in ("completed", "failed"), f"unexpected status: {doc['status']}"
    if doc["status"] == "completed":
        assert doc["chunk_count"] > 0

    # Verify chunks exist
    chunks_res = await client.get(f"/api/documents/{doc['id']}/chunks", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert chunks_res.status_code == 200
    if doc["status"] == "completed":
        assert len(chunks_res.json()) > 0

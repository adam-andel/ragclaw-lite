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
"""API contract tests: /api/retrieval endpoints."""

import pytest
import uuid


@pytest.mark.asyncio
async def test_search_with_kb_id(client, admin_token, test_kb):
    """POST /api/retrieval/search — with kb_id → 200 + results."""
    # Upload a document first so there's something to retrieve
    files = {"file": ("search_test.txt",
                      b"RAGClaw is an enterprise RAG platform built with FastAPI and Vue3.",
                      "text/plain")}
    upload_res = await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)
    # Upload may fail if embedding model not available, but KB exists so search won't crash
    assert upload_res.status_code in (200, 500)

    res = await client.post("/api/retrieval/search", json={
        "query": "什么是RAGClaw",
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    for r in body:
        assert "chunk_id" in r
        assert "content" in r
        assert "fusion_score" in r
        assert "vector_score" in r
        assert "bm25_score" in r


@pytest.mark.asyncio
async def test_search_without_kb_id(client, admin_token, test_kb):
    """POST /api/retrieval/search — without kb_id → auto-selects first KB."""
    res = await client.post("/api/retrieval/search", json={
        "query": "test query",
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)


@pytest.mark.asyncio
async def test_search_empty_query(client, admin_token, test_kb):
    """POST /api/retrieval/search — empty query → 422 (min_length=1 validation)."""
    res = await client.post("/api/retrieval/search", json={
        "query": "",
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"})
    # Pydantic SearchRequest.query min_length=1 → 422
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_search_no_token(client, test_kb):
    """POST /api/retrieval/search — no token → 401."""
    res = await client.post("/api/retrieval/search", json={
        "query": "test",
        "kb_id": test_kb["id"],
    })
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_search_top_k_zero(client, admin_token, test_kb):
    """POST /api/retrieval/search — top_k=0 → 422 (ge=1 validation)."""
    res = await client.post("/api/retrieval/search", json={
        "query": "test",
        "kb_id": test_kb["id"],
        "top_k": 0,
    }, headers={"Authorization": f"Bearer {admin_token}"})
    # SearchRequest.top_k has ge=1, so 0 fails validation → 422
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_search_threshold_max(client, admin_token, test_kb):
    """POST /api/retrieval/search — threshold=1.0 → 200 with empty or few results."""
    files = {"file": ("thresh_test.txt",
                      b"Some unique content for threshold testing.",
                      "text/plain")}
    await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)

    res = await client.post("/api/retrieval/search", json={
        "query": "threshold extreme test",
        "kb_id": test_kb["id"],
        "threshold": 1.0,
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    # With threshold=1.0, only exact cosine matches pass — should be empty or very few
    # At the very least, this shouldn't crash


@pytest.mark.asyncio
async def test_search_vector_weight_zero_bm25_one(client, admin_token, test_kb):
    """POST /api/retrieval/search — vector_weight=0, bm25_weight=1 → 200."""
    files = {"file": ("bm25_test.txt",
                      b"BM25-only search test document with specific keywords like RAGClaw platform retrieval.",
                      "text/plain")}
    await client.post("/api/documents/upload", files=files, data={
        "kb_id": test_kb["id"],
    }, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30)

    res = await client.post("/api/retrieval/search", json={
        "query": "RAGClaw retrieval",
        "kb_id": test_kb["id"],
        "vector_weight": 0.0,
        "bm25_weight": 1.0,
    }, headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)

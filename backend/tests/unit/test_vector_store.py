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
"""Unit tests for ChromaDB vector store (requires embedder model)."""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services.vector_store import vector_store


# ---------------------------------------------------------------------------
# Model availability check (do once per module)
# ---------------------------------------------------------------------------

def _model_available() -> bool:
    """Check if BGE embedder model can be loaded."""
    try:
        from app.services.embedder import embedder_service
        embedder_service.embed(["quick check"])
        return True
    except Exception:
        return False


_MODEL_OK = None


def _skip_if_no_model():
    global _MODEL_OK
    if _MODEL_OK is None:
        _MODEL_OK = _model_available()
    if not _MODEL_OK:
        pytest.skip("BGE embedding model not available")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kid() -> str:
    return f"test-kb-{uuid.uuid4().hex[:8]}"


def _chunk(cid: str = None, content: str = "hello world", doc_id: str = "d1") -> dict:
    if cid is None:
        cid = str(uuid.uuid4())
    return {
        "id": cid, "content": content, "token_count": 10,
        "heading": "Test", "page": 1, "chunk_index": 0,
        "doc_id": doc_id, "filename": "test.md",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVectorStore:
    """Core add / search / count / delete operations."""

    def test_add_chunks_increases_count(self):
        _skip_if_no_model()
        kb = _kid()
        try:
            vector_store.add_chunks(kb, [_chunk(), _chunk()])
            assert vector_store.count(kb) == 2
        finally:
            vector_store.delete_collection(kb)

    def test_search_finds_added_chunk(self):
        _skip_if_no_model()
        kb = _kid()
        try:
            vector_store.add_chunks(kb, [_chunk(content="RAGClaw is an enterprise RAG platform")])
            results = vector_store.search(kb, "enterprise RAG", top_k=3)
            assert len(results) > 0
            assert any("RAGClaw" in r.get("content", "") for r in results)
        finally:
            vector_store.delete_collection(kb)

    def test_search_empty_query_no_crash(self):
        _skip_if_no_model()
        kb = _kid()
        try:
            vector_store.add_chunks(kb, [_chunk()])
            results = vector_store.search(kb, "", top_k=3)
            assert isinstance(results, list)
        finally:
            vector_store.delete_collection(kb)

    def test_delete_by_doc_reduces_count(self):
        _skip_if_no_model()
        kb = _kid()
        doc_a = "doc_a"
        doc_b = "doc_b"
        try:
            vector_store.add_chunks(kb, [
                _chunk(doc_id=doc_a, content="Document A content"),
                _chunk(doc_id=doc_b, content="Document B content"),
            ])
            assert vector_store.count(kb) == 2
            vector_store.delete_by_doc(kb, doc_a)
            # doc_a should be gone; count should decrease
            assert vector_store.count(kb) <= 1
        finally:
            vector_store.delete_collection(kb)

    def test_delete_collection_clears_all(self):
        _skip_if_no_model()
        kb = _kid()
        vector_store.add_chunks(kb, [_chunk(), _chunk(), _chunk()])
        assert vector_store.count(kb) == 3
        vector_store.delete_collection(kb)
        # After delete, ChromaDB may raise or return 0 — we accept both
        count = vector_store.count(kb)
        assert count == 0

    def test_threshold_filters_out(self):
        _skip_if_no_model()
        kb = _kid()
        try:
            vector_store.add_chunks(kb, [_chunk(content="unique target phrase for testing")])
            # threshold=0.99 is almost impossible to match
            results = vector_store.search(kb, "target phrase", top_k=5, threshold=0.99)
            assert len(results) == 0
        finally:
            vector_store.delete_collection(kb)

    def test_top_k_limit(self):
        _skip_if_no_model()
        kb = _kid()
        try:
            chunks = [_chunk(content=f"chunk number {i} has some unique words") for i in range(5)]
            vector_store.add_chunks(kb, chunks)
            results = vector_store.search(kb, "chunk unique words", top_k=2)
            assert len(results) <= 2
        finally:
            vector_store.delete_collection(kb)

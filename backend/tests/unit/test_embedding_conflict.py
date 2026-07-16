"""Unit tests for the embedding-model switch guard (_embedding_conflict).

Strategy (per request): use a *real in-memory Chroma* — driven by the conftest
``_isolate_data`` autouse fixture which redirects ``settings.chroma_path`` into a
per-test temp dir — to power ``VectorStore.total_vector_count()``; and **stub**
``VectorStore.stored_embed_info`` so the stored ``(model, backend)`` identity can
be controlled directly without ever loading the BGE model.

No real embedding model is required:
  * ``known_dimension`` / ``model_manager.model_dimension`` resolve the 5 curated
    BGE dims from the registry (no install needed) — so the ``dim_conflict`` branch
    is fully exercisable offline.
  * ``embedder_service.BACKEND`` is just a string constant (``"torch"``) — read, not loaded.
"""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services.vector_store import vector_store
from app.services.config_manager import config_manager
from app.services.embedder import embedder_service
from app.routers.embedding_model import _embedding_conflict, _conflict_detail

# Curated model ids (dimension noted in comment)
SMALL_ZH = "BAAI/bge-small-zh-v1.5"   # 512
SMALL_EN = "BAAI/bge-small-en-v1.5"   # 512
LARGE_ZH = "BAAI/bge-large-zh-v1.5"   # 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kid() -> str:
    return f"kb-{uuid.uuid4().hex[:10]}"


def _seed(kb_id: str, n: int = 2, dim: int = 512):
    """Insert N synthetic (fake) vectors into in-memory Chroma.

    Uses ``add_chunks_cached`` so no real model/embed call is made; the vectors
    only exist to make ``total_vector_count()`` return > 0. Their actual metadata
    stamp is irrelevant because ``stored_embed_info`` is stubbed in every test.
    """
    chunks = []
    for i in range(n):
        chunks.append({
            "id": f"{kb_id}-c{i}",
            "content": f"chunk {i}",
            "embedding": [0.01 * ((j % 3) - 1) for j in range(dim)],
            "doc_id": "d1", "heading": "H", "page": 1, "chunk_index": i,
            "token_count": 5, "filename": "x.md",
        })
    vector_store.add_chunks_cached(kb_id, chunks)


def _set_configured(monkeypatch, model: str):
    """Patch config_manager.embedding_model (the currently configured model)."""
    monkeypatch.setattr(
        type(config_manager), "embedding_model",
        property(lambda self: model), raising=False,
    )


def _stub_stored(monkeypatch, model, backend):
    """Stub VectorStore.stored_embed_info to return a controlled (model, backend)."""
    monkeypatch.setattr(vector_store, "stored_embed_info", lambda: (model, backend))


# ---------------------------------------------------------------------------
# _embedding_conflict — pure function unit tests
# ---------------------------------------------------------------------------

class TestEmbeddingConflict:
    def test_no_vectors_no_conflict(self, monkeypatch):
        _stub_stored(monkeypatch, None, None)
        info = _embedding_conflict(SMALL_ZH)
        assert info["total"] == 0
        assert info["conflict"] is False
        assert info["dim_conflict"] is False
        assert info["source_conflict"] is False

    def test_same_model_same_backend_no_conflict(self, monkeypatch):
        _set_configured(monkeypatch, SMALL_ZH)
        _stub_stored(monkeypatch, SMALL_ZH, embedder_service.BACKEND)
        _seed(_kid())
        info = _embedding_conflict(SMALL_ZH)
        assert info["total"] > 0
        assert info["conflict"] is False
        assert info["dim_conflict"] is False
        assert info["source_conflict"] is False

    def test_same_dim_diff_model_source_conflict(self, monkeypatch):
        # The regression case: 512 -> 512 previously slipped through silently and
        # silently corrupted retrieval. The source guard must now flag it.
        _set_configured(monkeypatch, SMALL_ZH)
        _stub_stored(monkeypatch, SMALL_ZH, embedder_service.BACKEND)
        _seed(_kid())
        info = _embedding_conflict(SMALL_EN)
        assert info["dim_conflict"] is False
        assert info["source_conflict"] is True
        assert info["conflict"] is True

    def test_diff_dim_dim_conflict(self, monkeypatch):
        # configured=small-zh(512), target=large-zh(1024); stored model matches
        # target so only the dimension branch fires (isolated).
        _set_configured(monkeypatch, SMALL_ZH)
        _stub_stored(monkeypatch, LARGE_ZH, embedder_service.BACKEND)
        _seed(_kid())
        info = _embedding_conflict(LARGE_ZH)
        assert info["dim_conflict"] is True
        assert info["source_conflict"] is False
        assert info["conflict"] is True

    def test_backend_switch_source_conflict(self, monkeypatch):
        # Future ONNX path: same model id, backend differs ("torch" -> "onnx").
        _set_configured(monkeypatch, SMALL_ZH)
        _stub_stored(monkeypatch, SMALL_ZH, "onnx")
        _seed(_kid())
        info = _embedding_conflict(SMALL_ZH)
        assert info["dim_conflict"] is False
        assert info["source_conflict"] is True
        assert info["conflict"] is True

    def test_legacy_missing_stamp_no_source_conflict(self, monkeypatch):
        # Per product decision: legacy (unstamped) vectors are ignored for the
        # source check — they must NOT trigger source_conflict.
        _set_configured(monkeypatch, SMALL_ZH)
        _stub_stored(monkeypatch, None, None)
        _seed(_kid())
        info = _embedding_conflict(SMALL_EN)  # 512 -> 512, but legacy
        assert info["source_conflict"] is False
        assert info["dim_conflict"] is False
        assert info["conflict"] is False

    def test_legacy_with_dim_conflict_still_conflicts(self, monkeypatch):
        # Legacy vectors are still protected by the dimension check.
        _set_configured(monkeypatch, SMALL_ZH)   # 512
        _stub_stored(monkeypatch, None, None)
        _seed(_kid())
        info = _embedding_conflict(LARGE_ZH)      # 1024
        assert info["dim_conflict"] is True
        assert info["source_conflict"] is False
        assert info["conflict"] is True

    def test_backend_constant_locked(self):
        # Guard against an accidental flip of the backend identity.
        assert isinstance(embedder_service.BACKEND, str) and embedder_service.BACKEND


# ---------------------------------------------------------------------------
# _conflict_detail — 409 response shape
# ---------------------------------------------------------------------------

class TestConflictDetail:
    def test_detail_source_conflict(self, monkeypatch):
        info = {
            "total": 5, "existing_dim": 512, "new_dim": 512,
            "dim_conflict": False, "source_conflict": True,
            "stored_model": SMALL_ZH, "stored_backend": embedder_service.BACKEND,
        }
        detail = _conflict_detail(SMALL_EN, info)
        assert detail["conflict"] is True
        assert detail["source_conflict"] is True
        assert detail["dim_conflict"] is False
        assert detail["new_model"] == SMALL_EN
        assert detail["new_backend"] == embedder_service.BACKEND
        assert detail["existing_model"] == SMALL_ZH
        assert "来源" in detail["message"]

    def test_detail_dim_conflict(self, monkeypatch):
        info = {
            "total": 3, "existing_dim": 512, "new_dim": 1024,
            "dim_conflict": True, "source_conflict": False,
            "stored_model": SMALL_ZH, "stored_backend": embedder_service.BACKEND,
        }
        detail = _conflict_detail(LARGE_ZH, info)
        assert detail["dim_conflict"] is True
        assert "维度" in detail["message"]


# ---------------------------------------------------------------------------
# Endpoint-level integration (proves the guard actually blocks /check)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCheckEndpoint:
    async def test_check_409_on_source_conflict(self, admin_client, monkeypatch):
        client, headers = admin_client
        _set_configured(monkeypatch, SMALL_ZH)
        _stub_stored(monkeypatch, SMALL_ZH, embedder_service.BACKEND)
        _seed(_kid())
        resp = await client.post(
            "/api/embedding-model/check",
            json={"model": SMALL_EN}, headers=headers,
        )
        assert resp.status_code == 409
        body = resp.json()["detail"]
        assert body["source_conflict"] is True
        assert body["conflict"] is True
        assert body["new_model"] == SMALL_EN

    async def test_check_200_when_compatible(self, admin_client, monkeypatch):
        client, headers = admin_client
        _set_configured(monkeypatch, SMALL_ZH)
        _stub_stored(monkeypatch, SMALL_ZH, embedder_service.BACKEND)
        _seed(_kid())
        resp = await client.post(
            "/api/embedding-model/check",
            json={"model": SMALL_ZH}, headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["conflict"] is False
        assert body["source_conflict"] is False

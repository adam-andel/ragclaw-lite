"""Unit tests for hybrid_search top_k boundary handling.

Covers the two fixes from the review:
1. ``HybridSearchService.fuse`` must reject a negative ``final_top_k`` (which
   would otherwise produce ``results[:-1]`` and silently drop the last chunk).
2. ``_execute_hybrid_search`` must clamp a LLM-supplied ``top_k``: non-positive
   values fall back to the system default, and anything above
   ``retrieval_final_top_k`` is capped so the LLM cannot blow up context.

No I/O — fuse is a pure function and the meta-tool's retrieval backend is
mocked, so these run in isolation.

Run:
    cd backend
    python -m pytest tests/unit/test_hybrid_search.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import settings
from app.services.hybrid_search import HybridSearchService
from app.services import agent_nodes as nodes


def _make_vector_results(n: int) -> list[dict]:
    """Build n distinct vector results with descending scores (all above threshold)."""
    return [
        {
            "id": f"v{i}",
            "content": f"content-{i}",
            "score": 1.0 - i * 0.01,
            "metadata": {
                "doc_id": f"doc{i}",
                "filename": f"d{i}",
                "heading": "",
                "chunk_index": i,
                "page": None,
            },
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# fuse() final_top_k clamping
# ---------------------------------------------------------------------------

class TestFuseFinalTopK:
    def _fuse(self, final_top_k):
        return HybridSearchService().fuse(
            _make_vector_results(12), [], final_top_k=final_top_k
        )

    def test_negative_falls_back_to_default(self):
        # Without the fix, final_top_k=-1 slices results[:-1] -> 11 chunks.
        out = self._fuse(final_top_k=-1)
        assert len(out) == settings.retrieval_final_top_k  # 10, not 11

    def test_zero_falls_back_to_default(self):
        out = self._fuse(final_top_k=0)
        assert len(out) == settings.retrieval_final_top_k

    def test_none_falls_back_to_default(self):
        out = self._fuse(final_top_k=None)
        assert len(out) == settings.retrieval_final_top_k

    def test_positive_respected(self):
        out = self._fuse(final_top_k=5)
        assert len(out) == 5


# ---------------------------------------------------------------------------
# _execute_hybrid_search top_k coercion (LLM-controlled input)
# ---------------------------------------------------------------------------

class TestExecuteHybridSearchTopK:
    async def _call(self, monkeypatch, top_k_value):
        """Run _execute_hybrid_search with a mocked retrieval backend; return the top_k it passed through."""
        captured = {}

        async def fake_run(kb_id, query, doc_ids=None, top_k=None):
            captured["top_k"] = top_k
            return ("ctx", [{"doc_id": "d1", "doc_name": "n1"}])

        monkeypatch.setattr(nodes.hybrid_search, "_run_hybrid_retrieval", fake_run)
        args = {"query": "who chaired these meetings", "top_k": top_k_value}
        res = await nodes._execute_hybrid_search({"kb_id": "kb-1"}, args)
        return captured["top_k"], res

    @pytest.mark.asyncio
    async def test_negative_becomes_none(self, monkeypatch):
        top_k, _ = await self._call(monkeypatch, -1)
        assert top_k is None

    @pytest.mark.asyncio
    async def test_zero_becomes_none(self, monkeypatch):
        top_k, _ = await self._call(monkeypatch, 0)
        assert top_k is None

    @pytest.mark.asyncio
    async def test_one_kept(self, monkeypatch):
        top_k, _ = await self._call(monkeypatch, 1)
        assert top_k == 1

    @pytest.mark.asyncio
    async def test_within_range_kept(self, monkeypatch):
        top_k, _ = await self._call(monkeypatch, 5)
        assert top_k == 5

    @pytest.mark.asyncio
    async def test_over_default_capped(self, monkeypatch):
        top_k, _ = await self._call(monkeypatch, 40)
        assert top_k == settings.retrieval_final_top_k

    @pytest.mark.asyncio
    async def test_huge_capped(self, monkeypatch):
        top_k, _ = await self._call(monkeypatch, 999)
        assert top_k == settings.retrieval_final_top_k

    @pytest.mark.asyncio
    async def test_non_numeric_becomes_none(self, monkeypatch):
        top_k, _ = await self._call(monkeypatch, "abc")
        assert top_k is None

    @pytest.mark.asyncio
    async def test_missing_becomes_none(self, monkeypatch):
        captured = {}

        async def fake_run(kb_id, query, doc_ids=None, top_k=None):
            captured["top_k"] = top_k
            return ("ctx", [])

        monkeypatch.setattr(nodes.hybrid_search, "_run_hybrid_retrieval", fake_run)
        res = await nodes._execute_hybrid_search({"kb_id": "kb-1"}, {"query": "q"})
        assert captured["top_k"] is None
        assert res["endpoint"] is None
        assert "[hybrid_search]" in res["result"]

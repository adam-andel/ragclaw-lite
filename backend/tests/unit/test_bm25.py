"""Unit tests for BM25 keyword index (no model required)."""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services.bm25_index import bm25_index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kid() -> str:
    return f"test-kb-{uuid.uuid4().hex[:8]}"


def _chunks(doc_id: str, texts: list[str]) -> list[dict]:
    return [
        {"id": str(uuid.uuid4()), "content": t, "doc_id": doc_id,
         "heading": "Test", "page": 1}
        for t in texts
    ]


# ---------------------------------------------------------------------------

class TestBM25:
    """BM25 index lifecycle: build, search, remove, delete."""

    def test_build_makes_has_index_true(self):
        kb = _kid()
        try:
            bm25_index.build(kb, _chunks("d1", ["hello world", "goodbye world"]))
            assert bm25_index.has_index(kb) is True
        finally:
            bm25_index.delete_kb(kb)

    def test_search_recalls_relevant_chunk(self):
        kb = _kid()
        try:
            bm25_index.build(kb, _chunks("d1", [
                "ERAG is an enterprise RAG platform with hybrid search",
                "Python is a programming language for general purposes",
                "FastAPI is a modern web framework for building APIs",
            ]))
            results = bm25_index.search(kb, "ERAG RAG platform", top_k=3)
            assert len(results) > 0
            assert any("ERAG" in r["content"] for r in results)
        finally:
            bm25_index.delete_kb(kb)

    def test_chinese_word_segmentation(self):
        kb = _kid()
        try:
            # Use larger corpus with repeated terms so BM25 can distinguish relevance
            bm25_index.build(kb, _chunks("d1", [
                "知识图谱是人工智能的重要分支知识图谱",
                "机器学习需要大量训练数据",
                "深度学习依赖神经网络架构",
                "自然语言处理是人工智能的核心方向",
                "今天天气很好适合出去玩",
                "没有什么相关的内容在这里",
            ]))
            results = bm25_index.search(kb, "知识图谱 人工智能", top_k=4)
            assert len(results) > 0
            assert any("知识图谱" in r["content"] for r in results)
        finally:
            bm25_index.delete_kb(kb)

    def test_empty_chunks_means_no_index(self):
        kb = _kid()
        bm25_index.build(kb, [])
        assert bm25_index.has_index(kb) is False

    def test_remove_doc_excludes_its_chunks(self):
        kb = _kid()
        try:
            bm25_index.build(kb, [
                {"id": "c1", "content": "important ERAG document", "doc_id": "doc_a", "heading": "H1", "page": 1},
                {"id": "c2", "content": "irrelevant other content", "doc_id": "doc_b", "heading": "H1", "page": 1},
            ])
            bm25_index.remove_doc(kb, "doc_a")
            results = bm25_index.search(kb, "ERAG document", top_k=5)
            # doc_a chunks should be gone, so ERAG shouldn't match
            assert not any("ERAG" in r["content"] for r in results)
        finally:
            bm25_index.delete_kb(kb)

    def test_delete_kb_clears_index(self):
        kb = _kid()
        bm25_index.build(kb, _chunks("d1", ["hello world"]))
        assert bm25_index.has_index(kb) is True
        bm25_index.delete_kb(kb)
        assert bm25_index.has_index(kb) is False

    def test_results_sorted_by_score_descending(self):
        kb = _kid()
        try:
            bm25_index.build(kb, _chunks("d1", [
                "ERAG ERAG ERAG ERAG ERAG",
                "ERAG is a platform",
                "something completely unrelated",
            ]))
            results = bm25_index.search(kb, "ERAG", top_k=5)
            if len(results) >= 2:
                scores = [r["score"] for r in results]
                assert scores == sorted(scores, reverse=True), f"Scores not descending: {scores}"
        finally:
            bm25_index.delete_kb(kb)

    def test_no_match_returns_empty(self):
        kb = _kid()
        try:
            bm25_index.build(kb, _chunks("d1", ["hello world", "goodbye moon"]))
            results = bm25_index.search(kb, "xyznonexistent123", top_k=5)
            assert results == []
        finally:
            bm25_index.delete_kb(kb)

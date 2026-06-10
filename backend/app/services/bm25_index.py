"""BM25 keyword-based retrieval index using jieba + rank_bm25."""

import jieba
from rank_bm25 import BM25Okapi
from collections import defaultdict


class BM25Index:
    """Per-knowledge-base BM25 index for keyword-based retrieval.

    Maintains an in-memory index, rebuilt from SQLite on restart.
    """

    def __init__(self):
        self._indexes: dict[str, dict] = {}  # kb_id -> {bm25, chunks}

    def build(self, kb_id: str, chunks: list[dict]):
        """Build/rebuild the BM25 index for a knowledge base.

        Args:
            kb_id: Knowledge base ID
            chunks: List of chunk dicts with keys: id, content, doc_id, heading, page
        """
        if not chunks:
            self._indexes.pop(kb_id, None)
            return

        tokenized = [list(jieba.cut(c["content"])) for c in chunks]
        bm25 = BM25Okapi(tokenized)

        self._indexes[kb_id] = {
            "bm25": bm25,
            "chunks": chunks,
            "tokenized": tokenized,
        }

    def search(self, kb_id: str, query: str, top_k: int = 20) -> list[dict]:
        """Search for relevant chunks using BM25.

        Returns:
            List of result dicts with keys: chunk_id, content, score, doc_id, heading, page
        """
        if kb_id not in self._indexes:
            return []

        idx = self._indexes[kb_id]
        bm25 = idx["bm25"]
        chunks = idx["chunks"]

        tokenized_query = list(jieba.cut(query))
        scores = bm25.get_scores(tokenized_query)

        # Sort by score descending
        ranked = sorted(
            enumerate(scores), key=lambda x: x[1], reverse=True
        )[:top_k]

        results = []
        max_score = max(scores) if any(s > 0 for s in scores) else 1.0

        for i, score in ranked:
            if score <= 0:
                continue
            chunk = chunks[i]
            results.append({
                "chunk_id": chunk.get("id", ""),
                "content": chunk["content"],
                "score": score / max_score,  # normalize to 0-1
                "doc_id": chunk.get("doc_id", ""),
                "heading": chunk.get("heading", ""),
                "page": chunk.get("page"),
            })

        return results

    def remove_doc(self, kb_id: str, doc_id: str):
        """Remove a document's chunks from the index."""
        if kb_id not in self._indexes:
            return
        idx = self._indexes[kb_id]
        idx["chunks"] = [c for c in idx["chunks"] if c.get("doc_id") != doc_id]
        self.build(kb_id, idx["chunks"])

    def delete_kb(self, kb_id: str):
        """Delete the index for a knowledge base."""
        self._indexes.pop(kb_id, None)

    def has_index(self, kb_id: str) -> bool:
        return kb_id in self._indexes


# Singleton
bm25_index = BM25Index()

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

    def add(self, kb_id: str, chunks: list[dict]):
        """Incrementally extend an existing index with NEW chunks (no full re-tokenize).

        Only the new chunks are jieba-tokenized; the existing in-memory corpus is
        reused. This is the workhorse for memory archiving: the old code rebuilt the
        whole index from every persisted row on each archive, which re-ran jieba on
        all N chunks every time -> O(n^2) tokenization as memory grows. Incremental
        `add` makes the total jieba work O(n). De-dupes by chunk id so a retried
        archive cannot double-insert.

        Falls back to :meth:`build` when the KB has no index yet (cold start / first
        archive this process) so pre-existing persisted rows are never dropped from
        recall.
        """
        if not chunks:
            return
        if kb_id not in self._indexes:
            self.build(kb_id, chunks)
            return
        idx = self._indexes[kb_id]
        existing_ids = {c.get("id") for c in idx["chunks"]}
        new = [c for c in chunks if c.get("id") not in existing_ids]
        if not new:
            return
        new_tokenized = [list(jieba.cut(c["content"])) for c in new]
        all_tokenized = idx["tokenized"] + new_tokenized
        bm25 = BM25Okapi(all_tokenized)
        self._indexes[kb_id] = {
            "bm25": bm25,
            "chunks": idx["chunks"] + new,
            "tokenized": all_tokenized,
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
                # Preserve chunk_index & filename so downstream citation
                # metadata stays correct (otherwise hybrid_search falls
                # back to chunk_index=0 and doc_id[:8], making distinct
                # chunks from the same doc look like duplicate sources).
                "chunk_index": chunk.get("chunk_index", 0),
                "filename": chunk.get("filename", ""),
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

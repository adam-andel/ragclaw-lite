"""Hybrid search combining vector + BM25 via Reciprocal Rank Fusion (RRF)."""

from app.config import settings
from app.services.vector_store import vector_store
from app.services.bm25_index import bm25_index


class HybridSearchService:
    """Combines vector search and BM25 keyword search with RRF fusion."""

    def search(
        self,
        kb_id: str,
        query: str,
        vector_top_k: int | None = None,
        bm25_top_k: int | None = None,
        final_top_k: int | None = None,
        vector_weight: float = 0.5,
        bm25_weight: float = 0.5,
        threshold: float | None = None,
        doc_ids: list[str] | None = None,
    ) -> list[dict]:
        """Execute hybrid search with weighted RRF fusion.

        Runs vector + BM25 sequentially. Kept for synchronous callers (retrieval
        router, tests). Async callers that want vector/BM25 to run concurrently
        should run both searches themselves and call `fuse()` directly — see
        `parallel_retrieval_node` for the pattern.

        Returns:
            List of result dicts with: chunk_id, content, doc_name,
            vector_score, bm25_score, fusion_score, heading, page
        """
        vector_top_k = vector_top_k or settings.retrieval_vector_top_k
        bm25_top_k = bm25_top_k or settings.retrieval_bm25_top_k

        # Vector search may fail (e.g. embedding model not installed) — degrade
        # gracefully and rely on BM25 only instead of erroring the whole request.
        try:
            vector_results = vector_store.search(kb_id, query, top_k=vector_top_k)
        except Exception:
            vector_results = []

        bm25_results = bm25_index.search(kb_id, query, top_k=bm25_top_k)

        return self.fuse(
            vector_results,
            bm25_results,
            final_top_k=final_top_k,
            vector_weight=vector_weight,
            bm25_weight=bm25_weight,
            threshold=threshold,
            doc_ids=doc_ids,
        )

    def fuse(
        self,
        vector_results: list[dict],
        bm25_results: list[dict],
        final_top_k: int | None = None,
        vector_weight: float = 0.5,
        bm25_weight: float = 0.5,
        threshold: float | None = None,
        doc_ids: list[str] | None = None,
    ) -> list[dict]:
        """Fuse pre-computed vector + BM25 results via weighted RRF.

        Pure fusion — no I/O. Safe to call directly after running both searches
        concurrently (e.g. `parallel_retrieval_node`), so the slow vector path
        and the fast BM25 path overlap instead of running back-to-back.
        """
        final_top_k = final_top_k or settings.retrieval_final_top_k
        threshold = threshold if threshold is not None else settings.retrieval_similarity_threshold

        # Build lookup: chunk_id -> scores
        scores: dict[str, dict] = {}

        # Vector results
        for r in vector_results:
            cid = r["id"]
            scores[cid] = {
                "chunk_id": cid,
                "content": r["content"],
                "vector_score": r["score"],
                "bm25_score": 0.0,
                "doc_id": r["metadata"].get("doc_id", ""),
                "doc_name": r["metadata"].get("filename") or r["metadata"].get("doc_id", "")[:8],
                "heading": r["metadata"].get("heading", ""),
                "chunk_index": r["metadata"].get("chunk_index", 0),
                "page": r["metadata"].get("page"),
            }

        # BM25 results
        for r in bm25_results:
            cid = r["chunk_id"]
            if cid in scores:
                scores[cid]["bm25_score"] = r["score"]
            else:
                scores[cid] = {
                    "chunk_id": cid,
                    "content": r["content"],
                    "vector_score": 0.0,
                    "bm25_score": r["score"],
                    "doc_id": r.get("doc_id", ""),
                    "doc_name": r.get("filename") or r.get("doc_id", "")[:8],
                    "heading": r.get("heading", ""),
                    "chunk_index": r.get("chunk_index", 0),
                    "page": r.get("page"),
                }

        # Calculate weighted fusion scores
        for cid, s in scores.items():
            vec = s["vector_score"] * vector_weight
            bm = s["bm25_score"] * bm25_weight
            # If one source is missing, don't penalize; use available score directly
            has_vec = s["vector_score"] > 0
            has_bm = s["bm25_score"] > 0
            if has_vec and has_bm:
                s["fusion_score"] = (vec + bm) / (vector_weight + bm25_weight)
            elif has_vec:
                s["fusion_score"] = s["vector_score"]
            elif has_bm:
                s["fusion_score"] = s["bm25_score"]
            else:
                s["fusion_score"] = 0

        # Filter by threshold & doc_ids, sort, limit
        results = list(scores.values())

        if doc_ids:
            results = [r for r in results if r["doc_id"] in doc_ids]

        results = [r for r in results if r["fusion_score"] >= threshold]

        results.sort(key=lambda x: x["fusion_score"], reverse=True)

        return results[:final_top_k]


# Singleton
hybrid_search = HybridSearchService()

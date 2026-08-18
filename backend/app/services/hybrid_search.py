"""Hybrid search combining vector + BM25 via Reciprocal Rank Fusion (RRF)."""

import asyncio
import logging

from app.config import settings
from app.services.vector_store import vector_store
from app.services.bm25_index import bm25_index

logger = logging.getLogger(__name__)


def _resolve_param(kb_config: dict | None, key: str, default):
    """Resolve a retrieval parameter: KB config > explicit arg > global default."""
    if kb_config and kb_config.get(key) is not None:
        return kb_config[key]
    return default


class HybridSearchService:
    """Combines vector search and BM25 keyword search with RRF fusion."""

    def search(
        self,
        kb_id: str,
        query: str,
        vector_top_k: int | None = None,
        bm25_top_k: int | None = None,
        final_top_k: int | None = None,
        vector_weight: float | None = None,
        bm25_weight: float | None = None,
        threshold: float | None = None,
        doc_ids: list[str] | None = None,
        kb_config: dict | None = None,
    ) -> list[dict]:
        """Execute hybrid search with weighted RRF fusion.

        Runs vector + BM25 sequentially. Kept for synchronous callers (retrieval
        router, tests). Async callers that want vector/BM25 to run concurrently
        should run both searches themselves and call `fuse()` directly — see
        `parallel_retrieval_node` for the pattern.

        Args:
            kb_config: Per-KB retrieval config dict. If provided, its values
                override global settings for any non-None field.

        Returns:
            List of result dicts with: chunk_id, content, doc_name,
            vector_score, bm25_score, fusion_score, heading, page
        """
        vector_top_k = vector_top_k or _resolve_param(kb_config, "vector_top_k", settings.retrieval_vector_top_k)
        bm25_top_k = bm25_top_k or _resolve_param(kb_config, "bm25_top_k", settings.retrieval_bm25_top_k)
        vw = _resolve_param(kb_config, "vector_weight", vector_weight if vector_weight is not None else 0.5)
        bw = _resolve_param(kb_config, "bm25_weight", bm25_weight if bm25_weight is not None else 0.5)

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
            vector_weight=vw,
            bm25_weight=bw,
            threshold=threshold,
            doc_ids=doc_ids,
            kb_config=kb_config,
        )

    def fuse(
        self,
        vector_results: list[dict],
        bm25_results: list[dict],
        final_top_k: int | None = None,
        vector_weight: float | None = None,
        bm25_weight: float | None = None,
        threshold: float | None = None,
        doc_ids: list[str] | None = None,
        kb_config: dict | None = None,
    ) -> list[dict]:
        """Fuse pre-computed vector + BM25 results via weighted RRF.

        Pure fusion — no I/O. Safe to call directly after running both searches
        concurrently (e.g. `parallel_retrieval_node`), so the slow vector path
        and the fast BM25 path overlap instead of running back-to-back.
        """
        final_top_k = final_top_k or _resolve_param(kb_config, "final_top_k", settings.retrieval_final_top_k)
        # A truthy negative (e.g. -1) slips through the `or` above and would
        # yield results[:-1] below, silently dropping the last chunk. Reject
        # any non-positive value back to the system default.
        if final_top_k < 1:
            final_top_k = settings.retrieval_final_top_k
        threshold = threshold if threshold is not None else _resolve_param(
            kb_config, "similarity_threshold", settings.retrieval_similarity_threshold
        )
        vw = _resolve_param(kb_config, "vector_weight", vector_weight if vector_weight is not None else 0.5)
        bw = _resolve_param(kb_config, "bm25_weight", bm25_weight if bm25_weight is not None else 0.5)

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
            vec = s["vector_score"] * vw
            bm = s["bm25_score"] * bw
            # If one source is missing, don't penalize; use available score directly
            has_vec = s["vector_score"] > 0
            has_bm = s["bm25_score"] > 0
            if has_vec and has_bm:
                s["fusion_score"] = (vec + bm) / (vw + bw)
            elif has_vec:
                s["fusion_score"] = s["vector_score"]
            elif has_bm:
                s["fusion_score"] = s["bm25_score"]
            else:
                s["fusion_score"] = 0

        # Filter by threshold & doc_ids, sort, limit
        results = list(scores.values())

        if doc_ids:
            # Normalize doc_ids defensively: a non-string element (e.g. 123
            # from an LLM) would make every r["doc_id"] in doc_ids comparison
            # False and silently empty the result set. Coerce numerics to str
            # so an id like "123" still matches, drop anything else, and treat
            # an all-junk filter as "no filter" instead of "no results".
            normalized: set[str] = set()
            for d in doc_ids:
                if isinstance(d, str):
                    normalized.add(d)
                elif isinstance(d, (int, float)) and not isinstance(d, bool):
                    normalized.add(str(d))
            doc_ids = normalized
        if doc_ids:
            results = [r for r in results if r["doc_id"] in doc_ids]

        results = [r for r in results if r["fusion_score"] >= threshold]

        results.sort(key=lambda x: x["fusion_score"], reverse=True)

        return results[:final_top_k]

    async def _run_hybrid_retrieval(
        self,
        kb_id: str,
        query: str,
        doc_ids: list[str] | None = None,
        top_k: int | None = None,
        kb_config: dict | None = None,
    ) -> tuple[str, list[dict]]:
        """Run concurrent vector + BM25 hybrid retrieval and render the result.

        Mirrors the document path of ``parallel_retrieval_node``: vector + BM25
        run concurrently via executor threads, fused with RRF, then rendered
        into ``(rag_context, citations)``. Shared by the entry retrieval node
        (Step 2 of the hybrid_search meta-tool plan) and the ``hybrid_search``
        meta tool (Step 3).

        Args:
            kb_config: Per-KB retrieval config dict. If provided, its values
                override global settings for any non-None field.

        Returns:
            ``(rag_context, citations)`` — same shape as ``agent_nodes._build_context``.
        """
        v_top_k = _resolve_param(kb_config, "vector_top_k", settings.retrieval_vector_top_k)
        b_top_k = _resolve_param(kb_config, "bm25_top_k", settings.retrieval_bm25_top_k)

        loop = asyncio.get_running_loop()
        # Overlap the slow vector path with the fast BM25 path (see
        # parallel_retrieval_node for the latency rationale).
        v_task = loop.run_in_executor(None, vector_store.search, kb_id, query, v_top_k)
        b_task = loop.run_in_executor(None, bm25_index.search, kb_id, query, b_top_k)
        v_res, b_res = await asyncio.gather(v_task, b_task, return_exceptions=True)
        # Vector search may fail (e.g. embedding model not installed) — degrade
        # gracefully and rely on BM25 only instead of erroring the whole request.
        if isinstance(v_res, Exception):
            logger.warning("Vector search error: %s", v_res)
            v_res = []
        if isinstance(b_res, Exception):
            logger.warning("BM25 search error: %s", b_res)
            b_res = []
        retrieved = self.fuse(v_res, b_res, final_top_k=top_k, doc_ids=doc_ids, kb_config=kb_config)
        return _render_context(retrieved)


def _render_context(retrieved: list[dict]) -> tuple[str, list[dict]]:
    """Render fused chunks into ``(rag_context, citations)``.

    Verbatim port of ``agent_nodes._build_context``. Step 2 of the hybrid_search
    meta-tool plan unifies the two by routing the entry retrieval node through
    ``HybridSearchService._run_hybrid_retrieval`` and deleting the ``agent_nodes``
    copy, so this becomes the single source of truth.
    """
    if not retrieved:
        return "No relevant documents found", []
    parts, citations = [], []
    # Defense-in-depth: collapse display-identical sources so the UI never
    # shows what looks like the same chunk twice (e.g. same doc_id + chunk_index
    # + heading). Distinct sections survive because their headings differ.
    seen_keys: set[tuple] = set()
    for i, r in enumerate(retrieved):
        doc_name = r.get("doc_name") or r.get("doc_id", "?")[:8]
        heading = r.get("heading", "") or ""
        page = r.get("page")
        if page == 0:
            page = None
        key = (r.get("doc_id", ""), r.get("chunk_index", 0), heading)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        parts.append(f"[{i + 1}] {doc_name} {heading}\n{r['content']}")
        citations.append({"doc_id": r.get("doc_id", ""), "doc_name": doc_name,
                          "chunk_index": r.get("chunk_index", 0), "heading": heading,
                          "page": page, "score": round(r.get("fusion_score", 0), 4)})
    return "\n\n---\n\n".join(parts), citations


# Singleton
hybrid_search = HybridSearchService()

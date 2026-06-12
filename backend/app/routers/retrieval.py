"""Retrieval debug API routes."""

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.hybrid_search import hybrid_search
from app.services.auth import get_current_admin
from app.services.vector_store import vector_store
from app.services.bm25_index import bm25_index
from app.schemas.retrieval import SearchRequest, SearchResultResponse

router = APIRouter(prefix="/api/retrieval", tags=["Retrieval"])


@router.post("/search", response_model=list[SearchResultResponse])
async def search(request: SearchRequest, current_user: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    """Hybrid search endpoint for debugging retrieval quality.

    Returns results with individual vector/BM25/fusion scores.
    """

    # If kb_id not specified, search across all KBs? For now, require it.
    kb_id = request.kb_id
    if not kb_id:
        # Try to find any KB
        from sqlalchemy import select
        from app.models.knowledge_base import KnowledgeBase
        result = await db.execute(select(KnowledgeBase).limit(1))
        kb = result.scalar_one_or_none()
        if not kb:
            return []
        kb_id = kb.id

    # Ensure BM25 index is built for this KB
    if not bm25_index.has_index(kb_id):
        # Rebuild from DB
        from sqlalchemy import select
        from app.models.document import Chunk
        result = await db.execute(select(Chunk).where(Chunk.content != ""))
        chunks = result.scalars().all()
        if chunks:
            bm25_index.build(kb_id, [
                {
                    "id": c.id,
                    "content": c.content,
                    "doc_id": c.doc_id,
                    "heading": c.heading or "",
                    "page": c.page,
                }
                for c in chunks
            ])

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        None,
        lambda: hybrid_search.search(
        kb_id=kb_id,
        query=request.query,
        vector_weight=request.vector_weight,
        bm25_weight=request.bm25_weight,
        final_top_k=request.top_k,
        threshold=request.threshold,
        doc_ids=request.doc_ids,
    )
    )

    return [
        SearchResultResponse(
            chunk_id=r["chunk_id"],
            doc_name=r.get("doc_id", "未知")[:20],
            heading=r.get("heading"),
            page=r.get("page"),
            content=r["content"],
            vector_score=round(r["vector_score"], 4),
            bm25_score=round(r["bm25_score"], 4),
            fusion_score=round(r["fusion_score"], 4),
        )
        for r in results
    ]

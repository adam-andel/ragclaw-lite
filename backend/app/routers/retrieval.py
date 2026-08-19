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
"""Retrieval debug API routes."""

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.hybrid_search import hybrid_search
from app.services.auth import get_current_staff
from app.services.vector_store import vector_store
from app.services.bm25_index import bm25_index
from app.schemas.retrieval import SearchRequest, SearchResultResponse

router = APIRouter(prefix="/api/retrieval", tags=["Retrieval"])


@router.post("/search", response_model=list[SearchResultResponse])
async def search(request: SearchRequest, current_user: User = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
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
        # Rebuild from DB, scoped to this KB and include filenames
        from sqlalchemy import select, and_
        from app.models.document import Chunk, Document, DocStatus, KBDocument
        chunks_result = await db.execute(
            select(Chunk).join(Document, Chunk.doc_id == Document.id).join(
                KBDocument, and_(KBDocument.doc_id == Document.id, KBDocument.kb_id == kb_id)
            ).where(Document.status.in_([DocStatus.COMPLETED, DocStatus.CHUNKED]), Chunk.content != "")
        )
        chunks = chunks_result.scalars().all()
        if chunks:
            doc_ids = {c.doc_id for c in chunks}
            doc_result = await db.execute(
                select(Document.id, Document.filename).where(Document.id.in_(doc_ids))
            )
            doc_map = {row[0]: row[1] for row in doc_result.fetchall()}
            bm25_index.build(kb_id, [
                {
                    "id": c.id,
                    "content": c.content,
                    "doc_id": c.doc_id,
                    "heading": c.heading or "",
                    "chunk_index": c.chunk_index,
                    "page": c.page,
                    "filename": doc_map.get(c.doc_id, ""),
                }
                for c in chunks
            ])

    # Load KB retrieval config for per-KB parameter overrides
    kb = await db.get(KnowledgeBase, kb_id)
    kb_config = {
        "vector_weight": kb.vector_weight if kb else None,
        "bm25_weight": kb.bm25_weight if kb else None,
        "vector_top_k": kb.vector_top_k if kb else None,
        "bm25_top_k": kb.bm25_top_k if kb else None,
        "final_top_k": kb.final_top_k if kb else None,
        "similarity_threshold": kb.similarity_threshold if kb else None,
    }

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
        kb_config=kb_config,
    )
    )

    return [
        SearchResultResponse(
            chunk_id=r["chunk_id"],
            doc_name=r.get("doc_name") or r.get("doc_id", "unknown")[:20],
            heading=r.get("heading"),
            page=r.get("page"),
            content=r["content"],
            vector_score=round(r["vector_score"], 4),
            bm25_score=round(r["bm25_score"], 4),
            fusion_score=round(r["fusion_score"], 4),
        )
        for r in results
    ]

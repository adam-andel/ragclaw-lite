"""Rebuild BM25 and test search."""
import sys, asyncio
sys.path.insert(0, r'D:\AI\Autoclaw\RAGClaw\ragclaw\backend')

async def main():
    from app.database import async_session
    from sqlalchemy import select
    from app.models.document import Chunk
    from app.services.bm25_index import bm25_index
    from app.services.hybrid_search import hybrid_search

    kb_id = '531e6d76'

    # Rebuild BM25
    async with async_session() as db:
        r = await db.execute(select(Chunk))
        chunks = r.scalars().all()
        bm25_index.build(kb_id, [
            {'id': c.id, 'content': c.content, 'doc_id': c.doc_id,
             'heading': c.heading or '', 'page': c.page}
            for c in chunks
        ])
    print(f'BM25 rebuilt: {bm25_index.has_index(kb_id)}')

    # Test search
    results = hybrid_search.search(kb_id, 'RAGClaw')
    print(f'Search "RAGClaw": {len(results)} results')
    for r in results[:3]:
        print(f'  fusion={r["fusion_score"]:.3f} vec={r["vector_score"]:.3f} bm25={r["bm25_score"]:.3f}')
        print(f'  content: {r["content"][:80]}')

    results2 = hybrid_search.search(kb_id, '技术栈')
    print(f'Search "技术栈": {len(results2)} results')

asyncio.run(main())

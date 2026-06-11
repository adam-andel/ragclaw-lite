"""Check BM25 & ChromaDB for a specific KB."""
import sys, asyncio
sys.path.insert(0, r"D:\AI\Autoclaw\ERAG\erag\backend")

async def main():
    from app.services.bm25_index import bm25_index
    from app.services.vector_store import vector_store

    kb_id = "2ea92cfb"
    print(f"KB mybase ({kb_id[:8]}):")
    print(f"  BM25 built:   {bm25_index.has_index(kb_id)}")
    print(f"  ChromaDB vec: {vector_store.count(kb_id)}")

    # Try search
    from app.services.hybrid_search import hybrid_search
    results = hybrid_search.search(kb_id, "ERAG")
    print(f"  Search ERAG:  {len(results)} results")

asyncio.run(main())

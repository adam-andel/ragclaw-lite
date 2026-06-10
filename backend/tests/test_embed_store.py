"""Mirror exactly what the server does in embedding step."""
import sys, uuid, traceback
sys.path.insert(0, r'D:\AI\Autoclaw\ERAG\erag\backend')

from app.config import settings
from app.services.vector_store import vector_store
from app.services.embedder import embedder_service

kb_id = "test-kb-001"
chunks = [
    {"id": "c1", "content": "ERAG is an enterprise RAG platform.", "doc_id": "d1", "chunk_index": 0, "heading": "Overview", "page": 1, "token_count": 8},
    {"id": "c2", "content": "It uses FastAPI and Vue3 for the tech stack.", "doc_id": "d1", "chunk_index": 1, "heading": "Tech Stack", "page": 1, "token_count": 10},
]

print("Step 1: Embedding...")
try:
    texts = [c["content"] for c in chunks]
    embeddings = embedder_service.embed(texts)
    print(f"  OK: {len(embeddings)} vectors, dim={len(embeddings[0])}")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()
    sys.exit(1)

print("Step 2: ChromaDB add_chunks...")
try:
    vector_store.add_chunks(kb_id, chunks)
    print(f"  OK: count={vector_store.count(kb_id)}")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()
    sys.exit(1)

print("Step 3: Search...")
try:
    results = vector_store.search(kb_id, "tech stack", top_k=2)
    print(f"  OK: {len(results)} results")
    for r in results:
        print(f"    score={r['score']:.3f} content={r['content'][:40]}...")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("\nDone!")

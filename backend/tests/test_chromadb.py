"""Test ChromaDB directly."""
import sys, uuid
sys.path.insert(0, r'D:\AI\Autoclaw\RAGClaw\ragclaw\backend')

# Test 1: import and init
print("1. Importing ChromaDB...")
import chromadb
print("   OK")

# Test 2: Create client
print("2. Creating PersistentClient...")
from app.config import settings
settings.chroma_path.mkdir(parents=True, exist_ok=True)
client = chromadb.PersistentClient(path=str(settings.chroma_path))
print(f"   OK, path={settings.chroma_path}")

# Test 3: Create collection
print("3. Creating collection...")
col = client.get_or_create_collection("test_kb", metadata={"hnsw:space": "cosine"})
print("   OK")

# Test 4: Add data
print("4. Adding test data...")
col.add(
    ids=["test1", "test2"],
    documents=["hello world", "goodbye world"],
    metadatas=[{"idx": 0}, {"idx": 1}],
)
print(f"   OK, count={col.count()}")

# Test 5: Query
print("5. Query...")
results = col.query(query_texts=["hello"], n_results=2)
print(f"   OK, got {len(results['ids'][0])} results")

# Test 6: With embeddings (what our code does)
print("6. Loading embedder...")
from app.services.embedder import embedder_service
emb = embedder_service.embed(["test document content here"])
print(f"   OK, dim={len(emb[0])}")

print("7. Adding with embeddings...")
col2 = client.get_or_create_collection("test_kb2", metadata={"hnsw:space": "cosine"})
col2.add(
    ids=["e1", "e2"],
    embeddings=emb * 2,  # duplicate for 2 docs
    documents=["doc1 content", "doc2 content"],
    metadatas=[{"src": "a"}, {"src": "b"}],
)
print(f"   OK, count={col2.count()}")

print("\n🎉 All ChromaDB tests passed!")

"""Test: ChromaDB persistent vs ephemeral in FastAPI context."""
import sys, uuid, asyncio
sys.path.insert(0, r'D:\AI\Autoclaw\RAGClaw\ragclaw\backend')

async def test_persistent():
    """Test that PersistentClient works in an async context (simulating server)."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from app.config import settings
    
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    
    # Simulate database init (like the server does)
    from app.database import init_db
    await init_db()
    
    # Now try ChromaDB
    client = chromadb.PersistentClient(
        path=str(settings.chroma_path),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    col = client.get_or_create_collection("test_async", metadata={"hnsw:space": "cosine"})
    col.add(ids=["a1"], embeddings=[[0.1]*512], documents=["test"], metadatas=[{"x": 1}])
    print(f"  PersistentClient OK: count={col.count()}")
    client.delete_collection("test_async")
    
async def test_ephemeral():
    """Test EphemeralClient (no file I/O)."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    
    client = chromadb.EphemeralClient(
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    col = client.get_or_create_collection("test_ephemeral", metadata={"hnsw:space": "cosine"})
    col.add(ids=["e1"], embeddings=[[0.1]*384], documents=["test"], metadatas=[{"x": 1}])
    print(f"  EphemeralClient OK: count={col.count()}")

async def main():
    print("Test 1: PersistentClient after DB init...")
    try:
        await test_persistent()
    except Exception as e:
        print(f"  FAIL: {e}")
    
    print("Test 2: EphemeralClient...")
    try:
        await test_ephemeral()
        print("  (use this as fallback if Persistent fails)")
    except Exception as e:
        print(f"  FAIL: {e}")

asyncio.run(main())

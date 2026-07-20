"""Test if the issue is path-length related."""
import sys, uuid
sys.path.insert(0, r'D:\AI\Autoclaw\RAGClaw\ragclaw\backend')

import chromadb
from chromadb.config import Settings as ChromaSettings

# Test with short path
short_path = r"D:\temp_chroma"
import os, shutil
os.makedirs(short_path, exist_ok=True)

print(f"Testing with path: {short_path}")
try:
    client = chromadb.PersistentClient(
        path=short_path,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    col = client.get_or_create_collection("test_short", metadata={"hnsw:space": "cosine"})
    col.add(
        ids=["s1"],
        embeddings=[[0.1]*512],
        documents=["short test"],
        metadatas=[{"x": 1}],
    )
    print(f"  OK: count={col.count()}")
    client.delete_collection("test_short")
finally:
    shutil.rmtree(short_path, ignore_errors=True)

# Test with the actual project path
from app.config import settings
settings.chroma_path.mkdir(parents=True, exist_ok=True)
actual_path = str(settings.chroma_path)
print(f"\nTesting with actual path: {actual_path}")
try:
    client2 = chromadb.PersistentClient(
        path=actual_path,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    col2 = client2.get_or_create_collection("test_actual", metadata={"hnsw:space": "cosine"})
    col2.add(
        ids=["a1"],
        embeddings=[[0.1]*512],
        documents=["actual test"],
        metadatas=[{"x": 1}],
    )
    print(f"  OK: count={col2.count()}")
    client2.delete_collection("test_actual")
except Exception as e:
    print(f"  FAIL: {e}")

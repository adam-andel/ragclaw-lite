"""ChromaDB vector store with thread safety."""

import threading
import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings
from app.services.embedder import embedder_service


class VectorStore:
    """Thread-safe ChromaDB vector store with pre-computed embeddings."""

    def __init__(self):
        self._lock = threading.Lock()
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            settings.chroma_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(settings.chroma_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def _collection_name(self, kb_id: str) -> str:
        return f"kb_{kb_id}"

    def get_or_create_collection(self, kb_id: str):
        client = self._ensure_client()
        return client.get_or_create_collection(
            name=self._collection_name(kb_id),
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, kb_id: str, chunks: list[dict]):
        """Add chunks with fresh embedding computation."""
        if not chunks:
            return
        with self._lock:
            collection = self.get_or_create_collection(kb_id)
            ids = [c["id"] for c in chunks]
            texts = [c["content"] for c in chunks]
            try:
                embeddings = embedder_service.embed(texts)
            except Exception as e:
                raise RuntimeError(f"[embed_model] {e}") from e
            metadatas = [{
                "doc_id": c.get("doc_id", ""), "chunk_index": c.get("chunk_index", 0),
                "heading": c.get("heading", ""), "page": c.get("page") or 0,
                "token_count": c.get("token_count", 0), "filename": c.get("filename", ""),
            } for c in chunks]
            try:
                collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
                print(f"[ChromaDB] add {len(ids)} vectors to kb={kb_id[:8]}, cnt={collection.count()}", flush=True)
            except Exception as e:
                raise RuntimeError(f"[chromadb_add] {e}") from e

    def add_chunks_cached(self, kb_id: str, chunks: list[dict]):
        """Add chunks using pre-computed embeddings (no model call).

        Each chunk dict must include an 'embedding' key with a list[float].
        """
        if not chunks:
            return
        with self._lock:
            collection = self.get_or_create_collection(kb_id)
            ids = [c["id"] for c in chunks]
            texts = [c["content"] for c in chunks]
            embeddings = [c["embedding"] for c in chunks]
            metadatas = [{
                "doc_id": c.get("doc_id", ""), "chunk_index": c.get("chunk_index", 0),
                "heading": c.get("heading", ""), "page": c.get("page") or 0,
                "token_count": c.get("token_count", 0), "filename": c.get("filename", ""),
            } for c in chunks]
            try:
                collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
                print(f"[ChromaDB] add {len(ids)} cached vectors to kb={kb_id[:8]}, cnt={collection.count()}", flush=True)
            except Exception as e:
                raise RuntimeError(f"[chromadb_add] {e}") from e

    def search(self, kb_id: str, query: str,
               top_k: int | None = None, threshold: float | None = None) -> list[dict]:
        top_k = top_k or settings.retrieval_vector_top_k
        with self._lock:
            collection = self.get_or_create_collection(kb_id)
            query_embedding = embedder_service.embed_single(query)
            results = collection.query(
                query_embeddings=[query_embedding], n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
            print(f"[ChromaDB] search kb={kb_id[:8]} q={query[:20]} got={len(results.get('ids', [[]])[0])}", flush=True)
        hits = []
        if results["ids"] and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                similarity = 1.0 - min(distance, 1.0)
                if threshold is not None and similarity < threshold:
                    continue
                hits.append({
                    "id": chunk_id,
                    "content": results["documents"][0][i] if results["documents"] else "",
                    "score": similarity,
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                })
        return hits

    def delete_by_doc(self, kb_id: str, doc_id: str):
        with self._lock:
            try:
                collection = self.get_or_create_collection(kb_id)
                collection.delete(where={"doc_id": doc_id})
            except Exception:
                pass

    def delete_collection(self, kb_id: str):
        with self._lock:
            try:
                client = self._ensure_client()
                client.delete_collection(self._collection_name(kb_id))
            except Exception:
                pass

    def count(self, kb_id: str) -> int:
        with self._lock:
            try:
                collection = self.get_or_create_collection(kb_id)
                return collection.count()
            except Exception:
                return 0


vector_store = VectorStore()

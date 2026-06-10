"""Embedding service wrapper using sentence-transformers (BGE model)."""

from sentence_transformers import SentenceTransformer
from app.config import settings


class EmbedderService:
    """Lazy-loading wrapper for the BGE embedding model."""

    def __init__(self):
        self._model: SentenceTransformer | None = None

    def _ensure_model(self):
        if self._model is None:
            self._model = SentenceTransformer(
                settings.embedding_model,
                device=settings.embedding_device,
            )
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors (each a list of floats)
        """
        model = self._ensure_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        return self._ensure_model().get_sentence_embedding_dimension()


# Singleton
embedder_service = EmbedderService()

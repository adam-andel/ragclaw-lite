"""Embedding service wrapper using sentence-transformers (BGE model)."""

from sentence_transformers import SentenceTransformer
from app.config import settings
from app.services.config_manager import config_manager


class EmbedderService:
    """Lazy-loading wrapper for the BGE embedding model."""

    def __init__(self):
        self._model: SentenceTransformer | None = None

    def _ensure_model(self):
        if self._model is None:
            # Do NOT auto-download. The model is installed on demand from the
            # Settings UI; if it's missing we fail fast with a clear message so
            # the user knows to install it instead of silently pulling ~2GB.
            from app.services.model_manager import model_manager
            if not model_manager.is_installed(config_manager.embedding_model):
                raise RuntimeError(
                    "EMBED_MODEL_NOT_INSTALLED:"
                    "本地 Embedding 模型尚未安装，请前往「系统设置 → Embedding 模型」点击下载安装。"
                )
            self._model = SentenceTransformer(
                config_manager.embedding_model,
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

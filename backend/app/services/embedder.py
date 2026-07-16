"""Embedding service wrapper using sentence-transformers (BGE model)."""

import re
import threading
import unicodedata
from collections import OrderedDict

from sentence_transformers import SentenceTransformer
from app.config import settings
from app.services.config_manager import config_manager

# Query embedding cache: keyed by (model_id, normalized_query).
# Only embed_single (the query path) is cached; document embedding via
# embed() is intentionally NOT cached since those vectors already live in Chroma.
_EMBED_CACHE: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
_EMBED_CACHE_LOCK = threading.Lock()
EMBED_CACHE_MAXSIZE = 2048


def _normalize_query(text: str) -> str:
    """Aggressively normalize a query so equivalent phrasings share a cache key.

    NFC Unicode normalization + lowercase, then keep only CJK + alnum and drop
    ALL whitespace and punctuation. This maximizes hits for mixed CJK/Latin
    queries ("什么是RAG?" == "  什么是 rag ？ ") and is safe: strings that
    survive identical after this pass embed to essentially the same vector.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text).lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


class EmbedderService:
    """Lazy-loading wrapper for the BGE embedding model."""

    # Backend identifier stamped onto every stored vector so a future
    # model/backend switch can detect incompatibility (e.g. torch vs onnx)
    # even when the embedding dimension is unchanged.
    BACKEND = "torch"

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
        """Generate embedding for a single text (the query path).

        Results are cached by (model_id, normalized query) so repeated or
        identical questions reuse the vector instead of re-encoding. The cache
        is thread-safe; changing the embedding model automatically invalidates
        prior entries (different model_id -> different key).
        """
        model_id = config_manager.embedding_model
        cache_key = (model_id, _normalize_query(text))

        with _EMBED_CACHE_LOCK:
            if cache_key in _EMBED_CACHE:
                _EMBED_CACHE.move_to_end(cache_key)
                return list(_EMBED_CACHE[cache_key])

        # Cache miss: encode outside the lock so concurrent *different* queries
        # can run their (slow) encode in parallel across the executor pool.
        vector = self.embed([text])[0]

        with _EMBED_CACHE_LOCK:
            _EMBED_CACHE[cache_key] = vector
            while len(_EMBED_CACHE) > EMBED_CACHE_MAXSIZE:
                _EMBED_CACHE.popitem(last=False)
        return list(vector)  # copy: never hand out the cached object

    @property
    def dimension(self) -> int:
        return self._ensure_model().get_sentence_embedding_dimension()


# Singleton
embedder_service = EmbedderService()

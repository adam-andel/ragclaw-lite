"""Runtime management of the local Embedding model (download / status / delete).

The BGE model is NOT baked into the image at build time. Instead, an admin
downloads it on demand from the Settings UI. This keeps the image small and
lets the user decide when/if to pull the ~2GB model. Downloaded files live in
HF_HOME (configured to a path inside the persistent /app/data volume) so they
survive container restarts.

Source strategy mirrors the apt/pip rules elsewhere: try the official
HuggingFace source first; fall back to the hf-mirror.com mirror only on failure.
"""

import os
import shutil
import threading
from pathlib import Path

from app.config import settings


class _Status:
    IDLE = "idle"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class EmbeddingModelManager:
    """Thread-safe singleton tracking the embedding-model download lifecycle."""

    def __init__(self):
        self._lock = threading.Lock()
        self._status = _Status.IDLE
        self._progress = 0.0
        self._message = ""
        self._error = ""
        self._model = ""
        self._thread: threading.Thread | None = None

    # ── Queries ──

    def is_installed(self, model_name: str | None = None) -> bool:
        """Fast local-only check (no network) for whether the model is cached."""
        name = model_name or settings.embedding_model
        try:
            from huggingface_hub import try_to_load_from_cache
            return try_to_load_from_cache(name, "config.json") is not None
        except Exception:
            return False

    def get_state(self) -> dict:
        with self._lock:
            return {
                "status": self._status,
                "progress": round(self._progress, 1),
                "message": self._message,
                "error": self._error,
                "model": self._model or settings.embedding_model,
            }

    # ── Control ──

    def start_download(self, model_name: str | None = None) -> dict:
        with self._lock:
            if self._status == _Status.DOWNLOADING:
                return {"started": False, "reason": "already_downloading",
                        "model": self._model}
            name = model_name or settings.embedding_model
            self._status = _Status.DOWNLOADING
            self._progress = 0.0
            self._message = f"开始下载 {name} …"
            self._error = ""
            self._model = name
        self._thread = threading.Thread(
            target=self._download_worker, args=(name,), daemon=True,
            name="embedding-model-download",
        )
        self._thread.start()
        return {"started": True, "model": name}

    def delete(self, model_name: str | None = None) -> dict:
        name = model_name or settings.embedding_model
        cache_dir = self._hf_home()
        norm = name.replace("/", "--")
        target = cache_dir / f"models--{norm}"
        removed = False
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed = True
        # Drop any loaded model instance so it reloads on next use.
        try:
            from app.services.embedder import embedder_service
            embedder_service._model = None
        except Exception:
            pass
        with self._lock:
            self._status = _Status.IDLE
            self._progress = 0.0
            self._message = ""
            self._error = ""
        return {"deleted": removed, "model": name}

    # ── Internals ──

    def _hf_home(self) -> Path:
        return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))

    def _set(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, f"_{k}", v)

    def _on_progress(self, progress) -> None:
        try:
            if isinstance(progress, float):
                self._set(progress=max(0.0, min(100.0, progress)))
            elif hasattr(progress, "completed") and hasattr(progress, "total"):
                total = getattr(progress, "total") or 0
                completed = getattr(progress, "completed") or 0
                if total:
                    self._set(progress=max(0.0, min(100.0, completed / total * 100)))
        except Exception:
            pass

    def _download_worker(self, model_name: str) -> None:
        cache_dir = str(self._hf_home())
        # Official source first, then the domestic mirror as a fallback.
        attempts = [
            ("官方源", ""),
            ("国内镜像 (hf-mirror.com)", "https://hf-mirror.com"),
        ]
        last_err: Exception | None = None
        for label, endpoint in attempts:
            try:
                self._set(message=f"正在从{label}下载 {model_name} …")
                prev = os.environ.get("HF_ENDPOINT")
                if endpoint:
                    os.environ["HF_ENDPOINT"] = endpoint
                elif "HF_ENDPOINT" in os.environ:
                    del os.environ["HF_ENDPOINT"]
                from huggingface_hub import snapshot_download
                snapshot_download(
                    repo_id=model_name,
                    cache_dir=cache_dir,
                    progress_callback=self._on_progress,
                )
                # Success — restore HF_ENDPOINT to its pre-attempt value.
                if prev is not None:
                    os.environ["HF_ENDPOINT"] = prev
                elif "HF_ENDPOINT" in os.environ:
                    del os.environ["HF_ENDPOINT"]
                # Pre-warm so the first embedding request is fast.
                warmup_msg = ""
                try:
                    from app.services.embedder import embedder_service
                    embedder_service._ensure_model()
                except Exception as e:  # pragma: no cover - best effort
                    warmup_msg = f"（预热跳过：{e}）"
                self._set(status=_Status.COMPLETED, progress=100.0,
                          message=f"{model_name} 下载完成{warmup_msg}")
                return
            except Exception as e:
                last_err = e
                self._set(message=f"{label}下载失败，尝试下一个源")
                if "HF_ENDPOINT" in os.environ:
                    del os.environ["HF_ENDPOINT"]
                continue
        self._set(status=_Status.FAILED, error=str(last_err),
                  message=f"所有源下载失败：{last_err}")


# Module-level singleton
model_manager = EmbeddingModelManager()

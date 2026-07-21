"""Runtime management of the local Embedding model (download / status / delete).

The BGE model is NOT baked into the image at build time. Instead, an admin
downloads it on demand from the Settings UI. This keeps the image small and
lets the user decide when/if to pull the ~2GB model. Downloaded files live in
HF_HOME (configured to a path inside the persistent /app/data volume) so they
survive container restarts.

Source strategy mirrors the apt/pip rules elsewhere: try the official
HuggingFace source first; fall back to the hf-mirror.com mirror only on failure.
"""

import inspect
import os
import shutil
import threading
from pathlib import Path

# Disable the Xet CAS storage backend (default since huggingface_hub 0.26.0).
# It reconstructs large files via cas-server.xethub.hf.co and frequently fails
# with 401/503 on unstable networks / mirrors. Falling back to the classic
# HTTP/LFS download is slower but far more reliable. setdefault keeps any
# explicit override (e.g. from the Dockerfile/compose env) intact. Best-effort
# here for host/dev runs; the authoritative setting is in Dockerfile + compose.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

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
        # Authoritative record of *successfully* downloaded models. None means
        # "not yet loaded from disk"; seeded once from the cache on first use.
        self._installed: set[str] | None = None
        self._installed_lock = threading.Lock()

    # ── Queries ──

    def is_installed(self, model_name: str | None = None) -> bool:
        """True only when the model was *successfully* downloaded (tracked in the
        installed manifest) and its cache directory still exists. A partially
        downloaded repo — e.g. only ``config.json`` cached — is correctly
        reported as NOT installed, so the UI never shows "ready" for a failed
        download."""
        name = model_name or settings.embedding_model
        self._ensure_installed_loaded()
        norm = name.replace("/", "--")
        cache_dir = self._hf_home()
        return bool(self._installed and name in self._installed
                    and (cache_dir / f"models--{norm}").exists())

    def list_installed_models(self) -> list[str]:
        """Return ids of models tracked as successfully installed, restricted to
        the known embedding-model registry and pruning any whose cache directory
        has since been removed outside of delete()."""
        self._ensure_installed_loaded()
        cache_dir = self._hf_home()
        result = []
        for name in sorted(self._installed or set()):
            norm = name.replace("/", "--")
            if (cache_dir / f"models--{norm}").exists():
                result.append(name)
            else:
                self._forget_installed(name)  # stale entry — prune lazily
        return result

    def model_dimension(self, model_name: str) -> int | None:
        """Best-effort vector dimension for a model.

        Uses the curated registry first, then falls back to reading
        ``hidden_size`` from the cached ``config.json``. Returns None if unknown
        (e.g. not in the registry and not yet downloaded).
        """
        from app.services.embedding_models import known_dimension
        dim = known_dimension(model_name)
        if dim:
            return dim
        try:
            import json
            from huggingface_hub import try_to_load_from_cache
            cfg_path = try_to_load_from_cache(model_name, "config.json")
            if cfg_path:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return int(cfg.get("hidden_size", 0)) or None
        except Exception:
            pass
        return None

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
        self._forget_installed(name)
        with self._lock:
            self._status = _Status.IDLE
            self._progress = 0.0
            self._message = ""
            self._error = ""
        return {"deleted": removed, "model": name}

    # ── Internals ──

    def _hf_home(self) -> Path:
        return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))

    # ── Installed-model manifest (authoritative "is it really downloaded?") ──
    # We cannot trust cache probing (try_to_load_from_cache / scanning models--*
    # dirs): a download that fails partway through still leaves a cached
    # config.json and an empty models--<repo> directory, which would falsely
    # read as "installed". Instead we keep a small JSON manifest that is written
    # ONLY after a fully successful snapshot_download and removed on delete.

    def _manifest_path(self) -> Path:
        return self._hf_home() / "ragclaw_installed.json"

    def _ensure_installed_loaded(self) -> None:
        if self._installed is not None:
            return
        with self._installed_lock:
            if self._installed is not None:
                return
            manifest = self._manifest_path()
            if manifest.exists():
                try:
                    import json
                    data = json.loads(manifest.read_text(encoding="utf-8") or "{}")
                    self._installed = {m for m in data.get("models", []) if isinstance(m, str)}
                except Exception:
                    self._installed = set()
            else:
                # First run (or manifest removed): seed from the on-disk cache,
                # keeping only KNOWN models whose snapshot truly contains a weight
                # file. This avoids mistaking a half-downloaded repo for a real
                # install while preserving genuinely-installed models across an
                # upgrade. Unrelated HF caches are ignored via is_known_model().
                self._installed = self._seed_installed_from_cache()
                self._save_installed(self._installed)

    def _seed_installed_from_cache(self) -> set[str]:
        from app.services.embedding_models import is_known_model
        cache_dir = self._hf_home()
        found: set[str] = set()
        if not cache_dir.exists():
            return found
        for child in cache_dir.iterdir():
            if not (child.is_dir() and child.name.startswith("models--")):
                continue
            repo = child.name[len("models--"):].replace("--", "/", 1)
            if is_known_model(repo) and self._has_weight_file(child):
                found.add(repo)
        return found

    @staticmethod
    def _has_weight_file(model_dir: Path) -> bool:
        # A real install has a weight file (safetensors/bin) in its snapshot.
        # Snapshot entries are symlinks into blobs/; follows them so a dangling
        # (interrupted-download) target is correctly treated as missing.
        for pattern in ("*.safetensors", "*.bin"):
            for p in model_dir.rglob(pattern):
                try:
                    if p.exists() and p.stat().st_size > 1024 * 1024:
                        return True
                except OSError:
                    continue
        return False

    def _save_installed(self, models: set[str]) -> None:
        try:
            import json
            self._manifest_path().write_text(
                json.dumps({"models": sorted(models)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _record_installed(self, name: str) -> None:
        """Authoritatively mark a model as installed (called on successful download)."""
        self._ensure_installed_loaded()
        with self._installed_lock:
            if self._installed is None:
                self._installed = set()
            self._installed.add(name)
            self._save_installed(self._installed)

    def _forget_installed(self, name: str) -> None:
        self._ensure_installed_loaded()
        with self._installed_lock:
            if self._installed and name in self._installed:
                self._installed.discard(name)
                self._save_installed(self._installed)

    def _set(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, f"_{k}", v)

    def _on_progress(self, *args) -> None:
        """Version-agnostic handler for huggingface_hub's progress_callback.

        The callback convention changed across huggingface_hub versions, so we
        accept *args and detect the actual shape at runtime:
          * ~0.23+:  callback(task: str, progress)  where ``progress`` exposes
                     .completed / .total (also tolerates .current / .downloaded
                     aliases, since field names drift between releases)
          * legacy:  callback(completed: int, total: int)
          * single:  callback(fraction: float)  — rare
        Resolving it here keeps the UI progress bar moving on every version.
        """
        try:
            progress = None
            completed = 0.0
            total = 0.0
            # Modern API: callback(task: str, progress)
            if len(args) >= 2 and isinstance(args[0], str) and hasattr(args[1], "total"):
                progress = args[1]
            # Modern API variant: callback(progress) — single positional object
            elif len(args) == 1 and hasattr(args[0], "total") and not isinstance(args[0], (int, float, str)):
                progress = args[0]
            # Legacy API: callback(completed: int, total: int)
            elif len(args) >= 2 and isinstance(args[0], (int, float)) and isinstance(args[1], (int, float)):
                completed, total = float(args[0]), float(args[1])
            # Single numeric fraction in [0, 1]
            elif len(args) == 1 and isinstance(args[0], (int, float)):
                val = float(args[0])
                if 0.0 <= val <= 1.0:
                    self._set(progress=max(0.0, min(100.0, val * 100)))
                return
            else:
                return

            if progress is not None:
                total = float(getattr(progress, "total", 0) or 0)
                completed = (getattr(progress, "completed", None)
                             or getattr(progress, "current", None)
                             or getattr(progress, "downloaded", None)
                             or 0)
                completed = float(completed or 0)
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
                # `progress_callback` was only added to snapshot_download in
                # huggingface_hub 0.26.0. Older installs reject it with a
                # TypeError, which previously broke every model download. Only
                # pass it when the installed signature actually supports it, so
                # downloads keep working on any version (progress reporting just
                # degrades gracefully on older installs).
                dl_kwargs = {
                    "repo_id": model_name,
                    "cache_dir": cache_dir,
                }
                if "progress_callback" in inspect.signature(snapshot_download).parameters:
                    dl_kwargs["progress_callback"] = self._on_progress
                snapshot_download(**dl_kwargs)
                # Mark as installed ONLY on a fully successful download — this is
                # the authoritative signal for is_installed()/list_installed_models().
                # A partial/failed download must never be reported as "installed".
                self._record_installed(model_name)
                # Success — restore HF_ENDPOINT to its pre-attempt value.
                if prev is not None:
                    os.environ["HF_ENDPOINT"] = prev
                elif "HF_ENDPOINT" in os.environ:
                    del os.environ["HF_ENDPOINT"]

                # Pre-warm so the first embedding request against the newly
                # downloaded model is fast.
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

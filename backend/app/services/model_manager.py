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
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadCancelled(Exception):
    """Raised inside the progress callback to abort an in-flight download.

    huggingface_hub calls ``progress_callback`` frequently; raising here is the
    only clean way to stop a download *mid-file* (the underlying HTTP fetch is
    otherwise a single blocking call). The worker catches it and settles to the
    CANCELLED state.
    """


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
        # Pause / cancel control for an in-flight download.
        self._pause_cond = threading.Condition()
        self._paused = False
        self._cancel_event = threading.Event()
        # Generation counter: each new download bumps it so a still-finishing
        # superseded thread (e.g. right after a cancel) aborts at its next
        # checkpoint instead of racing the new download for the same cache.
        self._epoch = 0
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
            if self._status in (_Status.DOWNLOADING, _Status.PAUSED):
                return {"started": False, "reason": "already_downloading",
                        "model": self._model}
            name = model_name or settings.embedding_model
            self._status = _Status.DOWNLOADING
            self._progress = 0.0
            self._message = f"开始下载 {name} …"
            self._error = ""
            self._model = name
            # Bump the generation so any still-finishing previous thread (e.g.
            # right after a cancel or delete) aborts at its next checkpoint
            # instead of racing this new download for the same cache.
            self._cancel_event.clear()
            self._paused = False
            self._epoch += 1
            my_epoch = self._epoch
        self._thread = threading.Thread(
            target=self._download_worker, args=(name, my_epoch), daemon=True,
            name="embedding-model-download",
        )
        self._thread.start()
        return {"started": True, "model": name}

    # ── Pause / Resume / Cancel ──

    def pause_download(self) -> bool:
        """Freeze the download between files. A mid-file transfer is allowed to
        finish first, so the cache stays consistent and resume is byte-safe."""
        with self._lock:
            if self._status != _Status.DOWNLOADING:
                return False
            self._status = _Status.PAUSED
            self._paused = True
            self._message = f"已暂停：{self._model}（已下载部分保留）"
        return True

    def resume_download(self) -> bool:
        """Continue a paused download. The worker is already mid-flight; we just
        release the pause gate and it picks up the next file."""
        with self._lock:
            if self._status != _Status.PAUSED:
                return False
            self._status = _Status.DOWNLOADING
            self._paused = False
            self._message = f"继续下载 {self._model} …"
        with self._pause_cond:
            self._pause_cond.notify_all()
        return True

    def cancel_download(self) -> bool:
        """Abort the download and remove the partial cache entirely — a cancelled
        download keeps nothing behind. Safe to call when idle — it then returns
        False."""
        with self._lock:
            if self._status not in (_Status.DOWNLOADING, _Status.PAUSED):
                return False
            self._paused = False
            self._cancel_event.set()
            self._epoch += 1            # supersede the in-flight thread
            self._status = _Status.CANCELLED
            self._progress = 0.0
            self._message = f"正在取消 {self._model} …"
        # Wake the worker in case it is blocked on the pause gate.
        with self._pause_cond:
            self._pause_cond.notify_all()
        # Wipe the partial download so nothing is left behind.
        self._remove_cache(self._model)
        return True

    def _remove_cache(self, model_name: str) -> None:
        """Delete the on-disk cache for a model (best-effort, idempotent)."""
        cache_dir = self._hf_home()
        norm = model_name.replace("/", "--")
        target = cache_dir / f"models--{norm}"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    def delete(self, model_name: str | None = None) -> dict:
        name = model_name or settings.embedding_model
        cache_dir = self._hf_home()
        norm = name.replace("/", "--")
        target = cache_dir / f"models--{norm}"
        # Capture whether anything existed *before* we stop the worker, since
        # cancel_download() below wipes a partial download immediately.
        existed = target.exists()
        # Stop any in-flight download of this model so we don't race the rmtree
        # (the worker writes into the very directory we are about to remove).
        self.cancel_download()
        # Supersede the dying thread via the epoch so its error path can't
        # overwrite the IDLE state we set below.
        with self._lock:
            self._epoch += 1
        removed = False
        if existed:
            # cancel_download may have already removed a partial download; make
            # sure the directory is gone regardless.
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
            self._paused = False
            self._cancel_event.clear()
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
                # No manifest yet. The manifest is the SINGLE source of truth for
                # "successfully installed" — it is written ONLY after a fully
                # successful download (see _record_installed). We must NOT infer
                # installation by scanning the on-disk cache: a paused or
                # interrupted download leaves a partial cache (usually with a
                # >1MB weight file already fetched) that would otherwise be
                # wrongly reported as "installed". Start empty and persist it so
                # we never re-scan and never poison the manifest with a
                # half-downloaded model. A genuinely-installed model is always
                # present in the manifest and survives restarts; a model
                # downloaded by legacy code simply shows as "not installed" until
                # its next (cache-resuming, near-instant) download records it.
                self._installed = set()
                self._save_installed(self._installed)

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

    def _download_with_progress(self, fn, kwargs: dict, callback, epoch: int,
                                 model_name: str) -> None:
        """Call ``fn`` with ``progress_callback`` when the installed
        huggingface_hub version supports it.

        Signature introspection alone is unreliable across versions (e.g.
        ``hf_hub_download`` gained ``progress_callback`` in 0.16 but
        ``snapshot_download`` only in 0.26, and decorators can make
        ``inspect.signature`` lie) — so if the call still rejects the kwarg we
        retry once WITHOUT it. The download always succeeds; only live progress
        degrades gracefully on ancient installs. ``DownloadCancelled`` raised by
        the callback propagates (it is not a TypeError)."""
        if callback is not None:
            try:
                kw = dict(kwargs)
                kw["progress_callback"] = callback
                fn(**kw)
                return
            except TypeError as e:
                if "progress_callback" in str(e):
                    # Unsupported on this version — retry without progress.
                    fn(**kwargs)
                    return
                raise
        fn(**kwargs)

    def _check_abort(self, model_name: str, epoch: int) -> bool:
        """Return True if this run should stop. An epoch mismatch means a newer
        download superseded it — just return without touching state. An explicit
        cancel settles to CANCELLED first."""
        if self._epoch != epoch:
            return True
        if self._cancel_event.is_set():
            self._after_cancel(model_name)
            return True
        return False

    def _make_abort_callback(self, epoch):
        """Progress is driven by on-disk byte polling (see _run_with_disk_progress),
        so the huggingface_hub callback's ONLY job is instant cancellation: it
        raises ``DownloadCancelled`` the moment the user hits cancel (or a newer
        download epoch supersedes this run), aborting the in-flight transfer
        mid-file instead of only between files. We no longer try to derive a
        percentage from its args — that was fragile across versions/backends and
        left the bar frozen (e.g. stuck at 50%)."""
        def cb(*args):
            if self._cancel_event.is_set() or self._epoch != epoch:
                raise DownloadCancelled()
        return cb

    def _set_endpoint(self, endpoint: str) -> None:
        if endpoint:
            os.environ["HF_ENDPOINT"] = endpoint
        elif "HF_ENDPOINT" in os.environ:
            del os.environ["HF_ENDPOINT"]

    def _list_repo_files(self, model_name: str) -> list[tuple[str, int]]:
        """Return ``[(filename, size_bytes), ...]`` for the repo, best-effort.
        Sizes drive accurate overall progress; when unavailable we fall back to
        an index-based (file i of N) estimate, which is still correct, just
        coarser."""
        try:
            from huggingface_hub import HfApi, list_repo_files
            self._set_endpoint("")  # list from the official source first
            try:
                info = HfApi().model_info(model_name, files_metadata=True)
                files = [
                    (s.rfilename, self._sibling_size(s))
                    for s in (info.siblings or [])
                ]
                if files:
                    return files
            except Exception:
                pass
            return [(f, 0) for f in list_repo_files(model_name)]
        except Exception:
            return []

    @staticmethod
    def _sibling_size(s) -> int:
        """Best-effort byte size for a repo sibling. Big weight files are stored
        via LFS, whose size sometimes lives in ``s.lfs`` (object or dict) rather
        than ``s.size`` — missing it left total_bytes far too small and pinned
        the bar at an index-based floor. Try ``size`` first, then LFS."""
        sz = getattr(s, "size", 0) or 0
        if sz:
            return int(sz)
        lfs = getattr(s, "lfs", None)
        if lfs is not None:
            if isinstance(lfs, dict):
                return int(lfs.get("size", 0) or 0)
            return int(getattr(lfs, "size", 0) or 0)
        return 0

    def _model_blobs_dir(self, model_name: str) -> Path:
        """Path to the HF cache blobs dir for this model. Live downloads grow a
        ``*.incomplete`` blob here; summing this dir gives true on-disk bytes."""
        norm = model_name.replace("/", "--")
        return self._hf_home() / f"models--{norm}" / "blobs"

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        try:
            for p in path.rglob("*"):
                try:
                    if p.is_file():
                        total += p.stat().st_size
                except OSError:
                    continue
        except Exception:
            pass
        return total

    def _run_with_disk_progress(self, dl_callable, model_name: str,
                                 total_bytes: int, epoch: int) -> None:
        """Run a blocking HF download in a side thread while THIS thread polls the
        model's on-disk blob bytes to drive a smooth, reliable progress bar —
        independent of huggingface_hub's ``progress_callback`` shape or download
        backend (xet / hf_transfer), which were the source of the bar freezing.

        ``dl_callable`` should already carry an abort callback (raising
        ``DownloadCancelled`` on cancel/epoch) for instant mid-file cancel; any
        exception it raises is re-raised here so the worker handles it normally."""
        blobs = self._model_blobs_dir(model_name)
        done = threading.Event()
        box: dict = {}

        def run():
            try:
                dl_callable()
            except BaseException as e:  # noqa: BLE001 - capture cancel + errors
                box["err"] = e
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True, name="hf-download-io")
        t.start()
        while not done.wait(0.4):
            if total_bytes:
                cur = self._dir_size(blobs)
                # Cap at 99% so completion is only asserted after the call returns.
                pct = min(99.0, cur / total_bytes * 100)
                self._set(progress=max(self._progress, pct))
        t.join()
        if "err" in box:
            raise box["err"]

    def _record_success(self, model_name: str) -> None:
        """Mark the model installed and pre-warm it (best-effort)."""
        self._record_installed(model_name)
        warmup_msg = ""
        try:
            from app.services.embedder import embedder_service
            embedder_service._ensure_model()
        except Exception as e:  # pragma: no cover - best effort
            warmup_msg = f"（预热跳过：{e}）"
        self._set(status=_Status.COMPLETED, progress=100.0,
                  message=f"{model_name} 下载完成{warmup_msg}")

    def _after_cancel(self, model_name: str) -> None:
        """Settle to the CANCELLED terminal state. The partial cache is wiped so a
        cancelled download leaves nothing behind, and the model is NOT recorded as
        installed."""
        self._remove_cache(model_name)
        self._forget_installed(model_name)
        self._set(status=_Status.CANCELLED,
                  message=f"{model_name} 下载已取消（已删除未完成的下载）",
                  progress=0.0)

    def _download_via_snapshot(self, model_name: str, cache_dir: str, epoch: int) -> None:
        """Fallback when the file list can't be enumerated: a single
        ``snapshot_download``. Still cancel-capable (the abort callback raises
        ``DownloadCancelled``); progress stays coarse here since total size is
        unknown on this path."""
        from huggingface_hub import snapshot_download
        dl_kwargs: dict = {"repo_id": model_name, "cache_dir": cache_dir}
        self._download_with_progress(
            snapshot_download, dl_kwargs,
            self._make_abort_callback(epoch), epoch, model_name)

    def _download_via_attempts_snapshot(self, model_name: str, cache_dir: str,
                                        attempts: list[tuple[str, str]], epoch: int) -> None:
        """Run ``_download_via_snapshot`` against each source in turn."""
        last_err: Exception | None = None
        for label, endpoint in attempts:
            if self._check_abort(model_name, epoch):
                return
            self._set_endpoint(endpoint)
            self._set(message=f"正在从{label}下载 {model_name} …")
            try:
                self._download_via_snapshot(model_name, cache_dir, epoch)
                self._record_success(model_name)
                return
            except DownloadCancelled:
                self._after_cancel(model_name)
                return
            except Exception as e:
                last_err = e
                continue
        self._set(status=_Status.FAILED, error=str(last_err),
                  message=f"所有源下载失败：{last_err}")

    def _download_worker(self, model_name: str, epoch: int) -> None:
        cache_dir = str(self._hf_home())
        prev = os.environ.get("HF_ENDPOINT")
        # Official source first, then the domestic mirror as a fallback.
        attempts = [
            ("官方源", ""),
            ("国内镜像 (hf-mirror.com)", "https://hf-mirror.com"),
        ]
        try:
            files = self._list_repo_files(model_name)
            if not files:
                # Could not enumerate files → fall back to a single snapshot.
                self._download_via_attempts_snapshot(model_name, cache_dir, attempts, epoch)
                return

            total_files = len(files)
            # Only trust byte-based (disk-polled) progress when EVERY file size is
            # known; a partial total would make the bar rush ahead then freeze
            # (e.g. stuck at 99%). Otherwise use the coarse index-based floor.
            all_sizes_known = all(sz > 0 for _, sz in files)
            total_bytes = sum(sz for _, sz in files) if all_sizes_known else 0
            done_bytes = 0

            for idx, (fname, fsize) in enumerate(files, start=1):
                # ── Pause gate: only between files, so the cache stays valid ──
                with self._pause_cond:
                    while self._paused and not self._cancel_event.is_set():
                        self._pause_cond.wait()
                if self._check_abort(model_name, epoch):
                    return

                downloaded = False
                last_err: Exception | None = None
                for label, endpoint in attempts:
                    if self._check_abort(model_name, epoch):
                        return
                    self._set_endpoint(endpoint)
                    self._set(message=f"正在从{label}下载 {fname}（{idx}/{total_files}）")
                    # When total size is unknown we can't poll a real %, so fall
                    # back to an index-based floor (bar jumps at each file). With
                    # known sizes, disk polling drives the real %, so we must NOT
                    # apply this floor — it would wrongly jump the bar ahead.
                    if not total_bytes:
                        self._set(progress=max(self._progress, (idx - 1) / total_files * 100))
                    try:
                        from huggingface_hub import hf_hub_download
                        kwargs: dict = {
                            "repo_id": model_name,
                            "filename": fname,
                            "cache_dir": cache_dir,
                        }
                        cb = self._make_abort_callback(epoch)

                        def _dl():
                            self._download_with_progress(
                                hf_hub_download, kwargs, cb, epoch, model_name)

                        # Poll on-disk blob bytes for a smooth, backend-agnostic %.
                        self._run_with_disk_progress(_dl, model_name, total_bytes, epoch)
                        downloaded = True
                        break
                    except DownloadCancelled:
                        self._after_cancel(model_name)
                        return
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        continue
                if not downloaded:
                    self._set(status=_Status.FAILED, error=str(last_err),
                              message=f"{fname} 下载失败：{last_err}")
                    return
                done_bytes += fsize
                # With unknown sizes, mark this file done via the index floor.
                if not total_bytes:
                    self._set(progress=max(self._progress, idx / total_files * 100))

            # All files retrieved → success.
            self._record_success(model_name)
        except DownloadCancelled:
            self._after_cancel(model_name)
        except Exception as e:  # noqa: BLE001
            self._set(status=_Status.FAILED, error=str(e),
                      message=f"下载失败：{e}")
        finally:
            self._cancel_event.clear()
            with self._lock:
                self._paused = False
            if prev is not None:
                os.environ["HF_ENDPOINT"] = prev
            elif "HF_ENDPOINT" in os.environ:
                del os.environ["HF_ENDPOINT"]


# Module-level singleton
model_manager = EmbeddingModelManager()

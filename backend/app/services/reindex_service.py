"""Background re-indexing of all documents against the current embedding model.

After an embedding-model switch whose dimension differs from the previous one,
every Chroma collection is wiped (see ``embedding_model.switch``). This service
rebuilds them: it re-embeds every completed document with the *now active* model
and re-pushes the vectors into each knowledge base the document is linked to.

It runs in a daemon thread with a synchronous ``sqlite3`` connection so it can be
launched from anywhere (a FastAPI request handler, or the model-download worker
thread) without depending on the asyncio event loop.
"""

import struct
import threading

from app.config import settings
from app.models.document import DocStatus
from app.services.embedder import embedder_service
from app.services.vector_store import vector_store


class _ReindexStatus:
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class _ReindexPhase:
    """Machine-readable phase codes for the re-index job.

    The frontend maps these to localized strings via its i18n table, so the
    backend only ever emits codes + structured params, never baked text.
    """

    DELETING = "deleting"
    REEMBEDDING = "reembedding"
    NOTHING = "nothing"
    PROCESSED = "processed"
    COMPLETED = "completed"
    ERR_MODEL_NOT_INSTALLED = "err_model_not_installed"
    ERR_DELETE_FAILED = "err_delete_failed"
    ERR_DB_OPEN = "err_db_open"
    ERR_REEMBED_FAILED = "err_reembed_failed"


class ReindexService:
    """Thread-safe tracker + runner for the full-corpus re-index job."""

    def __init__(self):
        self._lock = threading.Lock()
        self._status = _ReindexStatus.IDLE
        self._progress = 0.0
        self._phase = ""
        self._params: dict = {}
        self._error = ""
        self._current = 0
        self._total = 0
        self._thread: threading.Thread | None = None

    # ── Queries ──

    def get_state(self) -> dict:
        with self._lock:
            return {
                "status": self._status,
                "phase": self._phase,
                "params": self._params,
                "progress": round(self._progress, 1),
                "error": self._error,
                "current": self._current,
                "total": self._total,
            }

    def is_running(self) -> bool:
        with self._lock:
            return self._status == _ReindexStatus.RUNNING

    # ── Control ──

    def start(self, clear_vectors: bool = False) -> dict:
        with self._lock:
            if self._status == _ReindexStatus.RUNNING:
                return {"started": False, "reason": "already_running"}
            # Surface the "deleting old vectors" phase immediately so the UI can
            # show it right after the switch endpoint returns (before the worker
            # thread actually performs the wipe). Only the embedding-model switch
            # flow clears; the plain rebuild flow re-embeds in place instead.
            if clear_vectors:
                self._status = _ReindexStatus.RUNNING
                self._progress = 0.0
                self._phase = _ReindexPhase.DELETING
                self._params = {}
                self._error = ""
                self._current = 0
                self._total = 0
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="embedding-reindex",
            args=(clear_vectors,),
        )
        self._thread.start()
        return {"started": True}

    # ── Internals ──

    def _set(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, f"_{k}", v)

    def _worker(self, clear_vectors: bool = False):
        from app.services.model_manager import model_manager

        if not model_manager.is_installed():
            self._set(status=_ReindexStatus.FAILED,
                      phase=_ReindexPhase.ERR_MODEL_NOT_INSTALLED,
                      error="", params={})
            return

        # Optional first phase: wipe every existing vector collection. Used by the
        # embedding-model switch flow, which re-embeds all documents against the
        # new model. Reporting it up front lets the UI show "Deleting old vectors"
        # immediately instead of only once re-embedding begins.
        if clear_vectors:
            self._set(status=_ReindexStatus.RUNNING, progress=0.0, current=0,
                      total=0, error="", phase=_ReindexPhase.DELETING, params={})
            try:
                from app.services.vector_store import vector_store
                vector_store.clear_all()
            except Exception as e:
                self._set(status=_ReindexStatus.FAILED,
                          phase=_ReindexPhase.ERR_DELETE_FAILED,
                          error=str(e), params={})
                return

        import sqlite3

        try:
            conn = sqlite3.connect(str(settings.sqlite_path), timeout=30)
        except Exception as e:
            self._set(status=_ReindexStatus.FAILED,
                      phase=_ReindexPhase.ERR_DB_OPEN, error=str(e), params={})
            return

        try:
            cur = conn.cursor()
            # NOTE: SQLAlchemy persists the DocStatus *member name* (e.g. "CHUNKED"),
            # not its value ("chunked"). Filter on the names to match the stored rows.
            target_statuses = (
                DocStatus.CHUNKED.name,
                DocStatus.COMPLETED.name,
                DocStatus.FAILED.name,
                DocStatus.EMBEDDING.name,
            )
            placeholders = ",".join("?" for _ in target_statuses)
            cur.execute(
                f"SELECT id, filename FROM documents "
                f"WHERE status IN ({placeholders})",
                target_statuses,
            )
            docs = cur.fetchall()
            total = len(docs)
            self._set(status=_ReindexStatus.RUNNING, current=0, total=total,
                      progress=0.0, error="",
                      phase=_ReindexPhase.REEMBEDDING if total else _ReindexPhase.NOTHING,
                      params={"total": total} if total else {})

            for i, (doc_id, filename) in enumerate(docs, start=1):
                try:
                    self._reindex_doc(cur, conn, doc_id)
                except Exception as e:
                    conn.execute(
                        "UPDATE documents SET status=?, error_message=?, progress=0 WHERE id=?",
                        (DocStatus.FAILED.name, str(e)[:500], doc_id),
                    )
                    conn.commit()
                self._set(current=i,
                          progress=round(i / total * 100, 1) if total else 100.0,
                          phase=_ReindexPhase.PROCESSED,
                          params={"i": i, "total": total, "filename": filename})

            self._set(status=_ReindexStatus.COMPLETED, progress=100.0,
                      phase=_ReindexPhase.COMPLETED, params={"total": total})
        except Exception as e:
            self._set(status=_ReindexStatus.FAILED,
                      phase=_ReindexPhase.ERR_REEMBED_FAILED,
                      error=str(e), params={})
        finally:
            conn.close()

    def _reindex_doc(self, cur, conn, doc_id: str) -> None:
        # Mark as in-progress so the UI reflects per-document activity.
        conn.execute(
            "UPDATE documents SET status=?, progress=50, error_message='' WHERE id=?",
            (DocStatus.EMBEDDING.name, doc_id),
        )
        conn.commit()

        cur.execute(
            "SELECT id, content, chunk_index, heading, page, token_count "
            "FROM chunks WHERE doc_id=? ORDER BY chunk_index",
            (doc_id,),
        )
        rows = cur.fetchall()
        if not rows:
            conn.execute(
                "UPDATE documents SET status=?, progress=100, chunk_count=0 WHERE id=?",
                (DocStatus.COMPLETED.name, doc_id),
            )
            conn.commit()
            return

        texts = [r[1] for r in rows]
        embeddings = embedder_service.embed(texts)  # list[list[float]]

        # Persist the freshly computed embeddings back into the chunk cache.
        for (cid, _content, _cidx, _heading, _page, _tcount), emb in zip(rows, embeddings):
            blob = struct.pack(f"{len(emb)}f", *emb)
            conn.execute("UPDATE chunks SET embedding=? WHERE id=?", (blob, cid))

        chunk_dicts = []
        for (cid, content, cidx, heading, page, tcount), emb in zip(rows, embeddings):
            chunk_dicts.append({
                "id": cid, "content": content, "embedding": emb,
                "doc_id": doc_id, "chunk_index": cidx,
                "heading": heading or "", "page": page,
                "token_count": tcount,
            })

        # Re-push into every linked knowledge base's Chroma collection.
        cur.execute("SELECT kb_id FROM kb_documents WHERE doc_id=?", (doc_id,))
        kb_ids = [r[0] for r in cur.fetchall()]

        for kb_id in kb_ids:
            try:
                vector_store.delete_by_doc(kb_id, doc_id)
            except Exception:
                pass
            vector_store.add_chunks_cached(kb_id, chunk_dicts)

        conn.execute(
            "UPDATE documents SET status=?, progress=100, chunk_count=? WHERE id=?",
            (DocStatus.COMPLETED.name, len(rows), doc_id),
        )
        conn.commit()


# Module-level singleton
reindex_service = ReindexService()

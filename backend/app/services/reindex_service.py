"""Background re-indexing of all documents against the current embedding model.

After an embedding-model switch whose dimension differs from the previous one,
every Chroma collection is wiped (see ``embedding_model.switch``). This service
rebuilds them: it re-embeds every completed document with the *now active* model
and re-pushes the vectors into each knowledge base the document is linked to.

It runs in a daemon thread, but all database access goes through the async
SQLAlchemy session (the worker drives its own event loop via ``asyncio.run``),
so it is dialect-agnostic and works against both SQLite and Postgres.
"""

import asyncio
import struct
import threading

from sqlalchemy import select, update

from app.database import async_session
from app.models.document import Document, Chunk, KBDocument, DocStatus
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
                vector_store.clear_all()
            except Exception as e:
                self._set(status=_ReindexStatus.FAILED,
                          phase=_ReindexPhase.ERR_DELETE_FAILED,
                          error=str(e), params={})
                return

        # All DB access happens through the async session. The worker runs in a
        # daemon thread with no ambient event loop, so drive it with asyncio.run.
        try:
            asyncio.run(self._run_reindex())
        except Exception as e:
            self._set(status=_ReindexStatus.FAILED,
                      phase=_ReindexPhase.ERR_REEMBED_FAILED,
                      error=str(e), params={})

    async def _run_reindex(self) -> None:
        # NOTE: SQLAlchemy persists the DocStatus *member name* (e.g. "CHUNKED"),
        # not its value ("chunked"). Filter on the names to match the stored rows.
        target_statuses = (
            DocStatus.CHUNKED.name,
            DocStatus.COMPLETED.name,
            DocStatus.FAILED.name,
            DocStatus.EMBEDDING.name,
        )

        async with async_session() as session:
            docs = (
                await session.execute(
                    select(Document.id, Document.filename)
                    .where(Document.status.in_(target_statuses))
                )
            ).all()
            total = len(docs)
            self._set(status=_ReindexStatus.RUNNING, current=0, total=total,
                      progress=0.0, error="",
                      phase=_ReindexPhase.REEMBEDDING if total else _ReindexPhase.NOTHING,
                      params={"total": total} if total else {})

            for i, (doc_id, filename) in enumerate(docs, start=1):
                try:
                    await self._reindex_doc(session, doc_id)
                except Exception as e:
                    await session.execute(
                        update(Document)
                        .where(Document.id == doc_id)
                        .values(status=DocStatus.FAILED,
                                error_message=str(e)[:500],
                                progress=0)
                    )
                    await session.commit()
                self._set(current=i,
                          progress=round(i / total * 100, 1) if total else 100.0,
                          phase=_ReindexPhase.PROCESSED,
                          params={"i": i, "total": total, "filename": filename})

            self._set(status=_ReindexStatus.COMPLETED, progress=100.0,
                      phase=_ReindexPhase.COMPLETED, params={"total": total})

    async def _reindex_doc(self, session, doc_id: str) -> None:
        # Mark as in-progress so the UI reflects per-document activity.
        await session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(status=DocStatus.EMBEDDING, progress=50, error_message="")
        )
        await session.commit()

        rows = (
            await session.execute(
                select(Chunk.id, Chunk.content, Chunk.chunk_index,
                       Chunk.heading, Chunk.page, Chunk.token_count)
                .where(Chunk.doc_id == doc_id)
                .order_by(Chunk.chunk_index)
            )
        ).all()
        if not rows:
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(status=DocStatus.COMPLETED, progress=100, chunk_count=0)
            )
            await session.commit()
            return

        texts = [r.content for r in rows]
        embeddings = embedder_service.embed(texts)  # list[list[float]]

        # Persist the freshly computed embeddings back into the chunk cache.
        for row, emb in zip(rows, embeddings):
            blob = struct.pack(f"{len(emb)}f", *emb)
            await session.execute(
                update(Chunk).where(Chunk.id == row.id).values(embedding=blob)
            )

        chunk_dicts = []
        for row, emb in zip(rows, embeddings):
            chunk_dicts.append({
                "id": row.id, "content": row.content, "embedding": emb,
                "doc_id": doc_id, "chunk_index": row.chunk_index,
                "heading": row.heading or "", "page": row.page,
                "token_count": row.token_count,
            })

        # Re-push into every linked knowledge base's Chroma collection.
        kb_ids = (
            await session.execute(
                select(KBDocument.kb_id).where(KBDocument.doc_id == doc_id)
            )
        ).scalars().all()

        for kb_id in kb_ids:
            try:
                vector_store.delete_by_doc(kb_id, doc_id)
            except Exception:
                pass
            vector_store.add_chunks_cached(kb_id, chunk_dicts)

        await session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(status=DocStatus.COMPLETED, progress=100, chunk_count=len(rows))
        )
        await session.commit()


# Module-level singleton
reindex_service = ReindexService()

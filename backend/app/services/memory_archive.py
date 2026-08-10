"""Archived conversation-memory store (three-tier memory, B namespace).

When the rolling-window (L0) summary grows past the HIGH% threshold,
``conversation_summary.maybe_archive_and_compact`` hands the older fold
paragraphs here to be persisted as ``MemoryChunk`` rows and indexed for
hybrid (vector + BM25) retrieval under a per-conversation pseudo-KB
``mem_{conversation_id}``. This keeps long-term conversation memory isolated
from the user's document knowledge base while still reusing the existing
``hybrid_search`` retrieval path.

Archival is split into two halves so a fold archived during a turn is already
retrievable by the retrieval node LATER IN THAT SAME TURN:

- :func:`archive_memory_essential` -- the retrieval-critical half, AWAITED by
  the caller before the agent graph starts: persist rows, flag the conversation
  via ``mark_has_memory``, (re)build the BM25 index. All cheap (one DB write
  plus an in-memory index build), so it costs the turn almost nothing.
- :func:`schedule_memory_embedding` -- the expensive half (embed + Chroma
  write), still fire-and-forget. ``hybrid_search`` degrades to BM25-only until
  it lands, so the current turn already recalls the new chunks and the next
  turn gets full hybrid retrieval.

Degradation mirrors ``doc_processor``: chunks are persisted BEFORE embedding,
so keyword (BM25) retrieval works even when no embedding model is installed.
When embedding fails (``EMBED_MODEL_NOT_INSTALLED``) we keep BM25-only and
leave ``embedded=False`` for ``process_pending_memory`` to retry on a later
startup.
"""

import asyncio
import logging
from sqlalchemy import select, update

import app.database as db_mod
from app.models.memory_chunk import MemoryChunk
from app.services.vector_store import vector_store
from app.services.bm25_index import bm25_index

logger = logging.getLogger("ragclaw.memory_archive")

# Track which conversations currently have archived memory so the per-turn
# retrieval node can skip the (cheap) memory lookup when there is nothing to
# retrieve. Populated at startup by process_pending_memory and kept in sync as
# chunks are archived / purged.
_MEM_CONV_IDS: set[str] = set()

# Keep references to fire-and-forget embedding tasks so they are not garbage
# collected before completing (mirrors other background tasks in the codebase).
_MEM_TASKS: set[asyncio.Task] = set()


def has_memory(conversation_id: str | None) -> bool:
    return bool(conversation_id) and conversation_id in _MEM_CONV_IDS


def mark_has_memory(conversation_id: str):
    _MEM_CONV_IDS.add(conversation_id)


def unmark_has_memory(conversation_id: str):
    _MEM_CONV_IDS.discard(conversation_id)


def _chunk_to_dict(c: MemoryChunk) -> dict:
    """Format a persisted MemoryChunk row for BM25 rebuild."""
    return {
        "id": c.id,
        "content": c.content,
        "doc_id": c.mem_kb_id,
        "heading": c.heading or "",
        "chunk_index": c.chunk_index,
        "page": c.page,
    }


def _embed_mem_chunks(mem_kb: str, dicts: list[dict]):
    """Embed + write chunks to Chroma, replacing any prior vectors for this mem-KB.

    Runs synchronously inside an executor. The collection is dropped first so
    re-embedding previously-failed (embedded=False) chunks does not collide with
    stale ids.
    """
    vector_store.delete_collection(mem_kb)
    vector_store.add_chunks(mem_kb, dicts)


async def archive_memory_essential(conv_id: str, chunk_dicts: list[dict]) -> bool:
    """Persist + keyword-index a batch of archived fold paragraphs. AWAIT THIS.

    This is the retrieval-critical half of archival, and it is deliberately
    awaited (not fire-and-forget) by ``maybe_archive_and_compact`` so that by the
    time the agent graph's retrieval node runs -- later in the SAME turn -- the
    freshly archived folds are already persisted, ``has_memory`` is True and the
    BM25 index is built. Retrieval therefore needs a single pass: no re-running
    or replacing of search results.

    Three steps, all cheap:
      1. Persist ``MemoryChunk`` rows (embedding-agnostic, survives a restart).
      2. ``mark_has_memory`` so the retrieval node stops skipping this KB.
      3. (Re)build BM25 from every persisted row of this conversation.

    Embedding is intentionally NOT done here -- it is the slow part and would be
    charged to the user's time-to-first-token. See ``schedule_memory_embedding``.

    Never raises: any failure is logged and reported as ``False``, so a broken
    archive degrades to "no memory recall this turn" instead of killing the turn.
    """
    if not conv_id or not chunk_dicts:
        return False
    mem_kb = f"mem_{conv_id}"
    try:
        # 1) Persist rows (no embedding required) so BM25 retrieval is available
        #    immediately and survives a restart / embed failure.
        async with db_mod.async_session() as db:
            for c in chunk_dicts:
                db.add(MemoryChunk(
                    id=c["id"],
                    conversation_id=conv_id,
                    mem_kb_id=mem_kb,
                    chunk_index=c["chunk_index"],
                    content=c["content"],
                    token_count=c["token_count"],
                    embedded=False,
                ))
            await db.commit()

        # 2) Unblock the retrieval node's has_memory() guard.
        mark_has_memory(conv_id)

        # 3) Update BM25 so keyword recall is ready before this turn's retrieval
        #    runs. Use the INCREMENTAL path when the KB already has an index (the
        #    common case after the first archive): only the NEW chunks are
        #    jieba-tokenized, so archiving stays O(n) in tokenization instead of the
        #    O(n^2) it was when every archive rebuilt the whole index from the full
        #    table (CONTEXT_REFACTOR_PLAN step 6). On a cold index (first archive
        #    this process, or after a restart that has not rebuilt yet) materialize
        #    the full set from DB so nothing already persisted is dropped from recall.
        if bm25_index.has_index(mem_kb):
            bm25_index.add(mem_kb, chunk_dicts)
        else:
            async with db_mod.async_session() as db:
                rows = (
                    await db.execute(
                        select(MemoryChunk)
                        .where(MemoryChunk.conversation_id == conv_id)
                        .order_by(MemoryChunk.chunk_index)
                    )
                ).scalars().all()
            bm25_index.build(mem_kb, [_chunk_to_dict(c) for c in rows])
        return True
    except Exception as e:
        logger.warning("Memory archive (essential) failed for conv=%s: %s", conv_id, e)
        return False


async def _embed_archived_chunks(conv_id: str, chunk_dicts: list[dict]):
    """Embed an already-persisted batch and flip ``embedded=True`` (background).

    Runs after ``archive_memory_essential`` has committed the rows, so a failure
    here only means "BM25-only recall until the next retry" -- never data loss.
    ``process_pending_memory`` retries leftovers on the next startup.
    """
    mem_kb = f"mem_{conv_id}"
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, vector_store.add_chunks, mem_kb, chunk_dicts
        )
        async with db_mod.async_session() as db:
            await db.execute(
                update(MemoryChunk)
                .where(
                    MemoryChunk.conversation_id == conv_id,
                    MemoryChunk.id.in_([c["id"] for c in chunk_dicts]),
                )
                .values(embedded=True)
            )
            await db.commit()
    except RuntimeError as e:
        if "EMBED_MODEL_NOT_INSTALLED" in str(e):
            logger.warning(
                "Memory archive embed skipped (no model); BM25-only for conv=%s",
                conv_id,
            )
        else:
            logger.warning("Memory archive embed failed for conv=%s: %s", conv_id, e)
    except Exception as e:
        logger.warning("Memory archive embed task failed for conv=%s: %s", conv_id, e)


def schedule_memory_embedding(conv_id: str, chunk_dicts: list[dict]):
    """Schedule the expensive half of archival (embedding) as fire-and-forget."""
    if not conv_id or not chunk_dicts:
        return
    task = asyncio.create_task(_embed_archived_chunks(conv_id, chunk_dicts))
    _MEM_TASKS.add(task)
    task.add_done_callback(_MEM_TASKS.discard)


# Tearing a conversation's memory down (vectors + BM25 + memory_chunks rows) now
# lives in ``services.conversation_purge``: dropping a Chroma collection is
# blocking disk I/O and the row delete has to be batched, so it belongs in the
# throttled background purge rather than on the delete request path. This module
# still owns the in-memory has_memory flag, which the request clears inline via
# ``unmark_has_memory``.


async def process_pending_memory():
    """On startup: rebuild BM25 for every conversation that has memory chunks and
    best-effort embed any not-yet-embedded chunks (e.g. after an embedding model
    was installed post-hoc)."""
    try:
        async with db_mod.async_session() as db:
            result = await db.execute(
                select(MemoryChunk.conversation_id).distinct()
            )
            conv_ids = [r[0] for r in result.fetchall()]

        for conv_id in conv_ids:
            mem_kb = f"mem_{conv_id}"
            mark_has_memory(conv_id)
            async with db_mod.async_session() as db:
                rows = (
                    await db.execute(
                        select(MemoryChunk)
                        .where(MemoryChunk.conversation_id == conv_id)
                        .order_by(MemoryChunk.chunk_index)
                    )
                ).scalars().all()
            if not rows:
                continue

            dicts = [_chunk_to_dict(c) for c in rows]
            # BM25 is always rebuilt (guaranteed recall path after a restart).
            bm25_index.build(mem_kb, dicts)

            if any(not c.embedded for c in rows):
                ids = [c.id for c in rows]
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None, _embed_mem_chunks, mem_kb, dicts
                    )
                    async with db_mod.async_session() as db2:
                        await db2.execute(
                            update(MemoryChunk)
                            .where(MemoryChunk.id.in_(ids))
                            .values(embedded=True)
                        )
                        await db2.commit()
                except RuntimeError as e:
                    if "EMBED_MODEL_NOT_INSTALLED" in str(e):
                        logger.warning(
                            "process_pending_memory: embed skipped (no model) for conv=%s",
                            conv_id,
                        )
                    else:
                        logger.warning(
                            "process_pending_memory embed error conv=%s: %s", conv_id, e
                        )
        logger.info("Memory archive startup processed %d conversation(s)", len(conv_ids))
    except Exception as e:
        logger.warning("process_pending_memory error: %s", e)

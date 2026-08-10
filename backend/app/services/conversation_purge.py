"""Throttled background purge of the rows a deleted conversation leaves behind.

``DELETE /api/conversations/{id}`` must feel instant, but a long conversation can
own thousands of ``messages`` / ``agent_steps`` rows plus a Chroma collection.
Doing that work inside the request -- especially through SQLAlchemy's ORM cascade,
which loads every child row into memory before issuing per-row DELETEs -- blocks
the event loop and holds SQLite's single writer lock for one long stretch, which
is exactly what makes concurrent chat turns stall.

Deletion is therefore split in two:

- The request deletes ONLY the ``conversations`` row (one indexed DELETE) and
  drops the in-memory state that would otherwise keep serving the conversation
  (history cache, BM25 index, has-memory flag). To the user the conversation is
  gone the moment the response returns.
- Everything heavy is handed to :func:`schedule_purge`: a background task that
  walks the child tables in small committed batches, sleeps between batches and
  takes ONE slot of the shared write semaphore per batch, so the purge queues
  behind ordinary API writes instead of competing with them. Only one purge runs
  at a time process-wide.

Orphaned child rows are thus normal for a short window. They would become
permanent if the process died mid-purge, so :func:`sweep_orphans` re-runs the
same batched cleanup at startup for every child row whose conversation no longer
exists.
"""

import asyncio
import logging

from sqlalchemy import delete, select

import app.database as db_mod
from app.models.conversation import AgentStep, Conversation, Message, PendingLimitState
from app.models.memory_chunk import MemoryChunk
from app.services.bm25_index import bm25_index
from app.services.vector_store import vector_store

logger = logging.getLogger("ragclaw.conversation_purge")

# Rows removed per committed batch. Small enough that a single batch holds the
# SQLite writer lock for a few milliseconds at most.
_BATCH_SIZE = 200

# Idle gap between batches. Yields the event loop AND leaves the writer lock free
# for chat turns, so a multi-thousand-row purge stays invisible to the user.
_BATCH_PAUSE_S = 0.05

# Hard ceiling on batch iterations per table. A batch that deletes nothing already
# breaks the loop; this is the belt-and-braces guard against a pathological case
# where the same ids keep coming back (never observed, but a spinning background
# task would be far worse than a logged warning).
_MAX_BATCHES = 100_000

# Purges are serialized process-wide: deleting five conversations in a row should
# cost the same total DB pressure as deleting them one by one.
_PURGE_LOCK = asyncio.Lock()

# Conversations with a purge scheduled or in flight, so a double-click (or a
# retry after a failed request) never starts two purges for the same id.
_PURGING: set[str] = set()

# Strong refs to fire-and-forget tasks (asyncio only holds weak ones).
_PURGE_TASKS: set[asyncio.Task] = set()


async def _purge_rows(model, pk_col, where_clause, label: str) -> int:
    """Delete matching rows in small committed batches. Never raises.

    Each batch opens its own short transaction and takes one slot of the shared
    write semaphore, so the purge is subject to the same write-concurrency cap as
    the API and can never hold the writer lock across the whole table.
    """
    removed = 0
    for _ in range(_MAX_BATCHES):
        try:
            async with db_mod.write_semaphore:
                async with db_mod.async_session() as db:
                    ids = (
                        await db.execute(
                            select(pk_col).where(where_clause).limit(_BATCH_SIZE)
                        )
                    ).scalars().all()
                    if not ids:
                        return removed
                    await db.execute(delete(model).where(pk_col.in_(ids)))
                    await db.commit()
        except Exception as e:
            logger.warning("Purge batch failed (%s): %s", label, e)
            return removed

        removed += len(ids)
        # A short final batch means the table is drained; skip the extra query.
        if len(ids) < _BATCH_SIZE:
            return removed
        await asyncio.sleep(_BATCH_PAUSE_S)

    logger.warning("Purge hit the batch ceiling (%s) after %d rows", label, removed)
    return removed


async def _drop_mem_collection(conv_id: str) -> None:
    """Drop the Chroma collection + BM25 index of a conversation's memory KB.

    ``vector_store.delete_collection`` is synchronous disk I/O, so it runs in an
    executor: a purge must never stall the event loop that is streaming answers
    for other conversations.
    """
    mem_kb = f"mem_{conv_id}"
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, vector_store.delete_collection, mem_kb)
    except Exception as e:
        logger.warning("Purge could not drop vectors for conv=%s: %s", conv_id, e)
    try:
        bm25_index.delete_kb(mem_kb)
    except Exception:
        pass


async def purge_conversation(conv_id: str) -> None:
    """Remove every child row of an already-deleted conversation. Never raises.

    Order matters: the Chroma collection is dropped BEFORE the ``memory_chunks``
    rows that point at it. A crash in between then leaves rows that
    :func:`sweep_orphans` can still find and retry; the reverse order would leave
    an orphaned collection nothing knows how to name.
    """
    async with _PURGE_LOCK:
        try:
            await _drop_mem_collection(conv_id)

            # agent_steps first: they reference messages.
            steps = await _purge_rows(
                AgentStep, AgentStep.id,
                AgentStep.conversation_id == conv_id,
                f"agent_steps conv={conv_id}",
            )
            msgs = await _purge_rows(
                Message, Message.id,
                Message.conversation_id == conv_id,
                f"messages conv={conv_id}",
            )
            chunks = await _purge_rows(
                MemoryChunk, MemoryChunk.id,
                MemoryChunk.conversation_id == conv_id,
                f"memory_chunks conv={conv_id}",
            )
            # One row at most -- batching would be pure overhead.
            pending = await _purge_rows(
                PendingLimitState, PendingLimitState.conversation_id,
                PendingLimitState.conversation_id == conv_id,
                f"pending_limit_states conv={conv_id}",
            )
            logger.info(
                "Purged conversation %s: %d step(s), %d message(s), %d memory chunk(s), %d pending row(s)",
                conv_id, steps, msgs, chunks, pending,
            )
        except Exception as e:  # defensive: a background task must never die loudly
            logger.warning("Purge failed for conv=%s: %s", conv_id, e)
        finally:
            _PURGING.discard(conv_id)


def schedule_purge(conv_id: str) -> bool:
    """Queue a conversation purge as fire-and-forget. Returns False if already queued."""
    if not conv_id or conv_id in _PURGING:
        return False
    _PURGING.add(conv_id)
    task = asyncio.create_task(purge_conversation(conv_id))
    _PURGE_TASKS.add(task)
    task.add_done_callback(_PURGE_TASKS.discard)
    return True


async def sweep_orphans() -> None:
    """Startup safety net: purge child rows whose conversation no longer exists.

    Covers the one window the split delete cannot: a process that dies after the
    ``conversations`` row is gone but before :func:`purge_conversation` finishes.
    Uses the same throttled batching, so a large backlog cannot slow down startup
    or the first requests after it.
    """
    try:
        live_convs = select(Conversation.id)

        # Resolve orphaned memory KBs before deleting their rows -- afterwards
        # there is no way to know which collections to drop.
        async with db_mod.async_session() as db:
            orphan_mem_convs = (
                await db.execute(
                    select(MemoryChunk.conversation_id)
                    .where(MemoryChunk.conversation_id.notin_(live_convs))
                    .distinct()
                )
            ).scalars().all()
        for conv_id in orphan_mem_convs:
            await _drop_mem_collection(conv_id)

        steps = await _purge_rows(
            AgentStep, AgentStep.id,
            AgentStep.conversation_id.notin_(live_convs),
            "orphan agent_steps",
        )
        msgs = await _purge_rows(
            Message, Message.id,
            Message.conversation_id.notin_(live_convs),
            "orphan messages",
        )
        chunks = await _purge_rows(
            MemoryChunk, MemoryChunk.id,
            MemoryChunk.conversation_id.notin_(live_convs),
            "orphan memory_chunks",
        )
        pending = await _purge_rows(
            PendingLimitState, PendingLimitState.conversation_id,
            PendingLimitState.conversation_id.notin_(live_convs),
            "orphan pending_limit_states",
        )
        if steps or msgs or chunks or pending:
            logger.info(
                "Orphan sweep removed %d step(s), %d message(s), %d memory chunk(s), %d pending row(s)",
                steps, msgs, chunks, pending,
            )
    except Exception as e:
        logger.warning("Orphan sweep error: %s", e)

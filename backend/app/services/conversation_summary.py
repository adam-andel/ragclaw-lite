"""Conversation-history compression (Layer 1: adaptive token-budget cascade).

Goal: keep the LLM context within the model's window by summarizing the oldest
part of a long conversation instead of dropping it, and by recursively compacting
the accumulated summary and even the current query when the window is still tight.

Design principles
-----------------
- We never truncate history silently. The full conversation is always loaded from
  the DB; raw ``Message`` rows are NEVER modified.
- A single boundary, ``budget = context_window - (max_tokens + SAFETY_MARGIN)``,
  drives every decision. Nothing is compressed until the estimated request would
  exceed that boundary, so we maximize window utilization and avoid premature
  compression (which hurts UX).
- Everything is measured in *tokens* (via tiktoken), never message counts, because
  messages vary wildly in length.
- The cascade, in order, only when still over budget:
    0. Fits -> return verbatim (zero compression).
    1. Compress oldest 2/3 of history; if still over, expand the range to the
       whole remaining history (single full pass, no duplicated folds). Cursor
       ``summary_msg_count`` records how much was folded.
    2. Re-compact the accumulated summary itself (with a "only adopt if it
       actually shrinks" progress guard, bounded iterations).
    3. Condense the query ONLY when it is itself long enough
       (>= ``QUERY_COMPRESS_MIN_TOKENS``): keep head + tail verbatim, summarize
       the middle. This is lossy, so the caller is told to warn the user.
       Short queries skip this and fall through to the assembly-point guard.
- Mechanical trimming is NOT done here. Anything still over budget after the
  cascade is handled by :func:`fit_assembly_context`, which runs immediately
  before every LLM submission. That is the only place with visibility into the
  FULL payload (RAG chunks, archived-memory recall, tool records, system
  prefix) -- trimming at turn start would be guessing with half the load
  invisible, and would discard summary paragraphs that the retrieval node may
  well recall verbatim from archived memory moments later.

The summary is injected as an independent ``system`` message that sits AFTER the
fixed cached system prefix and BEFORE the verbatim history, so it does not break
provider-side prompt caching of the stable system prefix.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_client
from app.services.config_manager import config_manager
from app.services.context_budget import (
    default_budget,
    record_tool_tokens,
    total_budget,
)
from app.services.token_count import (
    _get_encoder,
    count_messages_tokens,
    count_text_tokens,
    count_tools_tokens,
)
from app.services.memory_archive import (
    archive_memory_essential,
    schedule_memory_embedding,
)
from app.services.i18n import t as _t

logger = logging.getLogger("ragclaw.summary")

# NOTE: all context-window arithmetic lives in app.services.context_budget
# (single source of truth). This module only consumes it.

# Cap a single summarization output so the summary can never dominate the context.
SUMMARY_MAX_TOKENS = 2000

# Step 6: L0 archive chunker. A folded L0 paragraph is capped at SUMMARY_MAX_TOKENS
# (2000); we split each archived paragraph into retrieval-friendly sub-chunks at
# most L0_CHUNK_MAX_TOKENS (800) so a stored chunk is a coherent unit rather than a
# mid-sentence fragment (which is noise in the vector/BM25 space). ~3 chunks/segment.
L0_CHUNK_MAX_TOKENS = 800

# Bound a single maybe_archive_and_compact call so a pathological L0 (many folds over
# the HIGH% cap) can never spin the request or background task. The loop re-runs
# every turn and self-guards on the cap, so leftovers are archived on subsequent turns.
L0_MAX_ARCHIVE_SEGMENTS_PER_CALL = 32

# Proportions for query condensation: keep this much of the head/tail verbatim,
# summarize the middle.
QUERY_KEEP_HEAD_FRAC = 0.25
QUERY_KEEP_TAIL_FRAC = 0.35

# Minimum query length (tokens) before Layer 1 will even attempt lossy query
# condensation. Below this, condensing saves almost nothing -- and because the
# middle-segment summary is capped at SUMMARY_MAX_TOKENS, short queries can even
# grow. Oversized queries are rejected at the API entry point
# (query_exceeds_context_window) before any heavy processing, so nothing here
# needs to truncate them. Tunable.
QUERY_COMPRESS_MIN_TOKENS = 2048

# The query is never hard-truncated at the assembly point: oversized queries are
# rejected at the API entry point (query_exceeds_context_window), and any residual
# overflow that survives fit_assembly_context is surfaced to the caller as an
# upstream 400 rather than silently mangling the user's question.

# Prompts below were consolidated into the backend i18n dict (app/services/i18n):
#   summary_prompt, summary_recompact_prompt, query_condensed_warning,
#   assembly_trim_warning -> resolved via _t(key, prompt_language).

# Chunk delimiter used by agent_nodes._build_context when joining retrieved chunks
# into `rag_context`. Trimming splits on it to drop the least-relevant (tail) chunks
# while keeping the most-relevant (head) ones (fuse() returns chunks best-first).
RAG_CHUNK_DELIM = "\n\n---\n\n"

# Memory recall chunks are joined with the same delimiter so _trim_memory_oldest can
# drop the least-relevant (tail) chunk without mis-splitting a recalled passage that
# itself contains blank lines (the old "\n\n" join in _format_memory would have cut
# inside a chunk). Kept identical to RAG_CHUNK_DELIM for one less thing to remember.
MEM_CHUNK_DELIM = "\n\n---\n\n"


# --------------------------------------------------------------------------- #
# Token budget helpers
# --------------------------------------------------------------------------- #
def _budget() -> int:
    """Max input tokens allowed: window minus the reserved output allowance.

    Thin alias over ``context_budget.total_budget()`` -- kept because this name
    is used throughout the module. Pure arithmetic, no tokenization.
    """
    return total_budget()


def _empty_context_request_tokens(query: str) -> int:
    """Token cost of the request if EVERYTHING but the fixed prefix + query were
    dropped -- i.e. the floor ``fit_assembly_context`` can reach.

    Reproduces the unconditionally-emitted head of ``_assemble`` in agent_nodes.py
    (the tool-mode system prompt + the "## Task Background" block built from the
    configured system prompt) plus a minimal user question, so the entry-point
    firewall can measure the same floor fit would hit at its empty-context
    fallthrough. This is the floor of the real prefix -- an active skill / KB /
    user-memory can add more -- which errs toward letting borderline queries
    through to the precise fit guard rather than rejecting them outright.
    """
    tool_sys = _t("tool_system", config_manager.prompt_language, tool_desc="")
    task_bg = "## Task Background (reference only)\n" + (config_manager.system_prompt or "")
    msgs = [
        {"role": "system", "content": tool_sys},
        {"role": "system", "content": task_bg},
        {"role": "user", "content": "## Question\n" + query},
    ]
    return count_messages_tokens(msgs)


def query_exceeds_context_window(query: str) -> bool:
    """True when the query cannot fit even with an empty surrounding context.

    Used as an early-exit guard at the request entry point so an oversized query
    is rejected before any history compression, RAG, or LLM call runs.

    The check reserves the fixed system-prefix cost (see
    ``_empty_context_request_tokens``): fit_assembly_context can drop
    history / summary / RAG / tool records, but it can never shrink the system
    prefix, so a query that overflows *even with an empty context* can only end
    in an upstream 400. We catch that case here and return a clean user-facing
    QUERY_TOO_LONG instead of burning tokens on a doomed request. (Tool schemas
    are reserved separately inside fit via ``eff_budget``; they are not yet known
    at the entry point, so this guard deliberately ignores them.)
    """
    if not query:
        return False
    return _empty_context_request_tokens(query) > _budget()


def _overhead() -> int:
    """Non-persistent context cost, clamped so the cascade can always converge.

    Everything in the request that is NOT un-summarized history + L0: the system
    prefix, the tool schemas, and the RAG / memory slots. Previously a flat
    16000-token guess (this module cannot see those components directly); now
    derived by ``context_budget`` from the configured model, language, system
    prompt and the RAG / memory percentages. On a small context window the
    reserve can still approach the entire budget, which would make every stage
    look hopeless and skip compression entirely -- the clamp leaves at least a
    sliver of room for real content.
    """
    return min(default_budget().reserved, max(0, _budget() - 512))


def _estimate(history: list[dict], summary_text: str, query_tok: int) -> int:
    """Approximate total input tokens for the request about to be sent."""
    return (
        count_messages_tokens(history)
        + (count_text_tokens(summary_text) if summary_text else 0)
        + query_tok
        + _overhead()
    )


def _transcript(messages: list[dict]) -> str:
    return "\n".join(
        f"{m.get('role', '')}: {m.get('content', '')}" for m in messages
    )


def _message_tokens(m: dict) -> int:
    """Token mass of a single history message for fold-budget math.

    Uses the stored ``content_token_count`` (content tokens + 4 per-message
    overhead, written at insert time) when present, falling back to a live
    ``count_text_tokens`` for legacy rows that predate the column (project not
    yet launched; historical rows may be NULL).
    """
    ct = m.get("content_token_count")
    if ct:
        return int(ct)
    return count_text_tokens(m.get("content") or "") + 4


# --------------------------------------------------------------------------- #
# Segment planning (pure: no DB, no persistent state) -- CONTEXT_REFACTOR_PLAN §3.
# Replaces the synchronous _token_round_split fold point. The async executor
# (Step 5) reuses the SAME planner against DB-backed rounds keyed by Message.seq.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Round:
    """One Q/A exchange. ``start``/``end`` are opaque boundaries (message index in
    the sync path, ``Message.seq`` in the async executor); ``end`` is exclusive."""

    start: int
    end: int
    tokens: int
    text: str


@dataclass(frozen=True)
class SummaryUnit:
    """One LLM summarization call. ``kind`` distinguishes a whole round from an
    intra-round slice produced by splitting an over-long message (plan §3)."""

    text: str
    kind: str  # "rounds" | "msg_slice"


@dataclass(frozen=True)
class Segment:
    start: int  # == cursor: first message not yet summarized
    end: int  # exclusive boundary of the last folded round
    units: tuple  # tuple[SummaryUnit, ...]
    total_tokens: int


@dataclass(frozen=True)
class ArchiveL0:
    """Planning exhausted the un-summarized tail but its mass is below MIN: the
    rolling L0 window itself is the bloat. Route to the zero-LLM L0 archive chain
    instead of folding more history (avoids per-turn no-op spin + warning spam)."""

    pass


# Floating upper bound per plan §4: MAX = clamp(window*0.15, 8000, 50000),
# MIN = MAX/4. Defaults 20000/5000 when the window is unknown.
def segment_thresholds(window: int | None) -> tuple[int, int]:
    if not window or window <= 0:
        return 5000, 20000
    mx = max(8000, min(50000, int(window * 0.15)))
    return mx // 4, mx


_SENT_RE = re.compile(r"(?<=[.!?。！？\n])(\s+)")


def _hard_split(text: str, max_tok: int) -> list[str]:
    """Split ``text`` to pieces each <= ``max_tok``. Sentence boundaries first
    (preserving the whitespace separator so reconstruction is exact), then a hard
    character cut. Token count is estimated via the shared tokenizer; the cut is
    approximate but always terminates."""
    parts = _SENT_RE.split(text)  # alternating [sentence, sep, sentence, sep, ...]
    if len(parts) > 1:
        out, cur, cur_tok = [], "", 0
        for i in range(0, len(parts), 2):
            s = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
            st = count_text_tokens(s)
            if st > max_tok:
                if cur:
                    out.append(cur)
                    cur, cur_tok = "", 0
                out.extend(_char_split(s, max_tok))
                continue
            if cur_tok and cur_tok + st > max_tok:
                out.append(cur)
                cur, cur_tok = s, st
            else:
                cur = (cur + s) if cur else s
                cur_tok += st
        if cur:
            out.append(cur)
        return out
    return _char_split(text, max_tok)


def _char_split(text: str, max_tok: int) -> list[str]:
    est = count_text_tokens(text)
    step = max(1, int(max_tok * max(1, round(est / max(1, len(text))))))
    return [text[i : i + step] for i in range(0, len(text), step)]


def split_long_unit(text: str, max_tok: int) -> list[str]:
    """Break an over-long unit (a single round or message) into atomic chunks each
    <= ``max_tok``. Paragraph (``\\n\\n``) boundaries first; any paragraph still too
    big falls through to sentence-aware then hard cut (see _hard_split)."""
    if not text.strip():
        return []
    paras = [p for p in text.split("\n\n") if p]
    if not paras:
        return [text] if text.strip() else []
    chunks, cur, cur_tok = [], "", 0
    for p in paras:
        pt = count_text_tokens(p)
        if pt > max_tok:
            if cur:
                chunks.append(cur)
                cur, cur_tok = "", 0
            chunks.extend(_hard_split(p, max_tok))
            continue
        if cur_tok and cur_tok + pt > max_tok:
            chunks.append(cur)
            cur, cur_tok = p, pt
        else:
            cur = (cur + "\n\n" + p) if cur else p
            cur_tok += pt
    if cur:
        chunks.append(cur)
    return chunks


def _segment_units(rounds: list[Round], cursor: int, end: int, max_tok: int) -> tuple:
    units = []
    for r in rounds:
        if r.start < cursor or r.end > end:
            continue
        if r.tokens > max_tok:
            for piece in split_long_unit(r.text, max_tok):
                units.append(SummaryUnit(piece, "msg_slice"))
        else:
            units.append(SummaryUnit(r.text, "rounds"))
    return tuple(units)


def plan_segment(rounds: list[Round], cursor: int, min_tok: int, max_tok: int):
    """Plan one segment to summarize next. Pure: identical inputs -> same result.

    Walks rounds after ``cursor`` (ascending), accumulating token mass until it
    crosses a threshold:
      - crossing ``max_tok`` with ``acc >= min_tok`` -> emit (don't merge the round
        that would exceed it);
      - a single round exceeding ``max_tok`` while ``acc < min_tok`` -> merge it and
        split it at execution time (plan §3 "超长轮的单元切分"); the segment is then
        necessarily >= MAX so it emits;
      - accumulating to ``>= max_tok`` -> emit;
      - tail drained (``exhausted``) with ``acc >= min_tok`` -> emit (best-effort
        fold on a short tail);
      - tail drained with ``acc < min_tok`` -> ArchiveL0 (no history worth folding;
        L0 archive owns the bloat);
      - ``acc == 0`` -> None (cursor already at the newest round).

    Returns ``Segment | ArchiveL0 | None``. Callers must NOT pass the live (newest)
    round -- invariant #1 keeps it unfolded.
    """
    acc = 0
    end = cursor
    exhausted = True
    for r in rounds:
        if r.start < cursor:
            continue
        rt = r.tokens
        if acc + rt > max_tok:
            if acc >= min_tok:
                exhausted = False
                break  # keep acc/end from previous round; emit what we have
            end, acc = r.end, acc + rt  # merge over-long round, split on exec
            exhausted = False
            break
        acc, end = acc + rt, r.end
        if acc >= max_tok:
            exhausted = False
            break

    if acc == 0:
        return None
    if acc >= max_tok:
        return Segment(cursor, end, _segment_units(rounds, cursor, end, max_tok), acc)
    if acc >= min_tok:
        # Enough buffered (>= MIN) and the next round would overflow MAX, so stop
        # here and dispatch -- the loop's early break set exhausted=False on purpose.
        return Segment(cursor, end, _segment_units(rounds, cursor, end, max_tok), acc)
    if exhausted:
        return ArchiveL0()
    return None


def _history_to_rounds(messages: list[dict]) -> list[Round]:
    """Group a message list into rounds (user msg .. before next user msg)."""
    rounds: list[Round] = []
    cur_start: int | None = None
    cur_msgs: list[dict] = []

    def flush() -> None:
        nonlocal cur_start, cur_msgs
        if cur_start is None or not cur_msgs:
            cur_start, cur_msgs = None, []
            return
        text = _transcript(cur_msgs)
        toks = sum(_message_tokens(m) for m in cur_msgs) + 3  # +3 reply-priming
        rounds.append(Round(cur_start, cur_start + len(cur_msgs), toks, text))
        cur_start, cur_msgs = None, []

    for i, m in enumerate(messages):
        if m.get("role") == "user":
            flush()
            cur_start = i
            cur_msgs = [m]
        elif cur_start is not None:
            cur_msgs.append(m)
    flush()
    return rounds


def plan_segment_sync(history_tail: list[dict], min_tok: int, max_tok: int) -> int | None:
    """Plan a fold boundary for the synchronous path. ``history_tail`` is the
    un-summarized tail (``history[k:]``). Returns the EXCLUSIVE message index
    within ``history_tail`` at which to fold, or None to skip folding this turn
    (fit_assembly_context is the backstop). The newest (live) round is excluded
    per invariant #1.
    """
    rounds = _history_to_rounds(history_tail)
    if len(rounds) <= 1:
        return None
    plan = plan_segment(rounds[:-1], cursor=0, min_tok=min_tok, max_tok=max_tok)
    if isinstance(plan, Segment):
        return plan.end  # exclusive index within history_tail
    return None


# --------------------------------------------------------------------------- #
# Step 6: L0 archive chunker (sentence/paragraph boundary, never hard token cut)
# --------------------------------------------------------------------------- #
# Sentence boundary that works for BOTH English and Chinese. The older _SENT_RE used
# by split_long_unit requires whitespace after the terminal punctuation, which
# Chinese text lacks, so it silently degrades to a hard char cut there. The L0
# chunker must respect "split on sentence/paragraph boundary" per the plan, so it
# uses its own regex that breaks right after a terminal char regardless of spacing.
_L0_SENT_RE = re.compile(r"[^。！？!?]*[。！？!?]|[^。！？!?]+")


def _chunk_l0_segment(text: str, max_tok: int = L0_CHUNK_MAX_TOKENS) -> list[str]:
    """Split one L0 fold paragraph into retrieval-friendly sub-chunks.

    Boundaries are SENTENCE-first (works for Chinese too, see _L0_SENT_RE), then
    paragraph, then a hard char cut as a last resort -- a chunk is never a
    mid-sentence fragment, because half a sentence in the vector/BM25 space is
    essentially noise. Pure: no DB, no LLM.
    """
    text = text.strip()
    if not text:
        return []
    sentences = [s for s in _L0_SENT_RE.findall(text) if s.strip()]
    if len(sentences) <= 1:
        # No clean sentence boundary -> fall back to paragraph, then hard cut.
        return split_long_unit(text, max_tok) or [text]
    chunks, cur, cur_tok = [], "", 0
    for s in sentences:
        st = count_text_tokens(s)
        if st > max_tok:
            if cur:
                chunks.append(cur)
                cur, cur_tok = "", 0
            chunks.extend(split_long_unit(s, max_tok))
            continue
        if cur_tok and cur_tok + st > max_tok:
            chunks.append(cur)
            cur, cur_tok = s, st
        else:
            cur = (cur + s) if cur else s
            cur_tok += st
    if cur:
        chunks.append(cur)
    return chunks or [text]


def _segment_heading(segment: str) -> str:
    """Cheap, LLM-free title for an archived fold paragraph: its first sentence,
    truncated to the MemoryChunk.heading column width (String(200))."""
    first = _SENT_RE.split(segment.strip())[0].strip()
    if not first:
        first = segment.strip()[:80]
    return first[:200]


async def maybe_archive_and_compact(conv, db: AsyncSession, prompt_language: str):
    """Two-tier memory maintenance, run after Stage (1) has folded history.

    L0 (the rolling summary window, editable): when it exceeds
    ``summary_archive_high_pct`` of the PERSISTENT block P (not of the raw
    window -- see context_budget), move every fold paragraph EXCEPT the most
    recent one into long-term memory as MemoryChunks. L0 is left holding only
    the most recent fold paragraph; the archived folds come back on demand via
    hybrid recall (``memory_context``) instead of riding along in every prompt.

    There is deliberately no secondary ("L1") summary tier: a summary of
    summaries costs an extra LLM call per archive, is unconditionally injected
    into every subsequent turn, and duplicates content that recall already
    surfaces on demand. Archived folds are the single source of older context.

    The archive's retrieval-critical half (DB write + BM25 build) is awaited --
    this function runs inside ``build_context_with_summary``, which the chat
    router awaits BEFORE starting the agent graph, so the just-archived folds
    are already indexed when the retrieval node runs later in the same turn.
    Only embedding stays fire-and-forget (it is the slow part); hybrid retrieval
    degrades to BM25-only for this turn and becomes full hybrid from the next.

    Degenerate case: a single L0 fold paragraph that still exceeds HIGH% cannot be
    split into "older" parts, so it is re-compacted in place to preserve the
    "L0 = recent window" invariant.

    Never raises: all LLM/DB failures are swallowed so this never blocks the turn.
    """
    l0 = conv.summary_text or ""
    if not l0:
        return
    # Denominator is P (persistent block), not the raw context window: L0 shares
    # P with the un-summarized history tail, and only those two accumulate
    # across turns. A cap of 0 means the window is misconfigured to the point
    # where there is no persistent room at all -- archiving cannot help, and
    # fit_assembly_context is the backstop.
    cap = default_budget().l0_cap()
    if cap <= 0 or count_text_tokens(l0) < cap:
        return

    segs = l0.split("\n")
    conv_id = getattr(conv, "id", "") or ""

    if len(segs) <= 1:
        # Degenerate: only one fold paragraph and still over HIGH% -> re-compact it
        # in place (preserves the "L0 = recent window" invariant).
        compacted = await _summarize_text(l0, prompt_language, recompact=True)
        if compacted and len(compacted) < len(l0):
            conv.summary_text = compacted
            await db.commit()
        return

    # Archive loop: move the OLDEST fold paragraph into long-term memory, shrink
    # L0, re-check the watermark -- repeat until L0 drops below HIGH% or only the
    # most-recent fold remains. Each segment is chunked (sentence/paragraph
    # boundaries), archived, and committed atomically, so a partial archive is
    # always consistent. Bounded by L0_MAX_ARCHIVE_SEGMENTS_PER_CALL so a
    # pathological L0 can never spin the request/background task; leftovers are
    # archived on subsequent turns (this function self-guards on the cap).
    remaining = segs
    archived_segments = 0
    archived_chunk_dicts: list[dict] = []
    for _ in range(L0_MAX_ARCHIVE_SEGMENTS_PER_CALL):
        if len(remaining) <= 1:
            break
        if count_text_tokens("\n".join(remaining)) < cap:
            break
        oldest = remaining[0]
        rest = remaining[1:]
        heading = _segment_heading(oldest)
        chunk_dicts = [
            {
                "id": str(uuid.uuid4()),
                "content": piece,
                "chunk_index": i,
                "doc_id": f"mem_{conv_id}",
                "heading": heading,
                "page": 0,
                "token_count": count_text_tokens(piece),
            }
            for i, piece in enumerate(_chunk_l0_segment(oldest))
        ]
        if not chunk_dicts:
            # Empty segment (whitespace only): drop it from L0 and continue.
            remaining = rest
            conv.summary_text = "\n".join(remaining)
            await db.commit()
            continue

        # Release this session's transaction before the archive writes on its OWN
        # session/connection; holding an open read txn here makes SQLite block the
        # archive COMMIT on our shared lock until the busy timeout expires.
        await db.commit()

        # Retrieval-critical half: persist rows + mark_has_memory + incremental
        # BM25. Cannot raise -- returns False on failure.
        archived = await archive_memory_essential(conv_id, chunk_dicts)
        if not archived:
            # Archival failed (DB / index error). Keep the raw folds inline in L0 --
            # that is lossless and the next turn simply retries.
            logger.warning(
                "Memory archive failed for conv=%s; keeping L0 folds inline", conv_id
            )
            break

        remaining = rest
        conv.summary_text = "\n".join(remaining)
        conv.summary_archived_count = (
            getattr(conv, "summary_archived_count", 0) or 0
        ) + 1
        await db.commit()
        archived_segments += 1
        archived_chunk_dicts.extend(chunk_dicts)

    if archived_segments:
        # Expensive half: embedding stays in the background so it is never charged
        # to time-to-first-token. This turn recalls the new chunks via BM25; vectors
        # join in from the next turn on.
        schedule_memory_embedding(conv_id, archived_chunk_dicts)


# --------------------------------------------------------------------------- #
# Step 5: asynchronous summarization executor + dual watermark (70% / 80% of P)
# --------------------------------------------------------------------------- #
_INFLIGHT: set = set()            # per-conversation guard (plan §4 invariant #4)
_BACKGROUND_TASKS: set = set()   # keep fire-and-forget tasks referenced
MAX_SUMMARY_PASSES = 8           # iteration cap for the blocking (sync) path


def schedule_summary_pass(conv_id: str) -> None:
    """Fire-and-forget a background summarization pass (async watermark, >=70% of P).

    No-op when a pass is already in flight for this conversation. The in-flight
    guard is set SYNCHRONOUSLY here (not inside the task body) so two synchronous
    calls for the same conversation cannot both spawn a task -- the previous design
    set the guard only inside the coroutine, which runs later on the event loop,
    making the entry-level check a no-op and letting double-tasks race on the
    cursor (plan §4 invariant #4). The task is kept referenced in
    ``_BACKGROUND_TASKS`` so it is never garbage-collected mid-flight, and discards
    both the task reference and the guard on completion.
    """
    if not conv_id or conv_id in _INFLIGHT:
        return
    _INFLIGHT.add(conv_id)  # synchronous: dedup NOW, before any task runs
    task = asyncio.create_task(_run_async_pass(conv_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    task.add_done_callback(lambda _t: _INFLIGHT.discard(conv_id))


async def _run_async_pass(conv_id: str) -> bool:
    """Body of a background (fire-and-forget) pass. Owns no in-flight state --
    :func:`schedule_summary_pass` sets/clears the guard around it. All failures are
    swallowed so a background fold can never crash the turn or the event loop."""
    try:
        return await _run_summary_pass_inner(conv_id, blocking=False)
    except Exception as e:
        logger.warning("run_summary_pass error conv=%s: %s", conv_id, e)
        return False


async def run_summary_pass(conv_id: str, *, blocking: bool, emit=None) -> bool:
    """Plan -> summarize -> CAS-advance the cursor, looping until the persistent
    block falls below ``async_hi`` (70% of P).

    Single executor for BOTH watermarks (plan §3). The sync (``blocking=True``)
    form is awaited directly on the request path and, on an LLM failure, simply
    stops so the caller falls through to :func:`fit_assembly_context` (which trims
    mechanically). The async (``blocking=False``) form is dispatched by
    :func:`schedule_summary_pass` (which wraps it with the in-flight guard) -- that
    swallows failures and is retried next turn.

    Invariants (plan §4): own DB session (never the request's), L0 append and
    cursor advance in ONE CAS-guarded UPDATE keyed on ``summary_msg_seq``, and a
    per-conversation in-flight guard.
    """
    if not conv_id or conv_id in _INFLIGHT:
        return False
    _INFLIGHT.add(conv_id)
    try:
        return await _run_summary_pass_inner(conv_id, blocking=blocking, emit=emit)
    except Exception as e:  # a background fold must never crash the turn/loop
        logger.warning("run_summary_pass error conv=%s: %s", conv_id, e)
        return False
    finally:
        _INFLIGHT.discard(conv_id)


def _round_from_messages(msgs: list) -> list:
    """Group ORM Message rows into plan.Round objects keyed by ``Message.seq``
    (``start`` = first msg seq, ``end`` = last msg seq + 1, exclusive)."""
    rounds: list = []
    cur: list = []

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        text = "\n".join(f"{m.role}: {m.content}" for m in cur)
        toks = sum(
            (int(m.content_token_count) if m.content_token_count else count_text_tokens(m.content or "") + 4)
            for m in cur
        ) + 3
        rounds.append(Round(cur[0].seq, cur[-1].seq + 1, toks, text))
        cur = []

    for m in msgs:
        if m.role == "user":
            flush()
            cur = [m]
        elif cur:
            cur.append(m)
    flush()
    return rounds


async def _persistent_tokens_in(db, conv_id: str, cursor: int, l0: str | None) -> int:
    """Persistent-block size: un-summarized tail (seq > cursor) + L0 summary."""
    from app.models.conversation import Message

    tail = (
        await db.execute(
            select(func.coalesce(func.sum(Message.content_token_count), 0)).where(
                Message.conversation_id == conv_id, Message.seq > cursor
            )
        )
    ).scalar() or 0
    return int(tail) + (count_text_tokens(l0) if l0 else 0)


async def _run_summary_pass_inner(conv_id: str, *, blocking: bool, emit=None) -> bool:
    b = default_budget()
    async_hi, sync_hi = b.async_hi, b.sync_hi
    if async_hi <= 0 or sync_hi <= 0:
        return False
    min_tok, max_tok = segment_thresholds(config_manager.context_window)
    lang = config_manager.prompt_language

    if emit:
        try:
            emit("context_compress", _t("history_compressing", lang))
        except Exception:
            pass

    from app.database import async_session
    from app.models.conversation import Conversation, Message

    async with async_session() as db:
        for _ in range(MAX_SUMMARY_PASSES):
            conv = await db.get(Conversation, conv_id)
            if conv is None:
                return False
            await db.refresh(conv)  # reload cursor/summary a prior pass may have written
            cursor = conv.summary_msg_seq or 0

            # Recompute the persistent block straight from the DB: another pass (or
            # the request turn) may have advanced the cursor since we last looked.
            persistent = await _persistent_tokens_in(db, conv_id, cursor, conv.summary_text)
            if persistent < async_hi:
                return True  # already below the recovery watermark

            msgs = (
                await db.execute(
                    select(Message)
                    .where(Message.conversation_id == conv_id, Message.seq > cursor)
                    .order_by(Message.seq)
                )
            ).scalars().all()
            rounds = _round_from_messages(msgs)
            # Invariant #1: never fold the live (newest) round.
            if len(rounds) <= 1:
                return False
            plan = plan_segment(rounds[:-1], cursor=cursor, min_tok=min_tok, max_tok=max_tok)
            if plan is None:
                return False
            if isinstance(plan, ArchiveL0):
                # Tail below MIN but L0 itself is the bloat: route to the L0
                # archive chain (no LLM). Loop to re-check the watermark.
                await maybe_archive_and_compact(conv, db, lang)
                await db.commit()
                continue

            # Segment: summarize each planned unit, append to L0, CAS-advance cursor.
            paras: list[str] = []
            for unit in plan.units:
                u = await _summarize_text(unit.text, lang)
                if not u:
                    # LLM failed: do NOT advance the cursor on a half-folded
                    # segment. Blocking path falls through to fit; either way stop.
                    return False
                paras.append(u)

            base = conv.summary_text or ""
            new_para = "\n".join(paras)
            candidate = f"{base}\n{new_para}".strip() if base else new_para

            # L0 append + cursor advance in ONE CAS-guarded UPDATE. Keyed on
            # summary_msg_seq == cursor; 0 rows => another pass advanced it first.
            result = await db.execute(
                update(Conversation)
                .where(Conversation.id == conv_id, Conversation.summary_msg_seq == cursor)
                .values(
                    summary_text=candidate,
                    summary_msg_seq=plan.end,
                    # Bridge: keep the positional cursor in lockstep until Step 7
                    # retires summary_msg_count entirely.
                    summary_msg_count=plan.end,
                )
            )
            if result.rowcount == 0:
                return False
            await db.commit()

            # Maintain the L0 rolling window (self-guards on its own HIGH threshold).
            await db.refresh(conv)
            await maybe_archive_and_compact(conv, db, lang)
            await db.commit()

        logger.warning(
            "run_summary_pass: iteration cap (%d) hit for conv=%s; residual left to fit_assembly_context",
            MAX_SUMMARY_PASSES, conv_id,
        )
        return True


# --------------------------------------------------------------------------- #
# LLM-backed summarization
# --------------------------------------------------------------------------- #
async def _summarize_text(
    text: str, prompt_language: str, recompact: bool = False
) -> str:
    """One non-streaming LLM call that compresses ``text`` into a summary.

    ``conversation_id=None`` so these meta-calls are NOT cached per-conversation.
    Returns "" on failure (callers degrade gracefully).
    """
    if not text.strip():
        return ""
    prompt = _t("summary_recompact_prompt" if recompact else "summary_prompt", prompt_language)
    try:
        return (
            await llm_client.chat(
                messages=[{"role": "user", "content": f"{prompt}\n\n{text}"}],
                temperature=0,
                max_tokens=SUMMARY_MAX_TOKENS,
                conversation_id=None,  # meta-call: must NOT be cached per-conversation
            )
        ).strip()
    except Exception as e:  # never block the main turn on a summary failure
        logger.warning("Conversation summary LLM call failed: %s", e)
        return ""


async def _condense_query(
    query: str, base_summary: str, prompt_language: str
) -> str:
    """Condense an over-budget query: keep head + tail verbatim, summarize middle.

    Falls back to trimming the tail (progressively) if even head+tail still
    exceeds the budget. The result is lossy; the caller must warn the user.
    """
    enc = _get_encoder()
    ids = enc.encode(query)
    n_ids = len(ids)
    if n_ids <= 4:
        return query

    head_n = max(1, int(n_ids * QUERY_KEEP_HEAD_FRAC))
    tail_n = max(1, int(n_ids * QUERY_KEEP_TAIL_FRAC))

    if head_n + tail_n >= n_ids:
        # Too short to split meaningfully -> summarize the whole query.
        summary = await _summarize_text(query, prompt_language)
        return summary or query

    head_text = enc.decode(ids[:head_n])
    mid_text = enc.decode(ids[head_n : n_ids - tail_n])
    tail_text = enc.decode(ids[n_ids - tail_n :])

    mid_summary = await _summarize_text(mid_text, prompt_language)
    if not mid_summary:
        mid_summary = ""  # middle summarize failed; drop it rather than block

    assembled = (
        f"{head_text}\n\n[... middle of your message was condensed ...]\n"
        f"{mid_summary}\n\n{tail_text}"
    )

    # Progressive tail trim so the condensed query itself stays within budget.
    budget = _budget()
    while (
        count_text_tokens(assembled)
        + count_text_tokens(base_summary)
        + _overhead()
        > budget
        and tail_n > 1
    ):
        tail_n = max(1, tail_n - max(1, tail_n // 3))
        tail_text = enc.decode(ids[n_ids - tail_n :])
        assembled = (
            f"{head_text}\n\n[... middle of your message was condensed ...]\n"
            f"{mid_summary}\n\n{tail_text}"
        )
    return assembled


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #
async def build_context_with_summary(
    conv,
    history: list[dict],
    db: AsyncSession,
    prompt_language: str,
    query: str = "",
    emit=None,
) -> Tuple[list[dict], str, Optional[str], str]:
    """Return ``(recent_messages, summary_text, final_query, warning)``.

    - ``recent_messages``: verbatim tail of history not yet folded into the summary.
    - ``summary_text``: accumulated (and possibly just-compacted) summary.
    - ``final_query``: the query to actually send -- the original, or the lossily
      condensed form when stage (3) fired. The caller should use this instead of
      the raw query.
    - ``warning``: non-empty when the query was lossily condensed; the caller
      should surface it to the user.

    Raw ``Message`` rows are never modified. The summary is persisted on the
    ``Conversation`` row via the cursor ``summary_msg_count``.

    NOTE: this function performs SEMANTIC (LLM-backed) compression only. It may
    still return a payload that is over budget -- deliberately. Mechanical
    trimming belongs to :func:`fit_assembly_context`, which runs per submission
    and can see the full payload (RAG, archived-memory recall, tool records).
    """
    warning = ""
    n = len(history)
    if n == 0:
        return [], conv.summary_text or "", None, warning

    # Cursor: how many of the earliest messages are already summarized. Positional
    # index; summary_msg_seq is the durable seq-based cursor used by run_summary_pass
    # and is kept in lockstep until Step 7 retires this field.
    k = getattr(conv, "summary_msg_count", 0) or 0
    if k > n:
        k = n  # safety: never exceed history length

    # L0 = the rolling summary window (recent folds, editable). Anything older
    # lives in the memory archive and is recalled on demand, not injected here.
    l0 = conv.summary_text or ""
    q_tok = count_text_tokens(query)

    # (0) Fits -> zero compression. Maximize window utilization.
    if _estimate(history[k:], l0, q_tok) <= _budget():
        return history[k:], l0, None, warning

    # (Step 5) Watermark-gated folding via the shared async executor.
    # `persistent` = un-summarized tail + L0; the watermarks are fractions of P
    # (the persistent block), never of the raw window (see context_budget.py).
    #   persistent >= sync_hi (80% of P)  -> block this turn on a fold
    #   persistent >= async_hi (70% of P) -> kick off a background fold; this turn
    #                                       proceeds un-folded, fit_assembly_context trims
    #   else                                -> no-op (below the watermark)
    conv_id = getattr(conv, "id", "") or ""
    scheduled_bg = False
    b = default_budget()
    if conv_id and b.async_hi > 0 and b.sync_hi > 0:
        persistent = count_messages_tokens(history[k:]) + (count_text_tokens(l0) if l0 else 0)
        if persistent >= b.sync_hi:
            # Blocking: fold now, then re-read the (possibly advanced) cursor.
            await run_summary_pass(conv_id, blocking=True, emit=emit)
            await db.refresh(conv)
            k = getattr(conv, "summary_msg_count", 0) or 0
            if k > n:
                k = n
            l0 = conv.summary_text or ""
        elif persistent >= b.async_hi:
            schedule_summary_pass(conv_id)  # fire-and-forget; no-op this turn
            scheduled_bg = True

    recent = history[k:]
    base = l0

    # (2) L0 archive maintenance. Runs every turn and self-guards on its HIGH
    # threshold, so it is a no-op when L0 is small. Skipped when a background fold
    # was scheduled: that task owns L0 maintenance too, and running it here
    # concurrently would double-archive the same folds (duplicate MemoryChunks).
    if not scheduled_bg:
        await maybe_archive_and_compact(conv, db, prompt_language)
        base = conv.summary_text or ""

    # (3) Still over AND the query itself is long enough that condensing it yields
    # meaningful savings. Below QUERY_COMPRESS_MIN_TOKENS the query is too small to
    # be worth lossy condensation, so we skip it and let fit_assembly_context trim
    # the older history/summary instead.
    if _estimate([], base, q_tok) > _budget() and q_tok >= QUERY_COMPRESS_MIN_TOKENS:
        condensed = await _condense_query(query, base, prompt_language)
        if condensed and count_text_tokens(condensed) < q_tok:
            warning = _t("query_condensed_warning", prompt_language)
            return [], base, condensed, warning

    # Still over budget? Deliberately do NOTHING here.
    #
    # A mechanical trim at this point would be made blind: rag_context,
    # memory_context, user_memory, kb_prompt and the tool descriptions are all
    # produced LATER (retrieval node / assembly points) and are invisible to
    # `_estimate`, which only sees history + summary + query. Cutting against a
    # partial view either over-trims or still overflows.
    #
    # Worse, dropping the oldest summary paragraphs here is often pure waste:
    # those exact folds now live in archived memory and may be recalled verbatim
    # by `parallel_retrieval_node` a moment later.
    #
    # `fit_assembly_context` owns the hard ceiling instead -- it runs right
    # before every LLM submission with the complete payload in hand, and its
    # phase 3 hard-truncates the query when nothing else is left to give.
    #
    # `None` (not `query`) keeps the contract honest: a non-None final_query
    # means "the query was rewritten, use this instead". Nothing rewrote it on
    # this path -- only stage (3) does, and it returns early.
    return recent, base, None, warning


# --------------------------------------------------------------------------- #
# Per-submission context telemetry
# --------------------------------------------------------------------------- #
def context_breakdown(
    summary_text: str | None,
    history: list[dict] | None,
    total_tokens: int,
) -> dict:
    """Split an assembled payload into persistent vs transient token shares.

    - ``persistent``: the compressed summary plus the verbatim history. This is
      the only part that accumulates across turns, and the only part the manual
      "compact" action can shrink.
    - ``transient``: everything else in the same submission (system prefix, RAG
      chunks, memory, tool records, the current question). Recomputed each turn.

    Callers must pass the ALREADY-TRIMMED summary/history so the two shares add
    up to ``total_tokens`` exactly.
    """
    persistent = (count_text_tokens(summary_text) if summary_text else 0) + (
        count_messages_tokens(history) if history else 0
    )
    persistent = max(0, min(persistent, total_tokens))
    return {
        "prompt_tokens": total_tokens,
        "persistent_tokens": persistent,
        "transient_tokens": max(0, total_tokens - persistent),
    }


# --------------------------------------------------------------------------- #
# Manual compaction (user-triggered, unconditional)
# --------------------------------------------------------------------------- #
class CompactionError(RuntimeError):
    """Raised with a BARE error code when manual compaction cannot proceed.

    The code (e.g. ``SUMMARY_LLM_FAILED``) is surfaced verbatim to the frontend,
    which localizes it via ``errors.backendErrorCodes``. Never bake user-facing
    prose in here.
    """


async def compact_conversation(
    conv,
    history: list[dict],
    db: AsyncSession,
    prompt_language: str,
    fraction: float = 0.5,
) -> Tuple[int, int]:
    """Fold the oldest ``fraction`` of the un-summarized history into the summary.

    Returns ``(new_cursor, total_messages)``.

    Unlike :func:`build_context_with_summary` this is UNCONDITIONAL: it ignores
    the token budget entirely because it is driven by the user pressing
    "compact" in the UI, not by an overflow.

    Atomicity: when the summarization LLM call fails (``_summarize_text``
    returns ""), the cursor is NOT advanced and nothing is committed, so a
    failed compaction can never make part of the history invisible to the model.

    The ``max(1, ...)`` guard is required for progress: integer scaling
    degenerates to 0 for a small remainder, which would leave the cursor
    unchanged and make the button silently do nothing.
    """
    n = len(history)
    k = getattr(conv, "summary_msg_count", 0) or 0
    if k > n:
        k = n
    if k >= n:
        raise CompactionError("NOTHING_TO_COMPACT")

    frac = min(max(fraction, 0.0), 1.0)
    split = min(n, k + max(1, int((n - k) * frac)))

    new_para = await _summarize_text(
        _transcript(history[k:split]), prompt_language
    )
    if not new_para:
        raise CompactionError("SUMMARY_LLM_FAILED")

    base = conv.summary_text or ""
    conv.summary_text = f"{base}\n{new_para}".strip() if base else new_para
    conv.summary_msg_count = split
    conv.summary_msg_seq = split  # keep seq cursor consistent (dense seq == positional)
    await db.commit()

    # Feed the folded result through the same memory maintenance as the automatic
    # path, so manual compaction also archives older folds once L0 crosses HIGH%.
    await maybe_archive_and_compact(conv, db, prompt_language)

    logger.info(
        "Manual compaction: conv=%s cursor %d -> %d of %d messages",
        getattr(conv, "id", "?"), k, split, n,
    )
    return split, n


# --------------------------------------------------------------------------- #
# Assembly-point hard ceiling (per-submission, transient)
# --------------------------------------------------------------------------- #
def _trim_summary_oldest(summary: str) -> str:
    """Drop the oldest (front) paragraph of the compressed summary."""
    segs = summary.split("\n")
    if len(segs) <= 1:
        return ""
    return "\n".join(segs[1:])


def _trim_rag_oldest(rag: str) -> str:
    """Drop the least-relevant (tail) RAG chunk; keep the most-relevant head.

    Refuses to drop the last chunk (returns it unchanged) so the caller's
    "keep at least one most-relevant chunk" rule holds and trimming falls through
    to the next component instead of emptying RAG.
    """
    chunks = rag.split(RAG_CHUNK_DELIM)
    if len(chunks) <= 1:
        return rag
    return RAG_CHUNK_DELIM.join(chunks[:-1])


def _trim_memory_oldest(mem: str) -> str:
    """Drop the least-relevant (tail) memory chunk; keep the most-relevant head.

    Symmetric to _trim_rag_oldest: memory recall is query-driven and re-fetched every
    round, so its chunks are dropped before the persistent summary but after RAG.
    Refuses to drop the last chunk, so the caller's "keep >= 1" floor holds.
    """
    chunks = mem.split(MEM_CHUNK_DELIM)
    if len(chunks) <= 1:
        return mem
    return MEM_CHUNK_DELIM.join(chunks[:-1])


def _trim_tool_messages_oldest(payload: list) -> list:
    """Drop the oldest complete tool unit (assistant tool_calls + its results).

    tool_messages alternates assistant(tool_calls) and tool(result) messages. Dropping
    only one side would orphan a message and break the LLM call, so we drop from the
    front up to (but not including) the next assistant-with-tool_calls message.
    """
    end = len(payload)
    for i in range(1, len(payload)):
        m = payload[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            end = i
            break
    return payload[end:]


# Estimated decoration cost of one rendered tool-result list item (its "- " bullet
# plus the newline joining it to the next one). Only used when PLANNING how many
# units to drop -- the plan is always verified against a real measurement, so a
# small error here costs at most a couple of extra fix-up steps.
RESULT_ITEM_OVERHEAD = 3


def _tool_unit_slices(payload: list, payload_kind: str) -> list[list]:
    """Split ``payload`` into the indivisible units the trimmers drop, oldest first.

    For ``payload_kind == "messages"`` a unit is one assistant(tool_calls) message
    plus its result messages -- exactly what ``_trim_tool_messages_oldest`` removes
    per step, so the planner and the fix-up loop always agree on the boundaries.
    For plain result strings every entry is its own unit.
    """
    if payload_kind != "messages":
        return [[r] for r in payload]
    units: list[list] = []
    rest = payload
    while rest:
        nxt = _trim_tool_messages_oldest(rest)
        if len(nxt) >= len(rest):  # no progress -- treat the remainder as one unit
            units.append(list(rest))
            break
        units.append(list(rest[: len(rest) - len(nxt)]))
        rest = nxt
    return units


def fit_assembly_context(
    summary_text: str | None,
    history: list,
    rag_context: str | None,
    memory_context: str | None,
    tool_payload: list,
    query: str,
    payload_kind: str,
    build_messages,
    budget: int | None = None,
    tools: list | None = None,
) -> tuple:
    """Fit an assembly-point context to the token budget.

    This is the ONLY hard ceiling in the pipeline. It runs right before each LLM
    call (tool_decision_node and the final generation) -- after
    build_context_with_summary has already applied semantic (LLM-backed)
    compression to the persistent history. Unlike turn-start compression, this
    runs with the COMPLETE payload in hand: system prefix, RAG chunks, archived
    memory recall, tool records and the query all pass through ``build_messages``,
    so every decision is made against the real token cost.

    ``tools`` are the function-tool definitions sent via the separate ``tools=``
    parameter. They are NOT part of the messages array, yet they share the same
    input budget. Their token cost (see ``count_tools_tokens``) is subtracted
    from ``budget`` up front, so the trimming loop reserves room for them instead
    of blindly filling the window and overflowing once the provider adds the
    tool schemas.

    Trimming is planned in a SINGLE SCAN rather than by re-measuring the whole
    request after every dropped item. Re-measuring was O(n^2): each step rebuilt
    and re-encoded the entire context just to shave off one unit, so a heavily
    overflowing request could block for seconds -- and this function is synchronous,
    running on the async request path, so that blocked the event loop. Instead:

        1. measure the assembled request once           -> total, overflow
        2. price every droppable unit once              -> per-unit token cost
        3. walk the priority order accumulating costs until the overflow is covered
        4. apply the whole plan in one shot and verify with a second measurement

    Costing is per-unit and therefore approximate at the seams (BPE merges across a
    join differ by ~1 token, and a component's heading disappears once it empties
    out), so step 4 is mandatory: if the plan came up short, the original
    step-by-step loop runs as a bounded fix-up on what little remains. The trimming
    ORDER and the floors below are identical to the step-by-step behaviour -- only
    the number of full re-measurements changed (from O(dropped units) to 3).

    PHASE 1 -- "keep >= 1" (preferred). Every step leaves at least one item alive so
    a normal overflow degrades gracefully instead of blanking the context. The order
    DROPS ENHANCEMENTS BEFORE THE FALLBACK: rag and memory are re-fetched every round
    (rag by retrieval, memory by query-driven recall), so they are the cheapest to
    lose; the rolled-up summary is the only source of an unconditional continuous
    timeline and must NOT be trimmed first (especially after L1 was removed).
        1. rag_context    -> drop least-relevant (tail) chunk, keep >= 1 (most relevant)
        2. memory        -> drop least-relevant (tail) chunk, keep >= 1 (most relevant)
        3. summary        -> drop oldest paragraph (compressed context is least costly)
        4. history        -> drop oldest message, keep >= 1 (most recent)
        5. tool_payload   -> drop oldest unit/entry, keep >= 1 (most recent)

    PHASE 2 -- "no floor" (safety net). Phase 1 can still overflow when a SINGLE
    surviving item is itself huge (e.g. one tool result holding a 200k-char scrape,
    one oversized RAG chunk, or one very long history message). Once phase 1 has
    nothing left to give, we re-run the same order and drop the last survivors too:
        6. rag_context  -> drop the final chunk
        7. memory       -> drop the final chunk
        8. history      -> drop the final message
        9. tool_payload -> drop the final unit/entry
    (summary is already empty after phase 1, so it is a no-op here.)

    The result is purely transient: callers assemble THIS submission's messages
    from the returned components and must NOT write anything back to the database
    or mutate state. The query is returned unchanged -- oversized queries are
    rejected at the API entry point (query_exceeds_context_window) and any
    residual overflow is surfaced to the caller as an upstream 400, so this
    function never truncates the user's question.

    Returns ``(summary, history, rag, memory, payload, query, dropped)``.
    """
    if budget is None:
        budget = _budget()
    if budget < 1:
        budget = 1  # guard degenerate tiny-window configs; never loop forever
    # Tool definitions share the input budget but live outside the messages array,
    # so reserve their cost up front. eff_budget is what the messages may use.
    tools_tok = count_tools_tokens(tools)
    # Report the measurement so pre-assembly call sites (turn-start compaction
    # planning, config validation) stop falling back to a constant.
    record_tool_tokens(tools_tok)
    eff_budget = max(1, budget - tools_tok)

    cur_s = summary_text
    cur_h = list(history)
    cur_r = rag_context
    cur_mem = memory_context
    cur_p = list(tool_payload)
    cur_q = query or ""
    dropped = False

    def _tool_units(payload: list) -> int:
        if payload_kind == "messages":
            return sum(
                1 for m in payload if m.get("role") == "assistant" and m.get("tool_calls")
            )
        return len(payload)

    def _unit_cost(unit: list) -> int:
        """Token cost of one tool-payload unit."""
        if payload_kind == "messages":
            # count_messages_tokens adds a flat +3 reply-priming charge to the whole
            # request; only the per-message overhead disappears when a unit is dropped.
            return max(0, count_messages_tokens(unit) - 3)
        return sum(count_text_tokens(str(r)) + RESULT_ITEM_OVERHEAD for r in unit)

    # ---- Stage 1: measure once; the overwhelmingly common case fits as-is ---- #
    total = count_messages_tokens(build_messages(cur_s, cur_h, cur_r, cur_p, cur_q, cur_mem))
    if total <= eff_budget:
        return cur_s, cur_h, cur_r, cur_mem, cur_p, cur_q, dropped

    # ---- Stage 2: price every droppable unit once, then plan in one scan ---- #
    # Order: rag -> memory -> summary -> history -> tool_payload (drop enhancements
    # before the fallback; see the Phases docstring).
    overflow = total - eff_budget
    seg_r = cur_r.split(RAG_CHUNK_DELIM) if cur_r else []
    seg_m = cur_mem.split(MEM_CHUNK_DELIM) if cur_mem else []
    seg_s = cur_s.split("\n") if cur_s else []
    units_p = _tool_unit_slices(cur_p, payload_kind)
    nl_tok = count_text_tokens("\n")
    delim_rag_tok = count_text_tokens(RAG_CHUNK_DELIM)
    delim_mem_tok = count_text_tokens(MEM_CHUNK_DELIM)

    saved = 0
    k_r = k_m = k_s = k_h = k_p = 0  # units to drop per component

    # (1) rag -- least-relevant tail chunk first, keep the most-relevant head
    for i in range(max(0, len(seg_r) - 1)):
        if saved >= overflow:
            break
        saved += count_text_tokens(seg_r[-1 - i]) + delim_rag_tok
        k_r = i + 1
    # (2) memory -- least-relevant tail chunk first, keep the most-relevant head
    for i in range(max(0, len(seg_m) - 1)):
        if saved >= overflow:
            break
        saved += count_text_tokens(seg_m[-1 - i]) + delim_mem_tok
        k_m = i + 1
    # (3) summary -- oldest paragraph first; _trim_summary_oldest lets it drain fully
    for i, seg in enumerate(seg_s):
        if saved >= overflow:
            break
        saved += count_text_tokens(seg) + nl_tok
        k_s = i + 1
    # (4) history -- oldest first, keep the most recent message
    for i in range(max(0, len(cur_h) - 1)):
        if saved >= overflow:
            break
        saved += max(0, count_messages_tokens([cur_h[i]]) - 3)
        k_h = i + 1
    # (5) tool payload -- oldest unit first, keep the most recent
    for i in range(min(max(0, len(units_p) - 1), max(0, _tool_units(cur_p) - 1))):
        if saved >= overflow:
            break
        saved += _unit_cost(units_p[i])
        k_p = i + 1

    # ---- Phase 2 planning: only reached once every floor above is exhausted ---- #
    # (each loop above runs to completion unless it covered the overflow first)
    if saved < overflow and k_r < len(seg_r):
        saved += count_text_tokens(seg_r[0])
        k_r = len(seg_r)
    if saved < overflow and k_m < len(seg_m):
        saved += count_text_tokens(seg_m[0])
        k_m = len(seg_m)
    if saved < overflow and k_s < len(seg_s):
        saved += count_text_tokens(seg_s[0])
        k_s = len(seg_s)
    if saved < overflow and k_h < len(cur_h):
        saved += max(0, count_messages_tokens([cur_h[-1]]) - 3)
        k_h = len(cur_h)
    if saved < overflow and k_p < len(units_p):
        saved += sum(_unit_cost(u) for u in units_p[k_p:])
        k_p = len(units_p)

    # ---- Stage 3: apply the whole plan at once ---- #
    if k_r or k_m or k_s or k_h or k_p:
        if k_r:
            cur_r = "" if k_r >= len(seg_r) else RAG_CHUNK_DELIM.join(seg_r[: len(seg_r) - k_r])
        if k_m:
            cur_mem = "" if k_m >= len(seg_m) else MEM_CHUNK_DELIM.join(seg_m[: len(seg_m) - k_m])
        if k_s:
            cur_s = "" if k_s >= len(seg_s) else "\n".join(seg_s[k_s:])
        if k_h:
            cur_h = cur_h[k_h:]
        if k_p:
            cur_p = [] if k_p >= len(units_p) else [m for u in units_p[k_p:] for m in u]
        dropped = True

    # ---- Stage 4: verify; per-unit pricing is approximate at the seams ---- #
    if count_messages_tokens(build_messages(cur_s, cur_h, cur_r, cur_p, cur_q, cur_mem)) <= eff_budget:
        return cur_s, cur_h, cur_r, cur_mem, cur_p, cur_q, dropped

    # The plan came up short (or the fixed prefix alone overflows). Fall through to
    # the step-by-step trimmer as a bounded fix-up: it walks the same order over
    # whatever survived, so it converges in a handful of steps instead of hundreds.
    while True:
        msgs = build_messages(cur_s, cur_h, cur_r, cur_p, cur_q, cur_mem)
        if count_messages_tokens(msgs) <= eff_budget:
            return cur_s, cur_h, cur_r, cur_mem, cur_p, cur_q, dropped

        # ---- Phase 1: trim while keeping at least one item per component ---- #
        # 1. rag (keep >= 1 most-relevant)
        if cur_r:
            nr = _trim_rag_oldest(cur_r)
            if nr != cur_r:
                cur_r = nr
                dropped = True
                continue
        # 2. memory (keep >= 1 most-relevant)
        if cur_mem:
            nm = _trim_memory_oldest(cur_mem)
            if nm != cur_mem:
                cur_mem = nm
                dropped = True
                continue
        # 3. summary (drains fully within phase 1)
        if cur_s:
            ns = _trim_summary_oldest(cur_s)
            if ns != cur_s:
                cur_s = ns
                dropped = True
                continue
        # 4. history (keep >= 1)
        if len(cur_h) > 1:
            cur_h = cur_h[1:]
            dropped = True
            continue
        # 5. tool_payload (keep >= 1)
        if _tool_units(cur_p) > 1:
            cur_p = _trim_tool_messages_oldest(cur_p) if payload_kind == "messages" else cur_p[1:]
            dropped = True
            continue
        # ---- Phase 2: floors reached, drop the last survivors (same order) ---- #
        # (summary is already empty here, so it needs no phase-2 step)
        # 6. rag -> drop the final chunk
        if cur_r:
            cur_r = ""
            dropped = True
            continue
        # 7. memory -> drop the final chunk
        if cur_mem:
            cur_mem = ""
            dropped = True
            continue
        # 8. history -> drop the final message
        if cur_h:
            cur_h = []
            dropped = True
            continue
        # 9. tool_payload -> drop the final unit/entry
        if cur_p:
            cur_p = []
            dropped = True
            continue
        # Context is empty and the system prefix + query (plus the tool schema, if
        # any) still overflow: the query is returned untrimmed (oversized queries
        # are rejected at the API entry point and residual overflow surfaces as an
        # upstream 400), so nothing left to give -- surface it and let the call proceed.

        # Even a single-token query over an empty context overflows: the fixed
        # system prefix (and tool definitions, if present) alone exceed the window.
        # Nothing left to give -- surface it and let the call proceed (a
        # misconfigured context_window, or too many/too-large tool schemas, not a
        # runtime condition we can trim our way out of).
        logger.warning(
            "fit_assembly_context exhausted: %d tokens still over budget %d with an "
            "empty context and a minimal query (system prefix + %d tool-schema tokens overflow).",
            count_messages_tokens(build_messages(cur_s, cur_h, cur_r, cur_p, cur_q, cur_mem)),
            budget,
            tools_tok,
        )
        return cur_s, cur_h, cur_r, cur_mem, cur_p, cur_q, dropped

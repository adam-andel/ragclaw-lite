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
import uuid
from math import floor
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_client
from app.services.config_manager import (
    config_manager,
    SUMMARY_FIXED_OVERHEAD_TOKENS,
    SUMMARY_SAFETY_MARGIN,
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

# NOTE: SUMMARY_SAFETY_MARGIN and SUMMARY_FIXED_OVERHEAD_TOKENS are imported
# from app.services.config_manager (single source of truth) so that
# ConfigManager.validate_compression_budget() has no circular import.

# Cap a single summarization output so the summary can never dominate the context.
SUMMARY_MAX_TOKENS = 2000

# Single-pass history compression: compute the exact overflow and fold the
# minimum needed, hard-floored at HISTORY_COMPRESS_MIN_FRAC of the un-summarized
# tail. Over-folding is intentional -- L0/L1 + archive recall catch key context,
# so erring toward compression is safe (per project compression policy).
HISTORY_COMPRESS_MIN_FRAC = 0.5
# Reserve absorbed by the summary written back into L0 when a fold happens, so we
# target a bit more than the raw overflow. fraction of overflow + absolute floor.
HISTORY_COMPRESS_RESERVE_FRAC = 0.15
HISTORY_COMPRESS_RESERVE_MIN = 256

# Proportions for query condensation: keep this much of the head/tail verbatim,
# summarize the middle.
QUERY_KEEP_HEAD_FRAC = 0.25
QUERY_KEEP_TAIL_FRAC = 0.35

# Minimum query length (tokens) before Layer 1 will even attempt lossy query
# condensation. Below this, condensing saves almost nothing -- and because the
# middle-segment summary is capped at SUMMARY_MAX_TOKENS, short queries can even
# grow. Such overflow is handled by fit_assembly_context's phase-3 hard
# truncation instead. Tunable.
QUERY_COMPRESS_MIN_TOKENS = 2048

# Phase-3 (assembly point) query hard truncation: fraction of the remaining
# Oversized queries are rejected at the API entry point (query_exceeds_context_window)
# before any heavy processing, and any residual overflow that survives
# fit_assembly_context is surfaced to the caller as an upstream 400 -- the query is
# never silently truncated here.

# Maximum re-compaction iterations in step (2) (each is one LLM call).
MAX_RECOMPACT_ITERS = 3

# Prompts below were consolidated into the backend i18n dict (app/services/i18n):
#   summary_prompt, summary_recompact_prompt, query_condensed_warning,
#   assembly_trim_warning -> resolved via _t(key, prompt_language).

# Chunk delimiter used by agent_nodes._build_context when joining retrieved chunks
# into `rag_context`. Trimming splits on it to drop the least-relevant (tail) chunks
# while keeping the most-relevant (head) ones (fuse() returns chunks best-first).
RAG_CHUNK_DELIM = "\n\n---\n\n"


# --------------------------------------------------------------------------- #
# Token budget helpers
# --------------------------------------------------------------------------- #
def _budget() -> int:
    """Max input tokens allowed: window minus the reserved output allowance."""
    return config_manager.context_window - (
        config_manager.max_tokens + SUMMARY_SAFETY_MARGIN
    )


def query_exceeds_context_window(query: str) -> bool:
    """True when the raw query alone already exhausts the input token budget.

    Used as an early-exit guard at the request entry point so an oversized query
    is rejected before any history compression, RAG, or LLM call runs. A query
    that alone overflows the budget cannot be salvaged by trimming history/summary,
    so the caller should fail fast with a user-facing message instead of burning
    tokens on a low-quality truncated answer.
    """
    if not query:
        return False
    return count_text_tokens(query) > _budget()


def _overhead() -> int:
    """Fixed context overhead estimate, clamped so the cascade can always converge.

    ``SUMMARY_FIXED_OVERHEAD_TOKENS`` conservatively estimates system prompt +
    RAG context + memory + tool definitions -- components this module cannot
    measure directly (only the assembly point sees them). On small context
    windows that estimate can exceed the entire budget, which would make every
    stage look hopeless and skip compression entirely. Clamp it to leave at
    least a sliver of room for real content.
    """
    return min(SUMMARY_FIXED_OVERHEAD_TOKENS, max(0, _budget() - 512))


def _estimate(history: list[dict], summary_text: str, query_tok: int, summary2_text: str = "") -> int:
    """Approximate total input tokens for the request about to be sent.

    ``summary2_text`` is the L1 secondary summary, which is injected alongside
    ``summary_text`` (L0) as ambient context, so its cost must be counted too.
    """
    return (
        count_messages_tokens(history)
        + (count_text_tokens(summary_text) if summary_text else 0)
        + (count_text_tokens(summary2_text) if summary2_text else 0)
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


def _token_round_split(history: list[dict], k: int, frac: float) -> int:
    """Message index (exclusive upper bound) at which to fold so the folded token
    mass is >= ``frac`` of the total history tokens, snapped UP to whole
    conversation rounds.

    A round is one ``user`` message through the message just before the next
    ``user`` message (i.e. a full Q/A exchange). Snapping up guarantees the
    folded portion always covers complete exchanges. The most recent round is
    never folded, so the in-progress exchange always stays verbatim in the tail.
    Falls back to a message-count split when token data is unavailable.
    """
    n = len(history)
    if n - k <= 1:
        return n
    items = [(k + i, _message_tokens(m)) for i, m in enumerate(history[k:])]
    total = sum(t for _, t in items) + 3  # +3 reply-priming constant (see token_count.py)
    if total <= 0:
        return max(k + 1, floor(n * frac))

    # Round boundaries: each round starts at a 'user' message.
    rounds: list[tuple[int, int]] = []  # (start_idx, end_idx_exclusive)
    round_start = None
    for idx, _ in items:
        if history[idx].get("role") == "user":
            if round_start is not None:
                rounds.append((round_start, idx))
            round_start = idx
    if round_start is not None:
        rounds.append((round_start, n))  # last round extends to history end
    if not rounds:
        return max(k + 1, floor(n * frac))

    target = frac * total
    cum = 0
    boundary_end = rounds[0][1]
    for (s, e) in rounds:
        cum += sum(t for (idx, t) in items if s <= idx < e)
        boundary_end = e
        if cum >= target:
            break

    split = boundary_end
    # Guard: keep at least the latest round unfolded (don't fold the live exchange).
    if split >= n:
        split = max(k + 1, rounds[-1][0])
    if split <= k:
        split = k + 1
    if split > n:
        split = n
    return split


def _join_summary(l1: str | None, l0: str | None) -> str:
    """Combine the L1 secondary summary and the L0 rolling window for injection.

    L1 (older, more compressed) is placed before L0 (recent window) so the most
    recent context stays at the tail of the injected summary.
    """
    parts = []
    if l1 and l1.strip():
        parts.append(l1.strip())
    if l0 and l0.strip():
        parts.append(l0.strip())
    return "\n".join(parts)


async def maybe_archive_and_compact(conv, db: AsyncSession, prompt_language: str):
    """Three-tier memory maintenance, run after Stage (1) has folded history.

    - L1 (secondary summary, read-only): when its share of the context window
      exceeds ``summary_archive_low_pct``, re-compact it in place (overwrite).
    - L0 (rolling window, editable): when its share exceeds
      ``summary_archive_high_pct``, move every fold paragraph EXCEPT the most
      recent one into long-term memory: append a fresh secondary summary to L1
      and archive the raw folds as MemoryChunks. L0 is left holding only the
      most recent fold paragraph.

    The secondary summary (LLM) and the archive's retrieval-critical half (DB
    write + BM25 build) are independent, so they run CONCURRENTLY via
    ``asyncio.gather``. The archive half is awaited -- this whole function runs
    inside ``build_context_with_summary``, which the chat router awaits BEFORE
    starting the agent graph, so the just-archived folds are already indexed
    when the retrieval node runs later in the same turn. Only embedding stays
    fire-and-forget (it is the slow part); hybrid retrieval degrades to
    BM25-only for this turn and becomes full hybrid from the next one.

    Degenerate case: a single L0 fold paragraph that still exceeds HIGH% cannot be
    split into "older" parts, so it is re-compacted in place (the old Stage-2
    semantics) to preserve the "L0 = recent window" invariant.

    Never raises: all LLM/DB failures are swallowed so this never blocks the turn.
    """
    window = config_manager.context_window or 128000
    high = config_manager.summary_archive_high_pct / 100.0
    low = config_manager.summary_archive_low_pct / 100.0

    # ── L1 re-compaction (lowest priority, keep L1 bounded) ──
    l1 = getattr(conv, "summary2_text", None) or ""
    if l1 and (count_text_tokens(l1) / window) >= low:
        compacted = await _summarize_text(l1, prompt_language, recompact=True)
        if compacted and len(compacted) < len(l1):
            conv.summary2_text = compacted
            await db.commit()
            l1 = compacted

    # ── L0 archive (rolling window) ──
    l0 = conv.summary_text or ""
    if not l0:
        return
    if (count_text_tokens(l0) / window) < high:
        return

    segs = l0.split("\n")
    if len(segs) <= 1:
        # Degenerate: only one fold paragraph and still over HIGH% -> re-compact it
        # in place (preserves the "L0 = recent window" invariant).
        compacted = await _summarize_text(l0, prompt_language, recompact=True)
        if compacted and len(compacted) < len(l0):
            conv.summary_text = compacted
            await db.commit()
        return

    recent = segs[-1]            # rolling window: keep the most recent fold
    older_segs = segs[:-1]       # everything older -> archive + secondary summary
    conv_id = getattr(conv, "id", "") or ""

    older_text = "\n".join(older_segs)
    chunk_dicts = [
        {
            "id": str(uuid.uuid4()),
            "content": s,
            "chunk_index": i,
            "doc_id": f"mem_{conv_id}",
            "heading": "",
            "page": 0,
            "token_count": count_text_tokens(s),
        }
        for i, s in enumerate(older_segs)
    ]

    # Close this session's transaction before the archive writes. The archive
    # runs on its OWN session/connection; holding an open (read) transaction here
    # would make SQLite block its COMMIT on our shared lock until the busy
    # timeout expires. Nothing of ours is dirty at this point, so this is cheap.
    await db.commit()

    # ① secondary summary (LLM call) and ② the retrieval-critical half of the
    # archive (persist rows + mark_has_memory + build BM25) are independent, so
    # run them concurrently. Neither can raise -- _summarize_text returns "" and
    # archive_memory_essential returns False on failure -- so gather is safe.
    new_abstract, archived = await asyncio.gather(
        _summarize_text(older_text, prompt_language, recompact=True),
        archive_memory_essential(conv_id, chunk_dicts),
    )

    if not archived:
        # Archival failed (DB / index error). Keep the raw folds inline in L0 and
        # do NOT append the abstract to L1: that is lossless, avoids duplicating
        # the same content across both tiers, and the next turn simply retries.
        # fit_assembly_context still guarantees the window is respected meanwhile.
        logger.warning(
            "Memory archive failed for conv=%s; keeping L0 folds inline", conv_id
        )
        return

    if new_abstract:
        conv.summary2_text = f"{l1}\n{new_abstract}".strip() if l1 else new_abstract
    conv.summary_text = recent
    conv.summary_archived_count = (getattr(conv, "summary_archived_count", 0) or 0) + len(older_segs)
    await db.commit()

    # ③ Expensive half: embedding stays in the background so it is never charged
    # to time-to-first-token. This turn recalls the new chunks via BM25; vectors
    # join in from the next turn on.
    schedule_memory_embedding(conv_id, chunk_dicts)


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
        return [], _join_summary(getattr(conv, "summary2_text", None), conv.summary_text or ""), None, warning

    # Cursor: how many of the earliest messages are already summarized.
    k = getattr(conv, "summary_msg_count", 0) or 0
    if k > n:
        k = n  # safety: never exceed history length

    # L0 = rolling window (recent folds, editable); L1 = secondary summary
    # (older, read-only, re-compacted). Both are injected as ambient context,
    # so L1 cost is counted in every estimate.
    l0 = conv.summary_text or ""
    l1 = getattr(conv, "summary2_text", None) or ""
    q_tok = count_text_tokens(query)

    # (0) Fits -> zero compression. Maximize window utilization.
    if _estimate(history[k:], l0, q_tok, l1) <= _budget():
        return history[k:], _join_summary(l1, l0), None, warning

    # `recent` = verbatim tail not yet folded into the summary. Stage 1 shrinks
    # it as it folds older turns into `base`; if it never fits, `recent` carries
    # the un-folded history out to the assembly point, where fit_assembly_context
    # trims it against the real payload (so the overflow is always representable,
    # even when summarization fails).
    recent = history[k:]
    base = l0  # Stage (1) folds into L0 only; L1 is kept separate.

    # (1) Single-pass adaptive history compression.
    # Every message carries its token mass (content_token_count, written at insert
    # time), so we compute the EXACT overflow and fold the minimum needed to land
    # under budget -- no trial-and-error loop. Over-folding is deliberate: L0/L1
    # plus archive recall catch the key context, so erring toward compression is
    # safe. The fold is hard-floored at HISTORY_COMPRESS_MIN_FRAC of the
    # un-summarized tail, and always snaps to whole conversation rounds.
    recent = history[k:]
    overflow = _estimate(history[k:], l0, q_tok, l1) - _budget()
    if overflow > 0:
        # Reserve absorbs the summary written back into L0 (the fold removes N
        # tokens from history but adds a smaller summary to L0), so we target a
        # little more than the raw overflow to guarantee we cross the line.
        reserve = max(
            int(overflow * HISTORY_COMPRESS_RESERVE_FRAC),
            HISTORY_COMPRESS_RESERVE_MIN,
        )
        tail_tok = count_messages_tokens(history[k:])
        target_frac = (overflow + reserve) / tail_tok if tail_tok > 0 else 1.0
        frac = min(1.0, max(target_frac, HISTORY_COMPRESS_MIN_FRAC))
        split = _token_round_split(history, k, frac)
        if split > k:
            split = min(split, n)
            new_para = await _summarize_text(
                _transcript(history[k:split]), prompt_language
            )
            if new_para:
                candidate = f"{base}\n{new_para}".strip() if base else new_para
                conv.summary_text = candidate
                conv.summary_msg_count = split
                await db.commit()
                recent = history[split:]
            # summarize failed -> leave recent = history[k:], fall through to L0/L1

    # (2) Three-tier memory: L1 re-compaction + L0 archive. Runs EVERY turn now
    # (it self-guards on its own HIGH/LOW thresholds, so it is a no-op when L0/L1
    # are small). This replaces the old early-return-on-fit path: even a turn
    # whose fold already fits still gets its L0/L1 tiers maintained.
    await maybe_archive_and_compact(conv, db, prompt_language)
    l0 = conv.summary_text or ""
    l1 = getattr(conv, "summary2_text", None) or ""
    base = _join_summary(l1, l0)

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
    await db.commit()

    # Feed the folded result through the same three-tier memory maintenance as the
    # automatic path, so manual compaction also archives older folds and re-compacts
    # L1 when thresholds are crossed.
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


def fit_assembly_context(
    summary_text: str | None,
    history: list,
    rag_context: str | None,
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

    Trimming runs in two phases.

    PHASE 1 -- "keep >= 1" (preferred). Every step leaves at least one item alive so
    a normal overflow degrades gracefully instead of blanking the context:
        1. summary        -> drop oldest paragraph (compressed context is least costly)
        2. history        -> drop oldest message, keep >= 1 (most recent)
        3. rag_context    -> drop least-relevant (tail) chunk, keep >= 1 (most relevant)
        4. tool_payload   -> drop oldest unit/entry, keep >= 1 (most recent)

    PHASE 2 -- "no floor" (safety net). Phase 1 can still overflow when a SINGLE
    surviving item is itself huge (e.g. one tool result holding a 200k-char scrape,
    one oversized RAG chunk, or one very long history message). Once phase 1 has
    nothing left to give, we re-run the same order and drop the last survivors too:
        5. history      -> drop the final message
        6. rag_context  -> drop the final chunk
        7. tool_payload -> drop the final unit/entry
    (summary is already empty after phase 1, so it is a no-op here.)

    The result is purely transient: callers assemble THIS submission's messages
    from the returned components and must NOT write anything back to the database
    or mutate state. The query is returned unchanged -- oversized queries are
    rejected at the API entry point (query_exceeds_context_window) and any
    residual overflow is surfaced to the caller as an upstream 400, so this
    function never truncates the user's question.

    Returns ``(summary, history, rag, payload, query, dropped)``.
    """
    if budget is None:
        budget = _budget()
    if budget < 1:
        budget = 1  # guard degenerate tiny-window configs; never loop forever
    # Tool definitions share the input budget but live outside the messages array,
    # so reserve their cost up front. eff_budget is what the messages may use.
    tools_tok = count_tools_tokens(tools)
    eff_budget = max(1, budget - tools_tok)

    cur_s = summary_text
    cur_h = list(history)
    cur_r = rag_context
    cur_p = list(tool_payload)
    cur_q = query or ""
    dropped = False

    def _tool_units(payload: list) -> int:
        if payload_kind == "messages":
            return sum(
                1 for m in payload if m.get("role") == "assistant" and m.get("tool_calls")
            )
        return len(payload)

    while True:
        msgs = build_messages(cur_s, cur_h, cur_r, cur_p, cur_q)
        if count_messages_tokens(msgs) <= eff_budget:
            return cur_s, cur_h, cur_r, cur_p, cur_q, dropped

        # ---- Phase 1: trim while keeping at least one item per component ---- #
        # 1. summary
        if cur_s:
            ns = _trim_summary_oldest(cur_s)
            if ns != cur_s:
                cur_s = ns
                dropped = True
                continue
        # 2. history (keep >= 1)
        if len(cur_h) > 1:
            cur_h = cur_h[1:]
            dropped = True
            continue
        # 3. rag (keep >= 1 most-relevant)
        if cur_r:
            nr = _trim_rag_oldest(cur_r)
            if nr != cur_r:
                cur_r = nr
                dropped = True
                continue
        # 4. tool_payload (keep >= 1)
        if _tool_units(cur_p) > 1:
            cur_p = _trim_tool_messages_oldest(cur_p) if payload_kind == "messages" else cur_p[1:]
            dropped = True
            continue
        # ---- Phase 2: floors reached, drop the last survivors (same order) ---- #
        # (summary is already empty here, so it needs no phase-2 step)
        # 6. history -> drop the final message
        if cur_h:
            cur_h = []
            dropped = True
            continue
        # 7. rag -> drop the final chunk
        if cur_r:
            cur_r = ""
            dropped = True
            continue
        # 8. tool_payload -> drop the final unit/entry
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
            count_messages_tokens(build_messages(cur_s, cur_h, cur_r, cur_p, cur_q)),
            budget,
            tools_tok,
        )
        return cur_s, cur_h, cur_r, cur_p, cur_q, dropped

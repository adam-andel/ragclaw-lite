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
       Short queries skip this and fall through to Layer 2.
- Layer 2 (in-module hard ceiling + graceful degradation) runs when stages 1-3
  still overflow. Order B (drop oldest summary) -> A (drop oldest recent) ->
  C (hard-truncate the query, keep head / drop tail). It is purely transient:
  nothing is written back to the Conversation row, so raw history and the
  persisted summary survive for the next turn. Guarantees the LLM call never
  exceeds the window (no 400, no silent frontend truncation).

The summary is injected as an independent ``system`` message that sits AFTER the
fixed cached system prefix and BEFORE the verbatim history, so it does not break
provider-side prompt caching of the stable system prefix.
"""

from __future__ import annotations

import logging
from math import floor
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_client
from app.services.config_manager import config_manager
from app.services.token_count import (
    _get_encoder,
    count_messages_tokens,
    count_text_tokens,
)

logger = logging.getLogger("ragclaw.summary")

# Reserved output budget: mirrors the safety margin used by
# agent_nodes._compute_agent_max_tokens so the trigger point is consistent with
# how the model's output allowance is computed elsewhere.
SUMMARY_SAFETY_MARGIN = 256

# Estimate of fixed context overhead NOT part of `history`: system prompt,
# file/cron rules, RAG context, memory, tool definitions, and the query. Tunable;
# set conservatively so we trigger compression BEFORE overflow rather than after.
SUMMARY_FIXED_OVERHEAD_TOKENS = 16000

# Cap a single summarization output so the summary can never dominate the context.
SUMMARY_MAX_TOKENS = 2000

# Single-pass history compression unit (oldest fraction tried first).
HISTORY_COMPRESS_UNIT = 2 / 3

# Proportions for query condensation: keep this much of the head/tail verbatim,
# summarize the middle.
QUERY_KEEP_HEAD_FRAC = 0.25
QUERY_KEEP_TAIL_FRAC = 0.35

# Minimum query length (tokens) before Layer 1 will even attempt lossy query
# condensation. Below this, condensing saves almost nothing -- and because the
# middle-segment summary is capped at SUMMARY_MAX_TOKENS, short queries can even
# grow. Such overflow is handled by Layer 2 (hard truncation) instead. Tunable.
QUERY_COMPRESS_MIN_TOKENS = 2048

# Maximum re-compaction iterations in step (2) (each is one LLM call).
MAX_RECOMPACT_ITERS = 3

SUMMARY_PROMPT_ZH = (
    "你是一个对话压缩器。请将下面的对话记录压缩为一段连贯的中文摘要，"
    "保留：关键事实、用户偏好、已做出的决策、未解决的问题、重要结论与待办。"
    "不要逐字复述，不要遗漏关键上下文。输出纯文本摘要，不要使用 markdown 代码块。"
)
SUMMARY_PROMPT_EN = (
    "You are a conversation compressor. Compress the following dialogue into a "
    "coherent English summary, preserving: key facts, user preferences, decisions "
    "made, open questions, and important conclusions or follow-ups. Do not quote "
    "verbatim; do not drop critical context. Output plain-text summary only, no "
    "markdown code fences."
)

SUMMARY_RECOMPACT_PROMPT_ZH = (
    "你是一个对话摘要压缩器。下面是一段已有的对话摘要，请将其进一步压缩为更短的摘要，"
    "保留所有关键事实、用户偏好、决策、未解决问题与重要结论，删除冗余表述。"
    "输出纯文本，不要 markdown 代码块。"
)
SUMMARY_RECOMPACT_PROMPT_EN = (
    "You are a summary compressor. The text below is an existing conversation "
    "summary. Compress it further into a shorter summary, preserving all key "
    "facts, user preferences, decisions, open questions, and important conclusions; "
    "drop redundant wording. Plain text, no markdown code fences."
)

QUERY_CONDENSED_WARNING_ZH = (
    "您的消息过长，已自动压缩以适配上下文窗口（首尾原文保留，中间部分被摘要）。"
)
QUERY_CONDENSED_WARNING_EN = (
    "Your message was too long and has been condensed to fit the context window "
    "(head and tail kept verbatim, middle summarized)."
)

LAYER2_DROP_WARNING_ZH = (
    "对话内容超出模型上下文窗口，已自动丢弃最早的摘要与对话片段以保证请求成功发送。"
)
LAYER2_DROP_WARNING_EN = (
    "The conversation exceeded the model context window; the oldest summary and "
    "dialogue segments were automatically dropped so the request could be sent."
)

# Warning surfaced when the per-submission assembly-point trimmer (fit_assembly_context)
# had to drop older context so the request would fit the window.
ASSEMBLY_TRIM_WARNING_ZH = (
    "部分较早的上下文（摘要 / 对话记录 / 参考文档 / 工具记录）因超出上下文窗口已被自动裁剪，"
    "以确保本次回答能正常生成。"
)
ASSEMBLY_TRIM_WARNING_EN = (
    "Some earlier context (summary / conversation history / reference documents / "
    "tool records) was automatically trimmed to fit the context window so this "
    "response could be generated."
)

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


def _overhead() -> int:
    """Fixed context overhead estimate, clamped so Layer 2 can always converge.

    ``SUMMARY_FIXED_OVERHEAD_TOKENS`` conservatively estimates system prompt +
    RAG context + memory + tool definitions. On small context windows that
    estimate can exceed the entire budget, which would make Layer 2's trimming
    loop unable to ever fit. Clamp it to leave at least a sliver of room for
    real content.
    """
    return min(SUMMARY_FIXED_OVERHEAD_TOKENS, max(0, _budget() - 512))


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
    if recompact:
        prompt = (
            SUMMARY_RECOMPACT_PROMPT_EN
            if prompt_language == "en"
            else SUMMARY_RECOMPACT_PROMPT_ZH
        )
    else:
        prompt = (
            SUMMARY_PROMPT_EN if prompt_language == "en" else SUMMARY_PROMPT_ZH
        )
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
    - ``final_query``: the query to actually send. Usually the original; may be the
      Layer-1 condensed form, or (in Layer 2) hard-truncated to fit the window.
      The caller should use this instead of the raw query.
    - ``warning``: non-empty when the query was lossily condensed (Layer 1) or older
      content was auto-dropped to satisfy the window (Layer 2); the caller should
      surface it to the user.

    Raw ``Message`` rows are never modified. The summary is persisted on the
    ``Conversation`` row via the cursor ``summary_msg_count``. Layer 2 trims are
    transient only -- they are NOT written back.
    """
    warning = ""
    n = len(history)
    if n == 0:
        return [], conv.summary_text or "", None, warning

    # Cursor: how many of the earliest messages are already summarized.
    k = getattr(conv, "summary_msg_count", 0) or 0
    if k > n:
        k = n  # safety: never exceed history length

    base = conv.summary_text or ""
    q_tok = count_text_tokens(query)

    # (0) Fits -> zero compression. Maximize window utilization.
    if _estimate(history[k:], base, q_tok) <= _budget():
        return history[k:], base, None, warning

    # `recent` = verbatim tail not yet folded into the summary. Stage 1 shrinks
    # it as it folds older turns into `base`; if it never fits, `recent` carries
    # the un-folded history into Layer 2 for trimming (so the overflow is always
    # representable, even when summarization fails).
    recent = history[k:]

    # (1) Adaptive history compression: oldest 2/3, expand to all if insufficient.
    frac = HISTORY_COMPRESS_UNIT
    while True:
        split = max(k + 1, floor(n * frac))
        if split > n:
            split = n
        if split <= k:
            break  # nothing left to compress in history
        new_para = await _summarize_text(
            _transcript(history[k:split]), prompt_language
        )
        if not new_para:
            break  # summarize failed; bail out (falls through to re-compaction)
        candidate = f"{base}\n{new_para}".strip() if base else new_para
        if _estimate(history[split:], candidate, q_tok) <= _budget():
            conv.summary_text = candidate
            conv.summary_msg_count = split
            await db.commit()
            return history[split:], candidate, None, warning
        # Did not fit yet: record the un-folded tail for Layer 2, and (on the
        # final frac=1.0 pass) persist the single full fold of history[k:n] so the
        # next turn does not re-summarize. Each pass summarizes history[k:split]
        # against the ORIGINAL base (no accumulation), avoiding duplicated folds.
        recent = history[split:]
        if frac >= 1.0:
            conv.summary_text = candidate
            conv.summary_msg_count = split
            await db.commit()
            break
        frac = min(1.0, frac + 1 / 3)

    # (2) History fully compressed but still over -> re-compact the summary itself.
    base = conv.summary_text or ""
    iters = 0
    while (
        _estimate([], base, q_tok) > _budget()
        and len(base) > 500
        and iters < MAX_RECOMPACT_ITERS
    ):
        compacted = await _summarize_text(base, prompt_language, recompact=True)
        if compacted and len(compacted) < len(base):
            base = compacted
            conv.summary_text = base
            await db.commit()
            iters += 1
        else:
            break  # did not shrink -> stop to avoid pointless LLM calls

    # (3) Still over AND the query itself is long enough that condensing it yields
    # meaningful savings. Below QUERY_COMPRESS_MIN_TOKENS the query is too small to
    # be worth lossy condensation, so we skip it and let Layer 2 trim the older
    # history/summary instead.
    if _estimate([], base, q_tok) > _budget() and q_tok >= QUERY_COMPRESS_MIN_TOKENS:
        condensed = await _condense_query(query, base, prompt_language)
        if condensed and count_text_tokens(condensed) < q_tok:
            warning = (
                QUERY_CONDENSED_WARNING_EN
                if prompt_language == "en"
                else QUERY_CONDENSED_WARNING_ZH
            )
            return [], base, condensed, warning

    # ── Layer 2: hard ceiling + graceful degradation ──
    # Order B (oldest summary) -> A (oldest recent) -> C (hard-truncate query,
    # keep head / drop tail). Purely transient: nothing is written back to conv,
    # so raw Message rows and the persisted summary survive for the next turn.
    if _estimate(recent, base, q_tok) > _budget():
        budget = _budget()
        dropped = False
        # Phase B: drop oldest summary content from the front (oldest first).
        # Paragraph granularity when possible; token-level fallback for a single
        # oversized paragraph guarantees the loop always converges.
        segs = base.split("\n") if base else []
        enc = _get_encoder()
        while _estimate(recent, "\n".join(segs), q_tok) > budget:
            if len(segs) > 1:
                segs = segs[1:]
            elif segs:
                ids = enc.encode(segs[0])
                # Drop tokens from the FRONT (oldest) of this paragraph. Guarantee
                # at least one token is removed each pass so the loop always
                # converges (a fixed slice like ids[0:] would loop forever on a
                # ~9-token paragraph).
                drop = max(1, len(ids) // 10)
                if len(ids) <= drop:
                    break
                segs = [enc.decode(ids[drop:])]
            else:
                break
        if "\n".join(segs) != base:
            dropped = True
        base = "\n".join(segs)
        # Phase A: trim oldest recent message from the front.
        # (recent is usually empty here -- stage 1 folds the whole history into
        #  the summary before we arrive -- so this is typically a no-op.)
        while _estimate(recent, base, q_tok) > budget and recent:
            recent = recent[1:]
            dropped = True
        # Phase C: last resort -- hard-truncate the query, keep the HEAD (the
        # user's instruction) and drop the TAIL. Lossy; warn the user.
        while _estimate(recent, base, q_tok) > budget and q_tok > 0:
            ids = enc.encode(query)
            if len(ids) <= 1:
                break
            keep = max(1, len(ids) - max(1, len(ids) // 4))
            query = enc.decode(ids[:keep])
            q_tok = count_text_tokens(query)
            dropped = True
        if dropped:
            warning = (
                LAYER2_DROP_WARNING_EN
                if prompt_language == "en"
                else LAYER2_DROP_WARNING_ZH
            )

    return recent, base, query, warning


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
    memory_context: str | None,
    query: str,
    payload_kind: str,
    build_messages,
    budget: int | None = None,
) -> tuple:
    """Fit an assembly-point context to the token budget WITHOUT touching the query.

    This is the per-submission hard ceiling. It runs right before each LLM call
    (tool_decision_node and the final generation) -- after build_context_with_summary
    has already compressed the persistent history into ``summary_text``. It handles the
    overflow that build_context_with_summary cannot see: the in-turn accumulation of
    tool_messages / tool_results.

    Trimming order (each step always keeps at least one item, so we never delete
    everything but the query):
        1. summary        -> drop oldest paragraph (compressed context is least costly)
        2. history        -> drop oldest message, keep >= 1 (most recent)
        3. rag_context    -> drop least-relevant (tail) chunk, keep >= 1 (most relevant)
        4. tool_payload   -> drop oldest unit/entry, keep >= 1 (most recent)
        5. memory_context -> LAST RESORT: drop entirely (user profile)

    The query is never modified (it was already handled by build_context_with_summary,
    and re-trimming it could break task execution). The result is purely transient:
    callers assemble THIS submission's messages from the trimmed components and must
    NOT write anything back to the database or mutate state.

    Returns ``(summary, history, rag, payload, memory, dropped)``.
    """
    if budget is None:
        budget = _budget()
    if budget < 1:
        budget = 1  # guard degenerate tiny-window configs; never loop forever

    cur_s = summary_text
    cur_h = list(history)
    cur_r = rag_context
    cur_p = list(tool_payload)
    cur_m = memory_context
    dropped = False

    while True:
        msgs = build_messages(cur_s, cur_h, cur_r, cur_p, cur_m)
        if count_messages_tokens(msgs) <= budget:
            return cur_s, cur_h, cur_r, cur_p, cur_m, dropped

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
        if payload_kind == "messages":
            units = sum(
                1 for m in cur_p if m.get("role") == "assistant" and m.get("tool_calls")
            )
            if units > 1:
                cur_p = _trim_tool_messages_oldest(cur_p)
                dropped = True
                continue
        else:  # "results"
            if len(cur_p) > 1:
                cur_p = cur_p[1:]
                dropped = True
                continue
        # 5. memory (last resort)
        if cur_m:
            cur_m = ""
            dropped = True
            continue
        # nothing left to trim
        return cur_s, cur_h, cur_r, cur_p, cur_m, dropped

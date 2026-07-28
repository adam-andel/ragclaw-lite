"""Conversation-history compression.

Goal: keep the LLM context within the model's window by summarizing the oldest
part of a long conversation instead of dropping it.

Strategy
--------
- We never truncate history silently. The full conversation is always loaded
  from the DB; raw ``Message`` rows are NEVER modified.
- Before the turn, we estimate whether the full history (plus a fixed overhead
  for system prompt / tools / RAG / query) would overflow the context window.
- If it would overflow, we compress the **oldest two-thirds** of the history
  into the conversation's *accumulated* summary (persisted on the ``Conversation``
  row) and return only the most recent one-third verbatim plus the summary.
- A cursor ``summary_msg_count`` tracks how many of the earliest messages are
  already summarized, so we never send them verbatim again (no duplication) and
  we only summarize newly-eligible slices on later turns (no repeated work).

The summary is injected as an independent ``system`` message that sits AFTER the
fixed cached system prefix and BEFORE the verbatim history, so it does not break
provider-side prompt caching of the stable system prefix.

Known limitation: the accumulated summary itself is not recursively compacted.
For pathological ultra-long conversations it could eventually grow large; a
future enhancement can summarize the summary when it, too, approaches the window.
"""

from __future__ import annotations

import logging
from math import floor
from typing import Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_client
from app.services.config_manager import config_manager
from app.services.token_count import count_messages_tokens, count_text_tokens

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


def _would_overflow(history: list[dict], query: str) -> bool:
    """True if history + fixed overhead would exceed the usable window."""
    window = config_manager.context_window
    reserved = config_manager.max_tokens + SUMMARY_SAFETY_MARGIN
    estimated = (
        count_messages_tokens(history)
        + count_text_tokens(query)
        + SUMMARY_FIXED_OVERHEAD_TOKENS
    )
    return estimated > window - reserved


async def build_context_with_summary(
    conv,
    history: list[dict],
    db: AsyncSession,
    prompt_language: str,
    query: str = "",
) -> Tuple[list[dict], str]:
    """Return ``(recent_messages, summary_text)`` for the LLM context.

    - If the full history fits, return it verbatim with the existing summary
      (any previously-compressed turns are still reflected via the summary, and
      the already-summarized prefix is excluded from the verbatim part).
    - If it would overflow, compress the oldest two-thirds into the accumulated
      summary and return the most recent one-third verbatim plus the summary.
    """
    n = len(history)
    if n == 0:
        return [], conv.summary_text or ""

    # Cursor: how many of the earliest messages are already summarized.
    k = getattr(conv, "summary_msg_count", 0) or 0
    if k > n:
        k = n  # safety: never exceed history length

    if _would_overflow(history, query):
        split = max(1, floor(n * 2 / 3))  # oldest two-thirds boundary (>=1)
        if split > k:
            # Summarize only the newly-eligible slice [k, split); accumulate.
            to_summarize = history[k:split]
            try:
                prompt = SUMMARY_PROMPT_EN if prompt_language == "en" else SUMMARY_PROMPT_ZH
                transcript = "\n".join(
                    f"{m.get('role', '')}: {m.get('content', '')}" for m in to_summarize
                )
                new_para = (
                    await llm_client.chat(
                        messages=[
                            {"role": "user", "content": f"{prompt}\n\n{transcript}"}
                        ],
                        temperature=0,
                        max_tokens=SUMMARY_MAX_TOKENS,
                        conversation_id=None,  # meta-call: must NOT be cached per-conversation
                    )
                ).strip()
                if new_para:
                    base = conv.summary_text or ""
                    conv.summary_text = (
                        f"{base}\n{new_para}".strip() if base else new_para
                    )
                    conv.summary_msg_count = split
                    await db.commit()
                    k = split
            except Exception as e:  # never block the main turn on a summary failure
                logger.warning(
                    "Conversation summary failed (using verbatim history): %s", e
                )
        return history[split:], conv.summary_text or ""

    # Not overflowing: return the yet-unsummarized recent tail plus the summary.
    return history[k:], conv.summary_text or ""

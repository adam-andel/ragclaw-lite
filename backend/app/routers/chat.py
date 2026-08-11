"""Chat API routes with SSE streaming."""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func, text, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
import app.database as db_mod
from app.models.user import User
from app.models.conversation import Conversation, Message, PendingLimitState, AgentStep
from app.models.document import Document, Chunk
from app.services.auth import get_current_user
from app.services.cache import answer_cache
from app.services.repl_auth import get_user_repl_uid
from app.services.agent_nodes import (
    _strip_tool_call_noise,
    _normalize_download_url,
    _build_working_dir_prompt,
    _get_skill_index,
)
from app.services.skill_manager import read_skill_md, parse_skill_md
from app.services.kb_service import get_kb_prompt
from app.services.token_count import count_messages_tokens, count_text_tokens
from app.services.config_manager import config_manager
from app.services.context_budget import check_field_budget
from app.services.conversation_summary import (
    build_context_with_summary,
    compact_conversation,
    CompactionError,
    ContextWindowExceeded,
    classify_entry_overflow,
    SUMMARY_SEGMENT_DELIM,
    segment_thresholds,
    _tail_from,
)
from app.services.llm_semaphore import llm_limiter
from app.services import memory_archive
from app.services import conversation_purge
from app.services.i18n import t as _t

from app.schemas.chat import (
    ChatRequest,
    CompactRequest,
    ConversationResponse,
    ConversationDetail,
    ConversationMessagesPage,
    ConversationSummaryState,
    PendingLimitResponse,
    SummaryUpdateRequest,
    SummarySegmentDeleteRequest,
    PinInstructionRequest,
    PIN_INSTRUCTION_MAX_CHARS,
)

router = APIRouter(prefix="/api", tags=["Chat"])

# --------------------------------------------------------------------------- #
# Per-conversation message cache.
#
# A conversation's raw messages NEVER change in place -- they only GROW by
# append (new user/assistant rows). So once we have loaded a conversation's
# history we can keep it in memory and serve every subsequent chat turn from the
# cache, refreshing ONLY the appended tail (seq > cached max_seq). This removes
# the per-turn full `select(Message).all()` that used to run on every request.
#
# The cache is keyed by conversation_id with a generous TTL: it lives until the
# conversation is opened elsewhere, the TTL lapses, or the conversation is
# deleted. Stored entries are seq-ordered dicts (the same shape build_context_*
# expects), so `_tail_from` can slice by seq value regardless of where the tail
# starts.
# --------------------------------------------------------------------------- #
_HISTORY_CACHE_TTL = 600.0  # seconds -- long enough to cover a whole session
_HISTORY_CACHE: dict[str, dict] = {}        # conv_id -> {"msgs": [...], "max_seq": int|None, "ts": float}
_HISTORY_CACHE_LOCKS: dict[str, asyncio.Lock] = {}  # conv_id -> per-conversation refresh lock


def _msg_to_dict(m) -> dict:
    return {
        "role": m.role,
        "content": m.content,
        "content_token_count": m.content_token_count,
        "seq": m.seq,
    }


async def _load_history(conv_id: str, db: AsyncSession, cursor: int) -> list[dict]:
    """Return the un-summarized tail (seq >= cursor) of a conversation's messages,
    served from the per-conversation cache when warm.

    On a warm hit we only query ``seq > cached_max_seq`` (the appended tail since
    the last load); on a cold miss we query ``seq >= cursor`` once. Either way the
    returned list is the same seq-ordered dict shape the rest of the request uses.
    """
    now = time.monotonic()
    lock = _HISTORY_CACHE_LOCKS.get(conv_id)
    if lock is None:
        lock = _HISTORY_CACHE_LOCKS.setdefault(conv_id, asyncio.Lock())

    async with lock:
        entry = _HISTORY_CACHE.get(conv_id)
        if entry is not None and (now - entry["ts"]) < _HISTORY_CACHE_TTL:
            # Warm: refresh only the appended messages.
            if entry["max_seq"] is not None:
                new_rows = (
                    await db.execute(
                        select(Message)
                        .where(Message.conversation_id == conv_id, Message.seq > entry["max_seq"])
                        .order_by(Message.seq.asc())
                    )
                ).scalars().all()
                if new_rows:
                    entry["msgs"].extend(_msg_to_dict(m) for m in new_rows)
                    entry["max_seq"] = entry["msgs"][-1]["seq"]
            entry["ts"] = now
            return entry["msgs"]

        # Cold: load only the un-summarized tail.
        rows = (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conv_id, Message.seq >= cursor)
                .order_by(Message.seq.asc())
            )
        ).scalars().all()
        msgs = [_msg_to_dict(m) for m in rows]
        max_seq = msgs[-1]["seq"] if msgs else None
        _HISTORY_CACHE[conv_id] = {"msgs": msgs, "max_seq": max_seq, "ts": now}
        return msgs


def _evict_history_cache(conv_id: str) -> None:
    """Drop any cached history for a conversation (e.g. on delete)."""
    _HISTORY_CACHE.pop(conv_id, None)
    _HISTORY_CACHE_LOCKS.pop(conv_id, None)


logger = logging.getLogger("ragclaw.chat")


def _sse(event_type: str, payload: dict) -> str:
    """Format a single SSE data line."""
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


# The model sometimes echoes a raw `[TOOL_CALL] ... [/TOOL_CALL]` block into its
# final answer (it "restates" the tool call it just made). That text must never
# reach the user — the pipeline has already executed the real tool call via the
# proper tool-decision path. We intercept it here: anything inside a
# [TOOL_CALL] span is withheld from the live stream and stripped from the saved
# answer, so the call is effectively "consumed" by the pipeline, not dumped raw.
_TOOL_CALL_SPAN_RE = re.compile(r'\[TOOL_CALL\].*?\[/TOOL_CALL\]', re.DOTALL | re.IGNORECASE)


def _suppress_tool_call_span(buffer: str):
    """Given an accumulating stream buffer, return the text that is safe to emit
    (everything outside any `[TOOL_CALL] ... [/TOOL_CALL]` block) while holding
    back any trailing *unclosed* `[TOOL_CALL]` span — including a partial open
    tag that may be split across stream chunks — until its closing tag (or the
    end of the stream) arrives.

    Returns ``(text_to_emit, remaining_buffer_to_hold)``.
    """
    out = ""
    pos = 0
    for m in _TOOL_CALL_SPAN_RE.finditer(buffer):
        out += buffer[pos:m.start()]
        pos = m.end()
    rest = buffer[pos:]
    # Intact, unclosed open tag → hold from there.
    open_pos = rest.find("[TOOL_CALL]")
    if open_pos != -1:
        out += rest[:open_pos]
        return out, rest[open_pos:]
    # No intact open tag, but a partial open tag may be split across chunks
    # (e.g. "[TOOL_CA" + "LL]..."). Hold back from the last "[" if what follows
    # is a prefix of the literal open tag "[TOOL_CALL]".
    bi = rest.rfind("[")
    if bi != -1 and "[TOOL_CALL]".startswith(rest[bi:]):
        out += rest[:bi]
        return out, rest[bi:]
    out += rest
    return out, ""


async def _save_assistant_message(
    conv_id: str,
    content: str,
    citations: list[dict],
    cache_hit: bool,
    retrieval_ms: int = 0,
    msg_id: str | None = None,
    status: str | None = None,
    prompt_tokens: int | None = None,
) -> Message:
    """Persist assistant message and update conversation timestamp.

    If ``msg_id`` is given and that message already exists, update it in
    place (used to replace a pending-limit placeholder with the final answer).
    Otherwise create a new message (optionally reusing ``msg_id`` as its id).

    ``status`` persists a message state (e.g. ``"stopped"`` for a manually
    terminated turn) so the frontend can re-apply localized notes after reload.
    """
    async with db_mod.async_session() as session:
        if msg_id:
            existing = await session.get(Message, msg_id)
            if existing:
                existing.content = content
                existing.citations = citations
                existing.cache_hit = cache_hit
                existing.retrieval_ms = retrieval_ms
                existing.status = status
                if prompt_tokens is not None:
                    existing.token_count = prompt_tokens
                await session.commit()
                return existing

        assistant_msg = Message(
            id=msg_id or str(uuid.uuid4()),
            conversation_id=conv_id,
            role="assistant",
            content=content,
            citations=citations,
            cache_hit=cache_hit,
            ttft_ms=0,
            retrieval_ms=retrieval_ms,
            llm_ms=0,
            status=status,
            token_count=prompt_tokens,
            content_token_count=count_text_tokens(content) + 4,
            created_at=datetime.utcnow(),
        )

        session.add(assistant_msg)
        conv = await session.get(Conversation, conv_id)
        if conv:
            conv.updated_at = datetime.utcnow()
        await session.commit()

    return assistant_msg


async def _cleanup_orphan_messages(conv_id: str, keep_id: str | None = None) -> int:
    """Delete dangling assistant messages stuck in ``generating`` state.

    These accumulate when a turn is suspended / hit the round limit / errored
    mid-stream: the per-round assistant bubble is pre-created with
    ``status='generating'`` but never finalized. Left alone they clutter the
    chat with empty or half-written duplicates. The one message we are about to
    finalize (``keep_id``) is preserved.
    """
    try:
        async with db_mod.async_session() as session:
            stmt = select(Message).where(
                Message.conversation_id == conv_id,
                Message.role == "assistant",
                Message.status == "generating",
            )
            if keep_id:
                stmt = stmt.where(Message.id != keep_id)
            rows = (await session.execute(stmt)).scalars().all()
            for m in rows:
                await session.delete(m)
            if keep_id:
                # Also clear any leftover 'generating' flag on the kept message.
                kept = await session.get(Message, keep_id)
                if kept and kept.status == "generating":
                    kept.status = "complete"
            await session.commit()
            return len(rows)
    except Exception as e:
        logger.warning("Failed to clean up orphan messages for %s: %s", conv_id, e)
        return 0


async def _resolve_suspension_messages(conv_id: str, keep_id: str | None = None) -> int:
    """Mark lingering suspension placeholders as resolved once a run finishes.

    When a turn is suspended (quota reached / need_user_input) the backend
    stores an assistant message with ``status=None`` as the inline "continue/stop"
    hint. If that run is later resumed and completes successfully, the resumed
    answer normally overwrites the same row via ``msg_id``. But any *extra*
    suspension rows left over from earlier rounds (or a crash between
    suspension and resume) would otherwise stay ``status=None`` forever, so a
    page refresh could resurrect a stale "continue/stop" bubble.

    This flips those leftover rows to ``status='resolved'`` so they are inert but
    still retained for history. The message we are finalizing (``keep_id``) is
    never touched.
    """
    try:
        async with db_mod.async_session() as session:
            stmt = select(Message).where(
                Message.conversation_id == conv_id,
                Message.role == "assistant",
                Message.status.is_(None),
            )
            if keep_id:
                stmt = stmt.where(Message.id != keep_id)
            rows = (await session.execute(stmt)).scalars().all()
            for m in rows:
                m.status = "resolved"
            await session.commit()
            return len(rows)
    except Exception as e:
        logger.warning("Failed to resolve suspension messages for %s: %s", conv_id, e)
        return 0


async def _read_context_cursor(conv_id: str) -> tuple[int, int]:
    """Return ``(summary_msg_seq, total_messages)`` for a conversation.

    Read in its own session (mirrors ``_save_assistant_message``) because it is
    called from inside the SSE producer, after the request-scoped session has
    already been used for the turn. Failures degrade to ``(0, 0)`` -- this is
    telemetry for the context meter and must never break the stream.
    """
    try:
        async with db_mod.async_session() as session:
            conv = await session.get(Conversation, conv_id)
            total = await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conv_id)
            )
            return (
                (getattr(conv, "summary_msg_seq", 0) or 0) if conv else 0,
                total.scalar() or 0,
            )
    except Exception as e:
        logger.warning("Failed to read context cursor for %s: %s", conv_id, e)
        return 0, 0


async def _persist_agent_steps(conv_id: str, message_id: str, steps: list[dict]) -> None:
    """Persist accumulated agent_step traces to the agent_steps table.

    Runs in its own session (mirrors _save_assistant_message). Steps are stored
    verbatim for audit/replay; they are intentionally excluded from the LLM
    context and from MEM0 memory extraction elsewhere. Full retention (no cap).
    """
    if not steps:
        return
    try:
        async with db_mod.async_session() as session:
            # Replace, don't append: callers pass the full snapshot for a message,
            # so stale steps from a previous run on the same message must be cleared
            # first. Without this, re-running the agent on one message accumulates
            # duplicate steps across runs (e.g. 14 logical steps -> 119 rows).
            await session.execute(
                text(
                    "DELETE FROM agent_steps WHERE message_id = :mid"
                ),
                {"mid": message_id},
            )
            objs = []
            for i, s in enumerate(steps):
                extra = s.get("extra")
                objs.append(AgentStep(
                    conversation_id=conv_id,
                    message_id=message_id,
                    seq=i,
                    stage=s.get("stage", ""),
                    message=s.get("message", ""),
                    extra_json=json.dumps(extra, ensure_ascii=False) if extra else None,
                ))
            session.add_all(objs)
            await session.commit()
    except Exception as e:
        logger.warning("Failed to persist agent steps: %s", e)


# ── Suspension snapshot persistence (DB) ───# Persist the Human-in-the-Loop pure-data snapshot to the DB so it survives page refresh / process restart.。# These helpers reuse the caller's session, so tests can use their overridden test session.。

async def _save_pending_state(session, conv_id: str, message_id: str, snap: dict) -> None:
    """Persist a pending-limit snapshot to DB (upsert by conversation_id)."""
    snap_json = json.dumps(snap, ensure_ascii=False, default=str)
    row = await session.get(PendingLimitState, conv_id)
    if row:
        row.message_id = message_id
        row.snapshot_json = snap_json
    else:
        session.add(PendingLimitState(
            conversation_id=conv_id,
            message_id=message_id,
            snapshot_json=snap_json,
            created_at=datetime.utcnow(),
        ))
    await session.commit()


async def _load_pending_state(session, conv_id: str) -> dict | None:
    """Load a persisted pending-limit snapshot, or None if none pending."""
    row = await session.get(PendingLimitState, conv_id)
    if not row:
        return None
    snap = json.loads(row.snapshot_json)
    snap["pending_msg_id"] = row.message_id
    return snap


async def _clear_pending_state(session, conv_id: str) -> None:
    """Delete a persisted pending-limit snapshot for a conversation."""
    row = await session.get(PendingLimitState, conv_id)
    if row:
        await session.delete(row)
        await session.commit()


# ── File reference expansion ──
# The frontend inserts workspace file references into the user query as
# `[[file:rel_path]]`. On send we resolve each reference against the user's
# sandbox and expand it into the LLM prompt with an *adaptive* placement that
# maximises how accurately the model understands the question:
#   - short files  → spliced in place, right next to their reference (zero
#     co-reference cost, content co-located with the instruction that uses it);
#   - long / high-density / binary files → quarantined to a labelled appendix
#     at the very TOP of the message, so they don't break the user's sentence
#     or bury the instruction in the middle of the prompt.
# The original tokenised query is still stored as the user message (so history
# stays compact and human-readable); only the LLM sees the expanded version.

# Match `[[file:some/relative/path]]` — capture the path (anything but `]`).
FILE_REF_RE = re.compile(r"\[\[file:([^\]]+)\]\]")

# Guardrails so a huge/binary file can't blow up the prompt or inject garbage.
_MAX_FILE_CHARS = 40_000      # per-file content cap
_MAX_TOTAL_CHARS = 120_000    # combined cap across all referenced files

# Adaptive placement (see _expand_file_refs): short files are spliced in place
# next to their reference; long / dense / binary files are quarantined to a
# labelled appendix at the top of the message so they don't break the user's
# sentence or bury the instruction in the middle of the prompt.
_INLINE_MAX_CHARS = 1500       # a single file ≤ this → candidate for in-place
_INLINE_BUDGET_CHARS = 6000    # cumulative in-place content cap; overflow → appendix


def _looks_binary(text: str) -> bool:
    """Heuristic: is this "text" actually binary garbage?

    Decoding succeeded (so it's valid UTF-8), but a high ratio of control
    characters strongly suggests a binary payload (PDF/zip/…) that would only
    pollute the context.
    """
    sample = text[:2000]
    if not sample:
        return False
    ctrl = sum(1 for ch in sample if ord(ch) < 9 or 13 < ord(ch) < 32)
    return ctrl / len(sample) > 0.1


def _is_high_density(text: str) -> bool:
    """One or a few very long unbroken lines (base64 blobs, minified bundles,
    giant CSV rows). Valid text, but painful to read when spliced mid-sentence,
    so we push it to the top appendix instead of inlining it.
    """
    if len(text) < 200:
        return False
    nl_ratio = text.count("\n") / max(1, len(text)) * 100  # newlines per 100 chars
    return nl_ratio < 0.3


def _strip_trailing_source_dump(content: str, threshold: int = 1500) -> str:
    """When a downloadable file was just generated, the model is expected to give
    only a short note — not re-paste the generated file's source. If it left a
    trailing code block (open or closed) that is a large source dump, drop it so
    the answer stays clean and the system download link isn't buried/neutralised.

    Only the LAST code block is ever touched, and only when it is unclosed (always
    an error) or clearly oversized (> threshold chars). Small/inline snippets are
    left intact.
    """
    fence_positions = [m.start() for m in re.finditer(r"```", content)]
    n = len(fence_positions)
    if n == 0:
        return content
    if n % 2 == 1:
        # Unclosed trailing block: drop from the opening fence to end of string.
        return content[: fence_positions[-1]].rstrip()
    # Even count: the last block is closed. Inspect its size.
    start = fence_positions[-2]
    end = fence_positions[-1] + 3  # include the closing fence
    block = content[start:end]
    if len(block) > threshold:
        return content[:start].rstrip() + "\n"
    return content


async def _read_workspace_file_text(uid: int, path: str) -> tuple[str | None, str | None]:
    """Read a text file from the user's sandbox via the MCP REPL proxy.

    Returns ``(content, None)`` on success or ``(None, error_message)`` on any
    failure (missing sandbox, not found, binary, transport error, …).
    """
    from app.services.config_manager import config_manager

    secret = config_manager.repl_auth_secret
    if not secret:
        return None, "REPL 认证未配置"
    base = settings.mcp_repl_internal_url.rstrip("/")
    url = f"{base}/workspace/{path.lstrip('/')}"
    headers = {"X-Repl-Auth": secret, "X-Repl-Uid": str(uid)}

    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.ConnectError:
        return None, "MCP REPL 服务不可用"
    except Exception as e:  # noqa: BLE001 - surface uniformly
        return None, f"读取失败: {e}"

    if resp.status_code == 404:
        return None, "文件不存在"
    if resp.status_code != 200:
        return None, f"MCP 错误 {resp.status_code}"

    try:
        text = resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return None, "文件为二进制，无法作为文本插入"

    if _looks_binary(text):
        return None, "文件为二进制，无法作为文本插入"

    if len(text) > _MAX_FILE_CHARS:
        text = text[:_MAX_FILE_CHARS] + f"\n... (内容已截断，原文 {len(text)} 字符)"
    return text, None


async def _expand_file_refs(
    query: str, uid: int | None
) -> tuple[str, list[str], dict[str, int]]:
    """Expand `[[file:path]]` references with *adaptive* placement.

    Returns ``(expanded_query, errors, summary)`` where ``summary`` reports how
    many files ended up in-place vs in the top appendix vs failed to read.

    Placement rules (tuned for LLM comprehension, not literal position):
      - binary / high-density (one long unbroken line) → top appendix (note);
      - else if ``len ≤ _INLINE_MAX_CHARS`` AND cumulative in-place content
        ``≤ _INLINE_BUDGET_CHARS`` → in place, next to the reference;
      - otherwise → top appendix, referenced from the question as ``文档N``.

    Unreadable files fall back to a short inline note rather than aborting.
    """
    refs = FILE_REF_RE.findall(query)
    if not refs:
        return query, [], {"inline": 0, "prepend": 0, "failed": 0}

    # Deduplicate while preserving first-seen order.
    seen: set[str] = set()
    unique: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            unique.append(r)

    # Read every referenced file once.
    raw: dict[str, tuple[str | None, str | None]] = {}
    for path in unique:
        if uid is None:
            raw[path] = (None, "用户沙箱未初始化")
        else:
            raw[path] = await _read_workspace_file_text(uid, path)

    resolution: dict[str, str] = {}   # path -> replacement used in the body
    appendices: list[tuple[int, str, str]] = []  # (idx, path, block)
    label_of: dict[str, int] = {}
    inline_used = 0
    total = 0
    label_counter = 0
    summary = {"inline": 0, "prepend": 0, "failed": 0}
    errors: list[str] = []

    for path in unique:
        content, err = raw[path]

        # 1) Read failure --------------------------------------------------
        if err or content is None:
            if err and "二进制" in err:
                # Binary: quarantine to the appendix with a clean note instead
                # of inlining mojibake next to the user's question.
                label_counter += 1
                label_of[path] = label_counter
                summary["prepend"] += 1
                appendices.append(
                    (label_counter, path,
                     f"文档{label_counter} [{path}]:\n(该文件疑似二进制，无法以文本形式读取)\n")
                )
                resolution[path] = f"文档{label_counter}"
                continue
            resolution[path] = f"[文件 {path} 读取失败：{err or '读取失败'}]"
            summary["failed"] += 1
            errors.append(f"{path}: {err or '读取失败'}")
            continue

        # 2) Cumulative guardrail ------------------------------------------
        if total + len(content) > _MAX_TOTAL_CHARS:
            resolution[path] = (
                f"[文件 {path} 已跳过：累计内容超出 {_MAX_TOTAL_CHARS} 字符上限]"
            )
            errors.append(f"{path}: 累计超出字符上限")
            continue

        # 3) Classify placement -------------------------------------------
        force_appendix = _is_high_density(content)
        fits_inline = (
            not force_appendix
            and len(content) <= _INLINE_MAX_CHARS
            and inline_used + len(content) <= _INLINE_BUDGET_CHARS
        )

        if fits_inline:
            resolution[path] = (
                f"\n=== 文件内容: {path} ===\n{content}\n=== 文件内容结束: {path} ===\n"
            )
            inline_used += len(content)
            total += len(content)
            summary["inline"] += 1
        else:
            label_counter += 1
            label_of[path] = label_counter
            summary["prepend"] += 1
            appendices.append(
                (label_counter, path, f"文档{label_counter} [{path}]:\n{content}\n")
            )
            resolution[path] = f"文档{label_counter}"
            total += len(content)

    def _sub(m: re.Match) -> str:
        return resolution.get(m.group(1), m.group(0))

    body = FILE_REF_RE.sub(_sub, query)
    prefix = "\n\n".join(block for _, _, block in appendices)
    expanded = (prefix + "\n\n" + body) if appendices else body
    return expanded, errors, summary

# Approach B: manual suspend/resume state repository.。# Persisted to the DB (pending_limit_states table); survives refresh / restart and supports multiple workers.。# Keep only transient runtime objects in memory (stripped by _snapshot_state); the snapshot body is persisted to the DB.。


def _snapshot_state(state: dict) -> dict:
    """Persist the pure-data snapshot needed for suspension (runtime objects such as emit are not stored)."""
    return {
        "query": state.get("query"),
        "active_skill": state.get("active_skill"),
        "available_tools": state.get("available_tools"),
        "rag_context": state.get("rag_context"),
        "citations": state.get("citations"),
        "tool_results": state.get("tool_results"),
        "tool_messages": state.get("tool_messages"),
        "skill_stack": state.get("skill_stack"),
        "loaded_skill_ids": state.get("loaded_skill_ids"),
        "subdir": state.get("subdir"),
        "skill_switch_count": state.get("skill_switch_count"),
        "tool_round": state.get("tool_round"),
        "skill_switch_quota": state.get("skill_switch_quota"),
        "tool_round_quota": state.get("tool_round_quota"),
        "pending_limit": state.get("pending_limit"),
        "download_entries": state.get("download_entries", []),
    }


def _build_resume_initial_state(pending, mode, current_user, history, kb_prompt, request, emit_fn, conv_id, summary_text: str = "", summary_msg_seq: int = 0, emit_usage_fn=None) -> dict:
    """Rebuild initial_state from the snapshot: history is left untouched; only recharge the quota (continue) or clear tool_calls (stop).

    The accumulated conversation summary (if any) is re-injected, and the already
    summarized prefix of the history is dropped to avoid duplication with the summary.
    """
    pl = pending.get("pending_limit") or {}
    if mode == "continue":
        quota_ss = pending["skill_switch_quota"] + config_manager.skill_switch_quota
        quota_tr = pending["tool_round_quota"] + config_manager.agent_round_quota
        tool_calls = pl.get("deferred_tool_call")
        resume_action = "continue"
    else:  # stop
        quota_ss = pending["skill_switch_quota"]
        quota_tr = pending["tool_round_quota"]
        tool_calls = None
        resume_action = "stop"
    # Skip the earliest messages already captured in the summary. Inclusive
    # boundary: the message at seq == summary_msg_seq is part of the un-summarized
    # tail (kept in lockstep with the compaction tail). Slice by seq VALUE (never
    # list position) so it stays correct whether history is a full list or the
    # cached tail-only list.
    recent_history = _tail_from(history, summary_msg_seq) if summary_msg_seq else history
    return {
        "query": pending.get("query") or request.query,
        "kb_id": request.kb_id,
        "skill_id": request.skill_id,
        "user_id": current_user.id,
        "tenant_id": current_user.tenant_id,
        "user_memory": current_user.memory or "",
        "conversation_history": recent_history,
        "conversation_summary": summary_text,
        "conversation_id": conv_id,
        "subdir": pending["subdir"],
        # Prefer the user's persisted profile timezone, then the per-request
        # browser-detected value, then UTC. Avoids relying solely on the
        # browser's auto-detected timezone (which containerized/privacy browsers
        # often report as UTC) so file/code timestamps match the user's locale.
        "timezone": current_user.timezone or request.timezone or "UTC",
        "active_skill": pending["active_skill"],
        "available_tools": pending["available_tools"],
        "rag_context": pending["rag_context"],
        "citations": pending["citations"],
        "tool_calls": tool_calls,
        "tool_round": pending["tool_round"],
        "tool_results": pending["tool_results"],
        "tool_messages": pending["tool_messages"],
        "cache_hit": False,
        "final_answer": "",
        "retrieval_ms": 0,
        "skip_cache": request.skip_cache,
        "kb_prompt": kb_prompt,
        "skill_switch_quota": quota_ss,
        "tool_round_quota": quota_tr,
        "pending_limit": None,
        "resume_action": resume_action,
        "agent_steps": [],
        "download_entries": pending.get("download_entries", []),
        "emit": emit_fn,
        "emit_usage": emit_usage_fn,
    }


@dataclass
class RunHandle:
    """Tracks an in-flight streaming run so a page refresh can re-attach to it.

    When a client opens /chat/stream normally, a RunHandle is created and every
    emitted SSE line is appended to ``replay`` (so late subscribers can catch up)
    and fan-out to all current subscriber queues (the original client + any
    re-attaching clients). The handle is removed from RUN_REGISTRY once the run
    finishes, so a refresh that lands after completion simply reloads from the DB.
    """

    conv_id: str
    replay: list[str] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    stream_msg_id: str | None = None
    producer_task: asyncio.Task | None = None
    # Set by _abort_run when the run is killed on purpose (the conversation is
    # being deleted). It tells the SSE consumer that the producer's
    # CancelledError is expected, so the stream ends quietly instead of
    # surfacing as a request crash.
    aborted: bool = False


# conversation_id -> active run handle. Process-local (single worker). A refresh
# re-attaches only while the original run is still alive in this process.
RUN_REGISTRY: dict[str, RunHandle] = {}


def _emit_run(handle: RunHandle, line: str) -> None:
    """Persist + fan-out one SSE line to every subscriber of a run.

    NOTE: this is intentionally synchronous. Both call sites (``enqueue`` and
    ``on_queue_position``) call it fire-and-forget; making it async would mean
    the coroutine is never awaited and every SSE event silently disappears.
    """
    handle.replay.append(line)
    for q in list(handle.subscribers):
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            pass


# How long a caller waits for an aborted producer to unwind. Cancellation lands
# on the producer's next await (normally the LLM stream), so this returns in
# milliseconds; the timeout only bounds a producer parked in a non-cancellable
# executor call.
_ABORT_WAIT_S = 3.0


async def _abort_run(conv_id: str) -> bool:
    """Kill the in-flight streaming run of a conversation, if any.

    Returns True when a live run was actually aborted.

    Deleting a conversation mid-stream requires this. The producer deliberately
    outlives its client (see the note in ``generate``'s finally), so left alone it
    would finish generating and write the assistant message + agent_steps into a
    conversation row that no longer exists. SQLite runs with foreign keys OFF, so
    those inserts would not fail -- they would silently become orphan rows.
    """
    handle = RUN_REGISTRY.pop(conv_id, None)
    if handle is None or handle.done.is_set():
        return False

    handle.aborted = True
    task = handle.producer_task
    if task is not None and not task.done():
        task.cancel()
        # asyncio.wait -- NOT await/wait_for -- so the producer's CancelledError
        # is never re-raised into the caller's own task.
        await asyncio.wait({task}, timeout=_ABORT_WAIT_S)

    # Release every attached client. The producer's finally normally does this,
    # but not if it was cancelled before that block was ever reached.
    for q in list(handle.subscribers):
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            pass
    handle.done.set()
    return True


# Substrings that mark a context-window overflow. After every trimming guard has
# run, any residual 400 from the provider is overwhelmingly a context overflow;
# this lets us swap the raw provider error text for a clean localized code.
#
# These are deliberately phrases, never bare words. "exceed"/"超出" on their own
# also appear in quota and rate-limit errors, and mislabelling those as "your
# question is too long" sends the user chasing the wrong problem.
_CONTEXT_OVERFLOW_HINTS = (
    "maximum context length",
    "max context length",
    "context length",
    "context window",
    "context_length_exceeded",
    "too many tokens",
    "exceeds the model",
    "exceeds the maximum",
    "exceeds the context",
    "prompt is too long",
    "prompt too long",
    "input is too long",
    "sequence length",
    "reduce the length of the messages",
    # Reachable only since the provider response BODY started travelling with the
    # exception (see llm_client.LLMProviderError); these are the phrasings the
    # earlier status-line-only message could never contain.
    "token limit",
    "however, you requested",
    "reduce your prompt",
    "input length",
    "输入过长",
    "提示过长",
    "上下文长度",
    "上下文窗口",
    "超出上下文",
    "超过上下文",
)

# Failure modes that also say "exceeded"/"超出" but are NOT context overflows.
# Checked before the overflow hints so a billing or throttling problem always
# reaches the user verbatim.
_NON_CONTEXT_HINTS = (
    "quota",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "code: 429",
    "insufficient",
    "billing",
    "余额",
    "配额",
    "限流",
    "请求过于频繁",
)


def _classify_llm_error(e: Exception) -> str:
    """Map an LLM exception to a user-facing error code where possible.

    After all context-budget guards have run (entry firewall, fit trimming, tool
    reservation), a residual provider 400 is almost always a context-window
    overflow. Surface that as the clean localized code ``LLM_CONTEXT_EXCEEDED``
    instead of the raw provider error text. Anything we cannot confidently
    classify still goes through verbatim so genuine bugs stay visible.

    Quota/rate-limit failures are excluded first: they share the "exceeded"
    wording but need the real provider text, not a "shorten your question" hint.
    """
    # Our own context gates already carry a bare code; pass it straight through
    # instead of running it past the provider-text heuristics.
    if isinstance(e, ContextWindowExceeded):
        return str(e)
    text = str(e).lower()
    if any(hint in text for hint in _NON_CONTEXT_HINTS):
        return str(e)
    if any(hint in text for hint in _CONTEXT_OVERFLOW_HINTS):
        return "LLM_CONTEXT_EXCEEDED"
    return str(e)


async def _explicit_skill_prompt(skill_id: str | None) -> str | None:
    """SKILL.md body of an EXPLICITLY selected skill, for the entry-point gate.

    Returns None when no skill was pinned by the user (the gate then falls back to
    the configured system prompt, which is what the graph would use) or when the
    skill cannot be read. Auto-routed skills are intentionally not resolved here:
    routing runs an LLM call inside the graph, which is exactly the expensive work
    this gate exists to avoid. Any failure degrades to None -- a slightly optimistic
    floor is fine, the precise ceiling lives in fit_assembly_context.
    """
    if not skill_id:
        return None
    try:
        idx = await _get_skill_index(skill_id)
        folder = (idx or {}).get("folder_name")
        if not folder:
            return None
        content = read_skill_md(folder)
        if not content:
            return None
        return parse_skill_md(content).get("body") or None
    except Exception as e:  # never block a request on a gate-input lookup
        logger.warning("entry gate: skill prompt lookup failed for %s: %s", skill_id, e)
        return None


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE streaming RAG chat endpoint.

    Events:
        data: {"type": "queue", "position": N}
        data: {"type": "token", "content": "..."}
        data: {"type": "citation", "citation": {...}}
        data: {"type": "error", "message": "..."}
        data: {"type": "context_usage", "prompt_tokens": N,
               "persistent_tokens": N, "transient_tokens": N}
        data: {"type": "done", "conversation_id": "...", "message_id": "...",
               "cache_hit": ..., "prompt_tokens": N,
               "summary_msg_seq": N, "total_messages": N}

    ``context_usage`` fires once per LLM submission (every tool round, then the
    final generation), so the frontend context meter always reflects the most
    recent payload. ``done`` repeats the final numbers and adds the
    summary-folding cursor used by the context modal.
    """

    # Get or create conversation
    conv_id = request.conversation_id
    if conv_id:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        conv = result.scalar_one_or_none()
        if not conv:
            raise HTTPException(404, "Conversation not found")
        # Verify ownership: cannot continue someone else's conversation
        if conv.user_id and conv.user_id != current_user.id:
            raise HTTPException(403, "不能在其他用户的对话中发言")
    else:
        title = request.query[:50] + ("..." if len(request.query) > 50 else "")
        conv = Conversation(
            id=str(uuid.uuid4()),
            title=title,
            kb_id=request.kb_id,
            user_id=current_user.id,
        )
        db.add(conv)
        await db.commit()
        conv_id = conv.id

    # ── Attach: re-join an in-flight run after a page refresh ───────────────
    # The browser reloaded mid-stream; instead of starting a brand-new turn we
    # re-attach to the still-running producer, replaying every SSE line emitted
    # so far and then streaming the rest live. No DB writes happen here.
    if request.attach:
        handle = RUN_REGISTRY.get(conv_id)
        if handle is None or handle.done.is_set():
            # The run already finished (or never existed): tell the client to just
            # reload the conversation from the DB — there is nothing to re-stream.
            async def _already_done():
                yield _sse("run_gone", {"conversation_id": conv_id})
            return StreamingResponse(
                _already_done(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        async def _attach_stream():
            # 1) Replay everything emitted before we joined.
            for line in list(handle.replay):
                yield line
            # 2) Subscribe for live events going forward.
            sub: asyncio.Queue = asyncio.Queue()
            handle.subscribers.append(sub)
            try:
                while True:
                    line = await sub.get()
                    if line is None:
                        break
                    yield line
            finally:
                try:
                    handle.subscribers.remove(sub)
                except ValueError:
                    pass

        return StreamingResponse(
            _attach_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Resume (continue/stop) may carry an empty body.query because the real
    # query is persisted in the suspension snapshot. A genuine new question
    # must carry a non-empty query.
    is_resume = request.resume_action in ("continue", "stop")
    if not is_resume and not request.query:
        raise HTTPException(status_code=422, detail="query 不能为空")

    # Build conversation history. A conversation's messages only ever GROW by
    # append, never mutate, so we serve them from the per-conversation cache: the
    # first load queries the un-summarized tail (seq >= cursor); every later turn
    # in the same conversation reuses the cache and refreshes only appended rows.
    # Compression (conversation_summary.py) still decides what to fold at request
    # time; raw messages are never truncated here.
    history = await _load_history(
        conv_id, db, getattr(conv, "summary_msg_seq", 0) or 0
    )

    # Fetch the KB's instruction prompt once; reuse for cache key + system prompt.
    kb_prompt = await get_kb_prompt(request.kb_id)

    # Save user message (skipped on resume: the original question is already
    # in history and the snapshot carries the query — don't append a duplicate
    # empty user bubble).
    if not is_resume:
        user_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv_id,
            role="user",
            content=request.query,
            content_token_count=count_text_tokens(request.query) + 4,
            created_at=datetime.utcnow(),
        )
        db.add(user_msg)

    # Update conversation timestamp
    conv.updated_at = datetime.utcnow()
    await db.commit()

    # Streaming response
    async def generate():
        sse_queue: asyncio.Queue[str | None] = asyncio.Queue()

        # Guard against a concurrent second producer for the same conversation.
        # This happens when a page-refresh fires a fresh /stream while a prior run
        # is still generating, or a stale "continue" races a live run. Two
        # producers would both write the same assistant row and clobber each
        # other, so instead we re-attach to the existing handle: replay every
        # SSE line emitted so far, then stream the rest live. No new producer
        # is started and no DB writes happen here.
        existing = RUN_REGISTRY.get(conv_id)
        if existing is not None and not existing.done.is_set():
            for line in list(existing.replay):
                yield line
            existing.subscribers.append(sse_queue)
            try:
                while True:
                    line = await sse_queue.get()
                    if line is None:
                        break
                    yield line
            finally:
                try:
                    existing.subscribers.remove(sse_queue)
                except ValueError:
                    pass
            return

        # Register this run so a page-refresh client can re-attach mid-flight.
        handle = RunHandle(conv_id=conv_id)
        handle.subscribers.append(sse_queue)
        RUN_REGISTRY[conv_id] = handle

        def enqueue(event_type: str, payload: dict) -> None:
            _emit_run(handle, _sse(event_type, payload))

        async def on_queue_position(pos: int) -> None:
            # Must stay async: llm_semaphore.acquire() does `await on_position(...)`.
            _emit_run(handle, _sse("queue", {"position": pos}))

        # In-memory scratchpad for this turn. agent_steps accumulate here
        # (independent of `state`) and are written to the DB ONCE at the end of the
        # turn (see the success path at ~1071 and the error path at ~1118). This
        # avoids re-inserting the whole list on every emit, which previously blew
        # up the row count (e.g. 14 logical steps -> 119 rows) on multi-run turns.
        # Trade-off: a process crash mid-turn loses that turn's trace (client
        # disconnects are fine — the producer keeps running and still flushes).
        stream_msg_id: dict = {"id": None}
        stream_agent_steps: list = []

        def emit_agent_step(stage: str, message: str, **extra) -> None:
            """Stream an agent_step progress event (Route D observability)."""
            enqueue("agent_step", {"stage": stage, "message": message, **extra})
            # Accumulate only; persistence happens once at turn end.
            stream_agent_steps.append({
                "stage": stage,
                "message": message,
                "extra": extra or None,
            })

        def emit_context_usage(breakdown: dict) -> None:
            """Stream the token footprint of the submission just handed to the LLM.

            Emitted once per LLM submission (each tool round, then the final
            generation). The frontend meter shows the LATEST value, so a later
            event simply overwrites an earlier one within the same turn.
            """
            enqueue("context_usage", dict(breakdown))

        async def producer():
            try:
                from app.services.agent_graph import ragclaw_agent_graph
                from app.services.llm_client import (
                    llm_client,
                )
                from app.services.agent_nodes import (
                    _extract_download_entries_from_state,
                )

               # ── 0. Suspension triage: does this session have a pending quota suspension awaiting user confirmation (persisted snapshot) ───
                pending = await _load_pending_state(db, conv_id)
                resume_mode = None
                pending_msg_id = None
                # Snapshot of a suspension we are about to clear, so that if the run
                # subsequently crashes BEFORE a new suspension/final answer is persisted,
                # we can re-store it in the except handler (see below). Without this, a
                # crash during resume would permanently drop the suspension: the user's
                # "continue" already cleared it, the run died, and a page refresh finds
                # nothing to restore -> the inline resume bubble never comes back.
                cleared_pending = None
                cleared_pending_msg_id = None
                if pending is not None:
                    pending_msg_id = pending.get("pending_msg_id")
                    # NOTE: We intentionally do NOT clear the pending state here. Clearing
                    # at resume-start means a mid-run page refresh (the SSE connection drops,
                    # the backend run is still in flight) leaves the DB with no suspension
                    # snapshot — so the reloaded frontend finds nothing to restore and the
                    # inline "continue/stop" bubble never comes back. Instead we keep the
                    # snapshot alive until the run definitively ends:
                    #   - need_user_input branch: overwrites with a fresh snapshot (or, for
                    #     stop-mode, clears it below)
                    #   - done branch: clears it below (run finished, no suspension left)
                    #   - crash (except): restores it (see except handler) so the user can retry
                    if request.resume_action == "continue":
                        resume_mode = "continue"
                        cleared_pending, cleared_pending_msg_id = pending, pending_msg_id
                    elif request.resume_action == "stop":
                        resume_mode = "stop"
                        cleared_pending, cleared_pending_msg_id = pending, pending_msg_id
                    else:
                       # User sends a new question (not continue/stop): treat as stop, discard the suspension, and answer the new question normally
                        cleared_pending, cleared_pending_msg_id = pending, pending_msg_id
                elif is_resume:
                    # Resume requested but no suspension snapshot exists
                    # (e.g. it was already cleared). Cannot replay with an
                    # empty query, so fail loudly instead of producing a blank answer.
                    enqueue("error", {"message": "CONVERSATION_STATE_NOT_RECOVERABLE"})
                    return

                if resume_mode is None:
                   # ── 1. Normal new question (with cache) ───
                    if settings.cache_enabled and not request.skip_cache:
                        cached = answer_cache.get(
                            request.query, request.kb_id, request.skill_id or "", kb_prompt=kb_prompt
                        )
                        if cached:
                            enqueue("token", {"content": cached.answer})
                            for c in cached.citations or []:
                                enqueue("citation", {"citation": c})

                            assistant_msg = await _save_assistant_message(
                                conv_id,
                                cached.answer,
                                cached.citations or [],
                                cache_hit=True,
                            )
                            cursor, total_msgs = await _read_context_cursor(conv_id)
                            enqueue("done", {
                                "conversation_id": conv_id,
                                "message_id": assistant_msg.id,
                                "cache_hit": True,
                                "ttft_ms": 0,
                                "retrieval_ms": 0,
                                "llm_ms": 0,
                                "summary_msg_seq": cursor,
                                "total_messages": total_msgs,
                            })
                            return

                    # ── 1a-bis. Reject doomed requests before any heavy processing ──
                    # (history compression, RAG, file reads, LLM calls). The gate
                    # measures the same floor the assembly point would reach: the
                    # fixed prefix (skill body / KB instructions / working-dir note /
                    # user memory) plus the query, with everything droppable removed.
                    # Resolved once and reused by the post-file-expansion re-check.
                    gate_inputs = {
                        "kb_prompt": kb_prompt,
                        "user_memory": current_user.memory or "",
                        "ws_context": _build_working_dir_prompt(
                            {"subdir": request.subdir or ""}
                        ),
                        "skill_prompt": await _explicit_skill_prompt(request.skill_id),
                        "pinned_instruction": getattr(conv, "pinned_instruction", "") or "",
                    }
                    overflow_code = classify_entry_overflow(request.query, **gate_inputs)
                    if overflow_code:
                        enqueue("error", {"message": overflow_code})
                        return

                    # ── 1b. Pre-create the assistant message so the final content
                    # and the accumulated agent_steps can be persisted at the end
                    # of the turn (written once, not incrementally). The final
                    # content is upserted onto this same row at the end of the turn.
                    if stream_msg_id["id"] is None:
                        init_msg = await _save_assistant_message(
                            conv_id, "", [], cache_hit=False,
                            msg_id=pending_msg_id if resume_mode is not None else None,
                            status="generating",
                        )
                        stream_msg_id["id"] = init_msg.id
                        handle.stream_msg_id = init_msg.id

                    # ── 1a. Expand [[file:rel_path]] references (adaptive placement) ──
                    # The original tokenised query stays the cache/history key; only
                    # the LLM sees the expanded version. Short files are spliced in
                    # place next to their reference; long / dense / binary files are
                    # moved to a labelled appendix at the top of the message.
                    file_refs = FILE_REF_RE.findall(request.query)
                    expanded_query = request.query
                    file_read_errors: list[str] = []
                    file_summary = {"inline": 0, "prepend": 0, "failed": 0}
                    if file_refs:
                        uid = await get_user_repl_uid(current_user.id)
                        expanded_query, file_read_errors, file_summary = await _expand_file_refs(
                            request.query, uid
                        )
                        msg = (
                            f"Attached {len(file_refs)} file(s)"
                            f" (inlined {file_summary['inline']}, prepended appendix {file_summary['prepend']})"
                        )
                        if file_summary["failed"]:
                            msg += f"; {file_summary['failed']} failed to read"
                        emit_agent_step("file_context", msg)

                    # ── 1a-ter. Re-check after file expansion: expanded_query can be
                    # far larger than request.query once large files are spliced in.
                    # Same fast-fail as above.
                    overflow_code = classify_entry_overflow(expanded_query, **gate_inputs)
                    if overflow_code:
                        enqueue("error", {"message": overflow_code})
                        return

                    # Compress history / summary / query if it would overflow the
                    # context window. Returns (recent_messages, summary_text,
                    # condensed_query, warning); the summary is persisted on the
                    # conversation and injected as a system message downstream. Raw
                    # messages are never modified.
                    (
                        recent_history,
                        summary_text,
                        condensed_query,
                        summary_warning,
                    ) = await build_context_with_summary(
                        conv, history, db, config_manager.prompt_language, expanded_query,
                        emit=emit_agent_step,
                    )
                    if summary_warning:
                        emit_agent_step("context_compress", summary_warning)

                    initial_state = {
                        "query": condensed_query or expanded_query,
                        "kb_id": request.kb_id,
                        "skill_id": request.skill_id,
                        "user_id": current_user.id,
                        "tenant_id": current_user.tenant_id,
                        "user_memory": current_user.memory or "",
                        "conversation_history": recent_history,
                        "pinned_instruction": getattr(conv, "pinned_instruction", "") or "",
                        "conversation_summary": summary_text,
                        "conversation_id": conv_id,
                        # v2: user-selected workspace sub-directory ("" = root).
                        # Replaces the old per-conversation <ws> (conv_id) so all of a
                        # user's tool outputs land in their persistent workspace root.
                        "subdir": request.subdir or "",
                        # Prefer the user's persisted profile timezone, then the
                        # per-request browser-detected value, then UTC. Browsers in
                        # containers / privacy modes often report UTC, so the profile
                        # value must win. Used for cron scheduling and propagated to
                        # the REPL sandbox for local-time file stamps.
                        "timezone": current_user.timezone or request.timezone or "UTC",
                        "active_skill": None,
                        "available_tools": [],
                        "rag_context": "",
                        "citations": [],
                        "tool_calls": None,
                        "tool_round": 0,
                        "tool_results": [],
                        "tool_messages": [],
                        "download_entries": [],
                        "cache_hit": False,
                        "final_answer": "",
                        "retrieval_ms": 0,
                        "skip_cache": request.skip_cache,
                        "kb_prompt": kb_prompt,
                        "skill_switch_quota": config_manager.skill_switch_quota,
                        "tool_round_quota": config_manager.agent_round_quota,
                        "pending_limit": None,
                        "resume_action": None,
                        "agent_steps": [],
                        "emit": emit_agent_step,
                        "emit_usage": emit_context_usage,
                    }
                else:
                   # ── 1b. Resume: rebuild from snapshot, history untouched, only recharge / clear ───
                    initial_state = _build_resume_initial_state(
                        pending, resume_mode, current_user, history, kb_prompt, request, emit_agent_step, conv_id,
                        summary_text=conv.summary_text or "",
                        summary_msg_seq=getattr(conv, "summary_msg_seq", 0) or 0,
                        emit_usage_fn=emit_context_usage,
                    )

               # ── 1c. User manually stops: do not replay tools, do not generate an answer.
                #        A stop is a termination of the current (suspended) turn, NOT a new
                #        assistant message — so we must NOT persist the suspension code into
                #        the ``messages`` table (that would echo back as a fake answer on the
                #        next turn). The frontend overlays a localized termination notice on
                #        the existing suspension bubble via done.stopped, and the durable
                #        pending state remains so the user can still "continue" after a stop.
                if resume_mode == "stop":
                    cursor, total_msgs = await _read_context_cursor(conv_id)
                    enqueue("done", {
                        "conversation_id": conv_id,
                        "message_id": pending_msg_id,
                        "cache_hit": False,
                        "ttft_ms": 0,
                        "retrieval_ms": 0,
                        "llm_ms": 0,
                        "stopped": True,
                        "summary_msg_seq": cursor,
                        "total_messages": total_msgs,
                    })
                    return

               # ── 2. Run the graph ───
                async with llm_limiter.acquire(on_queue_position):
                    # Token acquired: build state and run agent graph.
                    # NOTE: the LLM time budget is now PER-CALL (each non-streaming
                    # call bounded by LLM_PER_CALL_BUDGET_SECONDS inside llm_client),
                    # not a cumulative cap on the whole turn — see llm_client.py.
                    state = await ragclaw_agent_graph.run(initial_state)

                   # ── 2b. Suspension detection: the graph requests user confirmation ───
                    if state.get("pending_limit"):
                        # A suspension is NOT an assistant answer and NOT a user query — it is
                        # a pure pause in the processing loop. Therefore it must NOT be written
                        # into the ``messages`` table (which only holds real conversation
                        # content). Writing the suspension code there would pollute the
                        # conversation history and get echoed back verbatim by the LLM on the
                        # next turn. Instead the suspension lives only in pending_limit_states
                        # (the durable snapshot) and is surfaced to the UI via the transient
                        # need_user_input SSE event, keyed by a stable bubble id that does NOT
                        # point at any messages row. The real final answer (produced after the
                        # user resumes) is persisted separately as a normal assistant message.
                        bubble_id = str(uuid.uuid4())
                        # Persist agent-step traces under the bubble id (audit table, decoupled
                        # from messages — the key is just a stable grouping id here).
                        await _persist_agent_steps(conv_id, bubble_id, state.get("agent_steps") or [])
                        snap = _snapshot_state(state)
                        snap["pending_msg_id"] = bubble_id
                        await _save_pending_state(db, conv_id, bubble_id, snap)
                        enqueue("need_user_input", {
                            "message": state["pending_limit"]["message"],
                            "conv_id": conv_id,
                            "kind": state["pending_limit"]["kind"],
                            "message_id": bubble_id,
                        })
                        # Drop any dangling 'generating' bubbles from earlier rounds.
                        await _cleanup_orphan_messages(conv_id, keep_id=bubble_id)
                        return

                    if state.get("cache_hit"):
                        # Defensive: should not happen because we checked above,
                        # but handle it gracefully if the graph recomputes and hits.
                        collected_content = state["final_answer"]
                        collected_citations = state.get("citations", [])
                        enqueue("token", {"content": collected_content})
                        for c in collected_citations:
                            enqueue("citation", {"citation": c})

                        assistant_msg = await _save_assistant_message(
                            conv_id,
                            collected_content,
                            collected_citations,
                            cache_hit=True,
                            msg_id=pending_msg_id if resume_mode is not None else None,
                        )
                        cursor, total_msgs = await _read_context_cursor(conv_id)
                        enqueue("done", {
                            "conversation_id": conv_id,
                            "message_id": assistant_msg.id,
                            "cache_hit": True,
                            "ttft_ms": 0,
                            "retrieval_ms": 0,
                            "llm_ms": 0,
                            "summary_msg_seq": cursor,
                            "total_messages": total_msgs,
                        })
                        return

                    # ── 3. Stream LLM generation ──
                    final_retr = state.get("retrieval_ms", 0)
                    messages, assembly_dropped = ragclaw_agent_graph.build_generation_messages(state)
                    if assembly_dropped:
                        emit_agent_step(
                            "context_compress",
                            _t("assembly_trim_warning", config_manager.prompt_language),
                        )
                    # Approximate total tokens of the request payload sent to the LLM.
                    prompt_tokens = count_messages_tokens(messages)
                    # Final submission of this turn: overwrite whatever the tool
                    # rounds reported so the meter ends on the real payload.
                    breakdown = state.get("context_breakdown") or {
                        "prompt_tokens": prompt_tokens,
                        "persistent_tokens": 0,
                        "transient_tokens": prompt_tokens,
                    }
                    emit_context_usage(breakdown)
                    # Signal the final-generation phase so the frontend can show it honestly
                    # (the graph handles everything up to here; the actual LLM stream starts now).
                    emit_agent_step("generating", "Generating answer…")
                    collected_content = ""
                    collected_citations = []
                    _stream_buf = ""  # holds back [TOOL_CALL] spans from live display

                    _stream_flush = 0
                    async for token in llm_client.chat_stream(messages, conversation_id=conv_id):
                        collected_content += token
                        _stream_buf += token
                        emit_text, _stream_buf = _suppress_tool_call_span(_stream_buf)
                        if emit_text:
                            enqueue("token", {"content": emit_text})
                        # Periodically upsert the partial answer so a disconnect
                        # mid-generation still leaves recoverable content in the DB.
                        if len(collected_content) - _stream_flush >= 64:
                            _stream_flush = len(collected_content)
                            try:
                                await _save_assistant_message(
                                    conv_id, collected_content, [], cache_hit=False,
                                    msg_id=stream_msg_id["id"],
                                    status="generating",
                                )
                            except Exception:
                                pass

                    # Flush any trailing buffer (e.g. an unclosed [TOOL_CALL] span)
                    # and strip it so it never reaches the visible answer.
                    if _stream_buf:
                        flush = _strip_tool_call_noise(
                            re.sub(r'\[TOOL_CALL\][\s\S]*', '', _stream_buf, flags=re.IGNORECASE)
                        )
                        if flush:
                            enqueue("token", {"content": flush})

                    # Clean the persisted answer: the raw [TOOL_CALL] span (and any
                    # dangling tail) must never be stored or shown to the user. The
                    # real tool call was already executed via the tool-decision path.
                    collected_content = _strip_tool_call_noise(
                        re.sub(r'\[TOOL_CALL\][\s\S]*', '', collected_content, flags=re.IGNORECASE)
                    )

                    # Surface generated-file download links through a SEPARATE,
                    # LLM-independent channel (agent_step stage="file_done") instead
                    # of appending markdown to the answer. This makes the link immune
                    # to the model leaving an unclosed code fence or re-pasting source
                    # (the recurring "broken download link" bug) and keeps the answer
                    # text clean. The frontend renders these as dedicated download
                    # buttons, not as inline markdown.
                    dl_entries = _extract_download_entries_from_state(state)
                    if dl_entries:
                        # The model may echo a legacy /api/download/user_u... link from
                        # an earlier turn (the old route is gone). Rewrite it to the
                        # uid-free workspace endpoint so it never leaks the uid nor
                        # renders as a second, broken link.
                        collected_content = re.sub(
                            r'\[([^\]]*)\]\((/api/download/user_u\d+/[^)]+)\)',
                            lambda mo: f"[{mo.group(1)}]({_normalize_download_url(mo.group(2))})",
                            collected_content,
                        )
                        # The model sometimes re-pastes the generated source as a
                        # trailing code block; drop it so the answer stays clean.
                        collected_content = _strip_trailing_source_dump(collected_content)
                        # `download_entries` uses an `operator.add` reducer, so it
                        # ACCUMULATES across tool rounds instead of being overwritten.
                        # `_build_download_entries` only de-dupes within a single
                        # round, so the same file returned by multiple tool calls/rounds
                        # ends up duplicated here — which would render as multiple
                        # identical download buttons. De-dupe the final list by url.
                        seen_urls: set[str] = set()
                        deduped_dl: list[dict] = []
                        for _e in dl_entries:
                            _u = _e.get("url")
                            if _u in seen_urls:
                                continue
                            seen_urls.add(_u)
                            deduped_dl.append(_e)

                        for e in deduped_dl:
                            # Live UI update (SSE) + persisted trace (agent_steps),
                            # so the download button also survives a page refresh.
                            emit_agent_step(
                                "file_done", e["filename"],
                                url=e["url"], filename=e["filename"], path=e["path"],
                            )
                            state.setdefault("agent_steps", []).append({
                                "stage": "file_done",
                                "message": e["filename"],
                                "extra": {
                                    "url": e["url"],
                                    "filename": e["filename"],
                                    "path": e["path"],
                                },
                            })

                    collected_citations = state.get("citations", [])
                    for c in collected_citations:
                        enqueue("citation", {"citation": c})

                    # Background: cache + memory
                    asyncio.create_task(_store_memory_and_cache(
                        query=request.query,
                        answer=collected_content,
                        kb_id=request.kb_id,
                        conversation_id=conv_id,
                        user_id=current_user.id,
                        citations=collected_citations,
                        skill_id=request.skill_id or (state.get("active_skill") or {}).get("id", ""),
                        kb_prompt=kb_prompt,
                    ))

                    assistant_msg = await _save_assistant_message(
                        conv_id,
                        collected_content,
                        collected_citations,
                        cache_hit=False,
                        retrieval_ms=final_retr,
                        msg_id=stream_msg_id["id"],
                        prompt_tokens=prompt_tokens,
                        status="complete",
                    )
                    await _persist_agent_steps(conv_id, assistant_msg.id, list(stream_agent_steps))
                    # Drop any dangling 'generating' bubbles left by suspended
                    # rounds earlier in this conversation.
                    await _cleanup_orphan_messages(conv_id, keep_id=assistant_msg.id)
                    cursor, total_msgs = await _read_context_cursor(conv_id)
                    enqueue("done", {
                        "conversation_id": conv_id,
                        "message_id": assistant_msg.id,
                        "cache_hit": False,
                        "ttft_ms": 0,
                        "retrieval_ms": final_retr,
                        "llm_ms": 0,
                        "prompt_tokens": prompt_tokens,
                        "persistent_tokens": breakdown.get("persistent_tokens", 0),
                        "transient_tokens": breakdown.get("transient_tokens", prompt_tokens),
                        "summary_msg_seq": cursor,
                        "total_messages": total_msgs,
                    })
                    # Run finished successfully without requesting a new suspension:
                    # drop any leftover pending-limit snapshot from a previous round so
                    # a page refresh does not resurrect a stale "continue/stop" bubble.
                    if cleared_pending is not None:
                        try:
                            await _clear_pending_state(db, conv_id)
                        except Exception:
                            pass
                    # Any suspension placeholder rows left dangling from earlier rounds
                    # (status=None) are now inert -- mark them resolved so they stop
                    # being mistaken for an active "continue/stop" bubble on reload.
                    try:
                        await _resolve_suspension_messages(conv_id, keep_id=assistant_msg.id)
                    except Exception:
                        pass

            except asyncio.CancelledError:
                # Client disconnected or cancelled the queue request.
                # The limiter context manager releases the token / removes us from queue.
                raise
            except Exception as e:
                import traceback
                traceback.print_exc()
                # Persist the partial turn onto the pre-created assistant message so a
                # page-refresh / mid-stream disconnect can still recover it. Steps that
                # were already emitted are flushed via stream_agent_steps (independent
                # of `state`, so this works even if the failure happened before the
                # agent graph built its state).
                mid = stream_msg_id["id"]
                try:
                    partial_content = collected_content
                except NameError:
                    partial_content = ""
                try:
                    partial_citations = collected_citations
                except NameError:
                    partial_citations = []
                try:
                    if mid:
                        err_msg = await _save_assistant_message(
                            conv_id,
                            partial_content or "",
                            partial_citations or [],
                            cache_hit=False,
                            msg_id=mid,
                            status="error",
                        )
                        await _persist_agent_steps(conv_id, mid, list(stream_agent_steps))
                except Exception as persist_err:
                    logger.warning("Failed to persist errored turn: %s", persist_err)
                # If this failed run was a resume/stop that already cleared the prior
                # suspension snapshot, the crash means no new suspension/final answer was
                # persisted — leaving the conversation in a state where a refresh finds
                # nothing to restore (the inline "continue/stop" bubble is gone forever).
                # Re-store the snapshot we cleared so the user can simply retry.
                if cleared_pending is not None and cleared_pending_msg_id is not None:
                    try:
                        await _save_pending_state(db, conv_id, cleared_pending_msg_id, cleared_pending)
                    except Exception as restore_err:
                        logger.warning("Failed to restore cleared pending state after crash: %s", restore_err)
                enqueue("error", {"message": _classify_llm_error(e)})
            finally:
                # Signal every subscriber (original client + any re-attaching clients)
                # that the stream is over, then drop the run from the registry so a
                # later refresh reloads from the DB instead of trying to re-attach.
                for q in list(handle.subscribers):
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass
                handle.done.set()
                RUN_REGISTRY.pop(conv_id, None)
                await sse_queue.put(None)

        producer_task = asyncio.create_task(producer())
        # Publish the task on the handle so _abort_run can cancel it (deleting a
        # conversation mid-stream). Without this the registry knows a run exists
        # but has no way to stop it.
        handle.producer_task = producer_task
        try:
            while True:
                event = await sse_queue.get()
                if event is None:
                    break
                yield event
        finally:
            # Do NOT cancel the producer when the client disconnects (e.g. the
            # user navigates away mid-stream). Cancelling abandons the in-flight
            # generation, so the assistant message + agent_steps are never
            # persisted and the conversation reloads blank. Instead, let the
            # producer run to completion on its own -- it opens its own DB
            # sessions for persistence, so the writes succeed regardless of the
            # request lifecycle. Swallow only non-cancellation errors so a
            # finished/errored producer never crashes the already-closed stream.
            #
            # The one exception is a DELIBERATE abort (_abort_run, i.e. the
            # conversation is being deleted): there the producer was cancelled on
            # purpose and ending this stream quietly is the correct response. A
            # cancellation of THIS task still propagates.
            try:
                await producer_task
            except asyncio.CancelledError:
                if not (handle.aborted and producer_task.cancelled()):
                    raise
            except Exception:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- Conversation Management ----

@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List conversations: filter by user_id. Only super admin can view any user via param."""
    user_id_filter = request.query_params.get("user_id") or current_user.id
    # Only super admin can view other users' conversations
    if user_id_filter != current_user.id and current_user.role.value != "admin":
        user_id_filter = current_user.id
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id_filter)
        .order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()

    responses = []
    for c in convs:
        count_result = await db.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == c.id)
        )
        count = count_result.scalar() or 0
        responses.append(ConversationResponse(
            id=c.id,
            title=c.title,
            kb_id=c.kb_id,
            created_at=c.created_at,
            updated_at=c.updated_at,
            message_count=count,
        ))
    return responses


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
async def get_conversation(
    conv_id: str,
    include_messages: bool = True,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get conversation metadata.

    Pass include_messages=false to skip loading messages (use the paginated
    /messages endpoint instead) — avoids transferring the entire history.
    """
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # Verify ownership
    if conv.user_id and conv.user_id != current_user.id and current_user.role.value != "admin":
        raise HTTPException(403, "无权访问")

    messages_list = []
    if include_messages:
        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.seq.asc())
        )
        messages_list = msg_result.scalars().all()
        total_messages = len(messages_list)
    else:
        total_result = await db.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conv_id)
        )
        total_messages = total_result.scalar() or 0

    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        kb_id=conv.kb_id,
        user_id=conv.user_id,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages_list,
        summary_text=conv.summary_text or "",
        summary_msg_seq=getattr(conv, "summary_msg_seq", 0) or 0,
        total_messages=total_messages,
        summary_archived_count=getattr(conv, "summary_archived_count", 0) or 0,
        min_compact_tok=segment_thresholds(config_manager.context_window)[0],
    )


@router.get("/conversations/{conv_id}/messages", response_model=ConversationMessagesPage)
async def get_conversation_messages(
    conv_id: str,
    page: str = "1",
    page_size: int = 10,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Server-side paginated messages, paginated by rounds (one Q&A = one round = 2 messages).

    page is 1-based, oldest first. Pass page=last to fetch the newest page.
    Rounds are kept intact at page boundaries so a Q&A pair is never split.
    """
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if conv.user_id and conv.user_id != current_user.id and current_user.role.value != "admin":
        raise HTTPException(403, "无权访问")

    total_result = await db.execute(
        select(func.count()).select_from(Message).where(Message.conversation_id == conv_id)
    )
    total_messages = total_result.scalar() or 0
    total_rounds = (total_messages + 1) // 2
    total_pages = max(1, (total_rounds + page_size - 1) // page_size)

    # Resolve requested page (support "last")
    if page.strip().lower() == "last":
        page_num = total_pages
    else:
        try:
            page_num = int(page)
        except ValueError:
            page_num = 1
    if page_num > total_pages:
        page_num = total_pages
    if page_num < 1:
        page_num = 1

    start = (page_num - 1) * page_size * 2
    end = min(total_messages, page_num * page_size * 2)

    msgs = []
    if start < end:
        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.seq.asc())
            .options(selectinload(Message.agent_steps))
            .offset(start)
            .limit(end - start)
        )
        msgs = msg_result.scalars().all()

    return ConversationMessagesPage(
        conversation_id=conv_id,
        page=page_num,
        page_size=page_size,
        total_rounds=total_rounds,
        total_pages=total_pages,
        total_messages=total_messages,
        has_more=page_num > 1,
        messages=msgs,
    )


@router.get("/conversations/{conv_id}/pending", response_model=PendingLimitResponse | None)
async def get_pending_limit(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the durable pending-limit pause for a conversation, or null.

    Used by the frontend after a page refresh to restore the inline resume
    bubble (the agent snapshot itself lives in the DB, not in the client).
    """
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if conv.user_id and conv.user_id != current_user.id and current_user.role.value != "admin":
        raise HTTPException(403, "无权访问")

    pending = await _load_pending_state(db, conv_id)
    if not pending:
        return None
    pl = pending.get("pending_limit") or {}
    return PendingLimitResponse(
        conversation_id=conv_id,
        message_id=pending.get("pending_msg_id") or "",
        message=pl.get("message", ""),
        kind=pl.get("kind", ""),
    )


class ConversationRunStatus(BaseModel):
    """Refresh-time snapshot the frontend uses to decide what to render.

    ``running`` means a generation is currently in flight in this process — the
    frontend should re-attach to the SSE stream, NOT show the pause bubble.
    ``pending`` is only populated when the run is NOT running (a durable pause),
    so a refreshing client never shows both a live stream and a stale resume UI.
    """

    running: bool
    pending: PendingLimitResponse | None = None


@router.get("/conversations/{conv_id}/status", response_model=ConversationRunStatus)
async def get_conversation_status(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tell a freshly-loaded (possibly refreshed) frontend what state the conversation is in."""
    conv = await _load_owned_conversation(conv_id, current_user, db)
    if conv is None:
        raise HTTPException(404, "Conversation not found")

    # A run is "in flight" ONLY if there is a live RunHandle in the registry for
    # this conversation. Relying on the message status ("generating") is racy: a
    # run that has just finished (e.g. hit the tool-round limit and suspended) may
    # still leave the message flagged "generating" for a brief window, which would
    # make a refreshing client wrongly try to re-attach and then receive `run_gone`.
    # The registry entry is removed in the producer's finally block on completion,
    # so its presence is the authoritative signal that a run is genuinely streaming.
    handle = RUN_REGISTRY.get(conv_id)
    running = bool(handle and not handle.done.is_set())

    pending = None
    if not running:
        pending_row = await _load_pending_state(db, conv_id)
        if pending_row:
            pl = pending_row.get("pending_limit") or {}
            pending = PendingLimitResponse(
                conversation_id=conv_id,
                message_id=pending_row.get("pending_msg_id") or "",
                message=pl.get("message", ""),
                kind=pl.get("kind", ""),
            )
    return ConversationRunStatus(running=running, pending=pending)


async def _load_owned_conversation(conv_id: str, current_user: User, db: AsyncSession) -> Conversation:
    """Fetch a conversation, enforcing the owner-or-admin rule.

    Mirrors the check used by ``delete_conversation``: legacy rows with no
    ``user_id`` stay accessible, otherwise only the owner (or an admin) may
    touch the row.
    """
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if conv.user_id and conv.user_id != current_user.id and current_user.role.value != "admin":
        raise HTTPException(403, "无权访问")
    return conv


async def _summary_state(conv: Conversation, db: AsyncSession) -> ConversationSummaryState:
    total_result = await db.execute(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conv.id)
    )
    return ConversationSummaryState(
        conversation_id=conv.id,
        summary_text=conv.summary_text or "",
        summary_msg_seq=getattr(conv, "summary_msg_seq", 0) or 0,
        total_messages=total_result.scalar() or 0,
        summary_archived_count=getattr(conv, "summary_archived_count", 0) or 0,
        min_compact_tok=segment_thresholds(config_manager.context_window)[0],
    )


@router.put("/conversations/{conv_id}/summary", response_model=ConversationSummaryState)
async def update_conversation_summary(
    conv_id: str,
    payload: SummaryUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace the compressed summary text of a conversation.

    The folding cursor (``summary_msg_seq``) is deliberately left untouched:
    it records which raw messages are already represented by the summary, and
    rewinding it would either duplicate content or permanently hide messages
    from the model. Clearing the text therefore makes ``history[:cursor]``
    invisible -- the frontend must double-confirm that case.
    """
    conv = await _load_owned_conversation(conv_id, current_user, db)
    if await _load_pending_state(db, conv_id) is not None:
        raise HTTPException(409, "CONVERSATION_BUSY")
    conv.summary_text = payload.summary_text.strip()
    conv.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(conv)
    return await _summary_state(conv, db)


@router.delete("/conversations/{conv_id}/summary/segments", response_model=ConversationSummaryState)
async def delete_summary_segment(
    conv_id: str,
    payload: SummarySegmentDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove one fold segment from the compressed summary by content match.

    Segments are joined with SUMMARY_SEGMENT_DELIM; we drop the first segment
    whose (stripped) text equals ``payload.segment_text``. The folding cursor
    (``summary_msg_seq``) is deliberately left untouched: rewinding it would
    either duplicate content or permanently hide messages from the model.

    A delete only shrinks the persistent summary, so it can never overflow.
    While the conversation is busy (streaming / compacting) the mutation is
    rejected with 409 CONVERSATION_BUSY.
    """
    if not payload.segment_text.strip():
        raise HTTPException(400, "EMPTY_SEGMENT")
    conv = await _load_owned_conversation(conv_id, current_user, db)
    if await _load_pending_state(db, conv_id) is not None:
        raise HTTPException(409, "CONVERSATION_BUSY")

    segs = (conv.summary_text or "").split(SUMMARY_SEGMENT_DELIM)
    stripped = payload.segment_text.strip()
    target_idx = next((i for i, s in enumerate(segs) if s.strip() == stripped), None)
    if target_idx is None:
        raise HTTPException(404, "SEGMENT_NOT_FOUND")

    segs.pop(target_idx)
    conv.summary_text = SUMMARY_SEGMENT_DELIM.join(segs)
    conv.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(conv)
    return await _summary_state(conv, db)


@router.get("/conversations/{conv_id}/pin")
async def get_pin_instruction(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the conversation's pinned instruction (empty string when unset)."""
    conv = await _load_owned_conversation(conv_id, current_user, db)
    return {"pinned_instruction": conv.pinned_instruction or ""}


@router.put("/conversations/{conv_id}/pin")
async def put_pin_instruction(
    conv_id: str,
    payload: PinInstructionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or clear a conversation's pinned instruction.

    The pin is injected into every turn's system prefix (a sacred, non-trimmable
    block) and is never folded into summary_text. Mutating it while the
    conversation is busy (streaming / compacting) is rejected with 409
    CONVERSATION_BUSY; an excessively long pin is rejected with 400
    PIN_INSTRUCTION_TOO_LONG.

    The character cap above is an abuse guard, independent of the returned
    ``warnings``: those are the non-blocking config-time budget check (the pin
    rides in the prefix on every turn, so its token mass is worth surfacing at
    edit time even when it is well under the character cap).
    """
    value = payload.pinned_instruction or ""
    if len(value) > PIN_INSTRUCTION_MAX_CHARS:
        raise HTTPException(400, "PIN_INSTRUCTION_TOO_LONG")
    conv = await _load_owned_conversation(conv_id, current_user, db)
    if await _load_pending_state(db, conv_id) is not None:
        raise HTTPException(409, "CONVERSATION_BUSY")
    conv.pinned_instruction = value.strip() or None
    conv.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(conv)
    return {
        "pinned_instruction": conv.pinned_instruction or "",
        "warnings": check_field_budget(conv.pinned_instruction, "pinned_instruction"),
    }


@router.post("/conversations/{conv_id}/compact", response_model=ConversationSummaryState)
async def compact_conversation_endpoint(
    conv_id: str,
    payload: CompactRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually fold the oldest part of the un-summarized history into the summary.

    Unconditional (ignores the token budget) because it is user-initiated. The
    compaction is atomic: if the summarization LLM call fails the cursor is not
    advanced, so no message can ever be lost from the model's view.
    """
    conv = await _load_owned_conversation(conv_id, current_user, db)
    if await _load_pending_state(db, conv_id) is not None:
        raise HTTPException(409, "CONVERSATION_BUSY")

    # Load ONLY the un-summarized tail (seq >= cursor) from the per-conversation
    # cache (warm hits refresh only appended rows; cold misses query seq>=cursor
    # once). Trim to the exact un-summarized tail in case the cache also holds
    # already-folded messages from an earlier cursor.
    cursor = getattr(conv, "summary_msg_seq", 0) or 0
    tail = _tail_from(await _load_history(conv_id, db, cursor), cursor)

    try:
        await compact_conversation(
            conv,
            tail,
            db,
            config_manager.prompt_language,
        )
    except CompactionError as e:
        await db.rollback()
        raise HTTPException(400, str(e))

    await db.refresh(conv)
    return await _summary_state(conv, db)


@router.delete("/conversations/{conv_id}", status_code=202)
async def delete_conversation(conv_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Delete a conversation. Only the owner can delete.

    Accepted-and-purging, not deleted-and-done. Only the work that must be
    correct the instant the response returns happens inline:

      1. abort any in-flight generation, so nothing writes into the conversation
         after its row is gone;
      2. drop the in-memory state that would keep serving it (history cache,
         has-memory flag);
      3. delete the ``conversations`` row itself -- a single indexed DELETE, so
         the conversation disappears from every listing immediately.

    The expensive half (thousands of ``messages`` / ``agent_steps`` rows, the
    Chroma collection) is handed to ``conversation_purge``, which drains it in
    throttled batches. Deliberately NOT ``db.delete(conv)``: the ORM cascade
    loads every child row into memory and issues per-row DELETEs inside this
    request, holding SQLite's writer lock long enough to stall live chat turns.
    """
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "CONVERSATION_NOT_FOUND")
    # A conversation is private to its owner -- admins included. Legacy rows with
    # no user_id stay deletable by anyone (same rule as the rest of this router).
    if conv.user_id and conv.user_id != current_user.id:
        raise HTTPException(403, "CONVERSATION_FORBIDDEN")

    # 1) Stop any live run BEFORE the row goes away. Left running, the producer
    #    would finish and persist its assistant message into a dead conversation.
    aborted = await _abort_run(conv_id)

    # 2) In-memory state. Both are cheap and must be gone before the next turn of
    #    any other conversation can observe them.
    _evict_history_cache(conv_id)
    memory_archive.unmark_has_memory(conv_id)

    # 3) The only DB write on the request path.
    await db.execute(delete(Conversation).where(Conversation.id == conv_id))
    await db.commit()

    # 4) Everything heavy, off the request path.
    conversation_purge.schedule_purge(conv_id)
    return {"status": "deleting", "aborted_run": aborted}


# ── Background helpers ──

async def _store_memory_and_cache(
    query: str, answer: str, kb_id: str, conversation_id: str, user_id: str,
    citations: list[dict], skill_id: str, kb_prompt: str = "",
):
    """Background task: store the answer cache after a completed turn."""
    try:
        answer_cache.put(query, kb_id, answer, citations, skill_id=skill_id, kb_prompt=kb_prompt)
    except Exception:
        pass


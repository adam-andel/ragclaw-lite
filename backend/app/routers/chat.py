"""Chat API routes with SSE streaming."""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
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
from app.services.agent_nodes import MAX_SKILL_SWITCHES, MAX_TOOL_ROUNDS, _strip_tool_call_noise, _normalize_download_url
from app.services.kb_service import get_kb_prompt
from app.services.token_count import count_messages_tokens
from app.services.config_manager import config_manager
from app.services.conversation_summary import build_context_with_summary
from app.services.llm_semaphore import llm_limiter
from app.services.cron_parser import try_parse_cron_payload
from app.services.cron_graph import run_cron_creation_subgraph
from app.schemas.chat import (
    ChatRequest,
    ConversationResponse,
    ConversationDetail,
    ConversationMessagesPage,
    PendingLimitResponse,
)

router = APIRouter(prefix="/api", tags=["Chat"])

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
            created_at=datetime.utcnow(),
        )

        session.add(assistant_msg)
        conv = await session.get(Conversation, conv_id)
        if conv:
            conv.updated_at = datetime.utcnow()
        await session.commit()

    return assistant_msg


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
        "memory_context": state.get("memory_context"),
        "tool_results": state.get("tool_results"),
        "tool_messages": state.get("tool_messages"),
        "skill_stack": state.get("skill_stack"),
        "loaded_skill_ids": state.get("loaded_skill_ids"),
        "workspace_id": state.get("workspace_id"),
        "skill_switch_count": state.get("skill_switch_count"),
        "tool_round": state.get("tool_round"),
        "skill_switch_quota": state.get("skill_switch_quota"),
        "tool_round_quota": state.get("tool_round_quota"),
        "pending_limit": state.get("pending_limit"),
    }


def _build_resume_initial_state(pending, mode, current_user, history, kb_prompt, request, emit_fn, conv_id, summary_text: str = "", summary_msg_count: int = 0) -> dict:
    """Rebuild initial_state from the snapshot: history is left untouched; only recharge the quota (continue) or clear tool_calls (stop).

    The accumulated conversation summary (if any) is re-injected, and the already
    summarized prefix of the history is dropped to avoid duplication with the summary.
    """
    pl = pending.get("pending_limit") or {}
    if mode == "continue":
        quota_ss = pending["skill_switch_quota"] + MAX_SKILL_SWITCHES
        quota_tr = pending["tool_round_quota"] + config_manager.agent_round_quota
        tool_calls = pl.get("deferred_tool_call")
        resume_action = "continue"
    else:  # stop
        quota_ss = pending["skill_switch_quota"]
        quota_tr = pending["tool_round_quota"]
        tool_calls = None
        resume_action = "stop"
    # Skip the earliest messages already captured in the summary.
    recent_history = history[summary_msg_count:] if summary_msg_count else history
    return {
        "query": pending.get("query") or request.query,
        "kb_id": request.kb_id,
        "skill_id": request.skill_id,
        "user_id": current_user.id,
        "tenant_id": current_user.tenant_id,
        "conversation_history": recent_history,
        "conversation_summary": summary_text,
        "conversation_id": conv_id,
        "workspace_id": pending["workspace_id"],
        "active_skill": pending["active_skill"],
        "available_tools": pending["available_tools"],
        "rag_context": pending["rag_context"],
        "citations": pending["citations"],
        "memory_context": pending["memory_context"],
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
        "emit": emit_fn,
    }


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
        data: {"type": "done", "conversation_id": "...", "message_id": "...", "cache_hit": ...}
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

    # Resume (continue/stop) may carry an empty body.query because the real
    # query is persisted in the suspension snapshot. A genuine new question
    # must carry a non-empty query.
    is_resume = request.resume_action in ("continue", "stop")
    if not is_resume and not request.query:
        raise HTTPException(status_code=422, detail="query 不能为空")

    # Build conversation history (ALL messages — no per-turn cap). The full
    # history is loaded from the DB; compression (conversation_summary.py) decides
    # at request time whether the oldest part must be summarized to fit the context
    # window. Raw messages are never truncated here.
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
    )
    history_msgs = result.scalars().all()
    history = [{"role": m.role, "content": m.content} for m in history_msgs]

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
            created_at=datetime.utcnow(),
        )
        db.add(user_msg)

    # Update conversation timestamp
    conv.updated_at = datetime.utcnow()
    await db.commit()

    # Streaming response
    async def generate():
        sse_queue: asyncio.Queue[str | None] = asyncio.Queue()

        def enqueue(event_type: str, payload: dict) -> None:
            sse_queue.put_nowait(_sse(event_type, payload))

        async def on_queue_position(pos: int) -> None:
            await sse_queue.put(_sse("queue", {"position": pos}))

        def emit_agent_step(stage: str, message: str, **extra) -> None:
            """Stream an agent_step progress event (Route D observability)."""
            enqueue("agent_step", {"stage": stage, "message": message, **extra})

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
                if pending is not None:
                    pending_msg_id = pending.get("pending_msg_id")
                    if request.resume_action == "continue":
                        resume_mode = "continue"
                        await _clear_pending_state(db, conv_id)
                    elif request.resume_action == "stop":
                        resume_mode = "stop"
                        await _clear_pending_state(db, conv_id)
                    else:
                       # User sends a new question (not continue/stop): treat as stop, discard the suspension, and answer the new question normally
                        await _clear_pending_state(db, conv_id)
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
                            enqueue("done", {
                                "conversation_id": conv_id,
                                "message_id": assistant_msg.id,
                                "cache_hit": True,
                                "ttft_ms": 0,
                                "retrieval_ms": 0,
                                "llm_ms": 0,
                            })
                            return

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

                    # Compress oldest history if it would overflow the context window.
                    # Returns (recent_messages, summary_text); summary is persisted on
                    # the conversation and injected as a system message downstream. Raw
                    # messages are never modified.
                    recent_history, summary_text = await build_context_with_summary(
                        conv, history, db, config_manager.prompt_language, expanded_query
                    )

                    initial_state = {
                        "query": expanded_query,
                        "kb_id": request.kb_id,
                        "skill_id": request.skill_id,
                        "user_id": current_user.id,
                        "tenant_id": current_user.tenant_id,
                        "conversation_history": recent_history,
                        "conversation_summary": summary_text,
                        "conversation_id": conv_id,
                        # v2: user-selected workspace sub-directory ("" = root).
                        # Replaces the old per-conversation <ws> (conv_id) so all of a
                        # user's tool outputs land in their persistent workspace root.
                        "workspace_id": request.workspace_dir or "",
                        "active_skill": None,
                        "available_tools": [],
                        "rag_context": "",
                        "citations": [],
                        "memory_context": "",
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
                        "skill_switch_quota": MAX_SKILL_SWITCHES,
                        "tool_round_quota": config_manager.agent_round_quota,
                        "pending_limit": None,
                        "resume_action": None,
                        "agent_steps": [],
                        "emit": emit_agent_step,
                    }
                else:
                   # ── 1b. Resume: rebuild from snapshot, history untouched, only recharge / clear ───
                    initial_state = _build_resume_initial_state(
                        pending, resume_mode, current_user, history, kb_prompt, request, emit_agent_step, conv_id,
                        summary_text=conv.summary_text or "",
                        summary_msg_count=getattr(conv, "summary_msg_count", 0) or 0,
                    )

               # ── 1c. User manually stops: do not replay tools, do not generate an answer,，
               #        only persist the original suspension hint, and let the frontend overlay a localized termination notice via done.stopped ───
                if resume_mode == "stop":
                    plim = pending.get("pending_limit") or {}
                    base_msg = plim.get("message") or ""
                    assistant_msg = await _save_assistant_message(
                        conv_id,
                        base_msg,
                        pending.get("citations", []),
                        cache_hit=False,
                        msg_id=pending_msg_id,
                        status="stopped",
                    )
                    enqueue("done", {
                        "conversation_id": conv_id,
                        "message_id": assistant_msg.id,
                        "cache_hit": False,
                        "ttft_ms": 0,
                        "retrieval_ms": 0,
                        "llm_ms": 0,
                        "stopped": True,
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
                       # First persist the suspension hint as an assistant message and record its id so it can be replaced in place after the reply
                        pending_msg = await _save_assistant_message(
                            conv_id,
                            state["pending_limit"]["message"],
                            state.get("citations", []),
                            cache_hit=False,
                        )
                        await _persist_agent_steps(conv_id, pending_msg.id, state.get("agent_steps") or [])
                        snap = _snapshot_state(state)
                        snap["pending_msg_id"] = pending_msg.id
                        await _save_pending_state(db, conv_id, pending_msg.id, snap)
                        enqueue("need_user_input", {
                            "message": state["pending_limit"]["message"],
                            "conv_id": conv_id,
                            "kind": state["pending_limit"]["kind"],
                            "message_id": pending_msg.id,
                        })
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
                        enqueue("done", {
                            "conversation_id": conv_id,
                            "message_id": assistant_msg.id,
                            "cache_hit": True,
                            "ttft_ms": 0,
                            "retrieval_ms": 0,
                            "llm_ms": 0,
                        })
                        return

                    # ── 3. Stream LLM generation ──
                    final_retr = state.get("retrieval_ms", 0)
                    messages = ragclaw_agent_graph.build_generation_messages(state)
                    # Approximate total tokens of the request payload sent to the LLM.
                    prompt_tokens = count_messages_tokens(messages)
                    # Signal the final-generation phase so the frontend can show it honestly
                    # (the graph handles everything up to here; the actual LLM stream starts now).
                    emit_agent_step("generating", "Generating answer…")
                    collected_content = ""
                    collected_citations = []
                    _stream_buf = ""  # holds back [TOOL_CALL] spans from live display

                    async for token in llm_client.chat_stream(messages, conversation_id=conv_id):
                        collected_content += token
                        _stream_buf += token
                        emit_text, _stream_buf = _suppress_tool_call_span(_stream_buf)
                        if emit_text:
                            enqueue("token", {"content": emit_text})

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
                        for e in dl_entries:
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

                    # Detect and persist cron jobs created via natural language.
                    cron_payload = try_parse_cron_payload(collected_content)
                    if cron_payload:
                        confirmation = await run_cron_creation_subgraph(
                            payload=cron_payload,
                            user_id=current_user.id,
                            tenant_id=current_user.tenant_id,
                            kb_id=request.kb_id or None,
                            skill_id=request.skill_id or (state.get("active_skill") or {}).get("id", None),
                            user_timezone=request.timezone,
                            workspace_dir=request.workspace_dir or None,
                        )
                        collected_content = confirmation
                        # Re-emit the confirmation as a single token event.
                        enqueue("token", {"content": "\n\n" + collected_content})

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
                        msg_id=pending_msg_id if resume_mode is not None else None,
                        prompt_tokens=prompt_tokens,
                    )
                    await _persist_agent_steps(conv_id, assistant_msg.id, state.get("agent_steps") or [])
                    enqueue("done", {
                        "conversation_id": conv_id,
                        "message_id": assistant_msg.id,
                        "cache_hit": False,
                        "ttft_ms": 0,
                        "retrieval_ms": final_retr,
                        "llm_ms": 0,
                        "prompt_tokens": prompt_tokens,
                    })

            except asyncio.CancelledError:
                # Client disconnected or cancelled the queue request.
                # The limiter context manager releases the token / removes us from queue.
                raise
            except Exception as e:
                import traceback
                traceback.print_exc()
                enqueue("error", {"message": str(e)})
            finally:
                await sse_queue.put(None)

        producer_task = asyncio.create_task(producer())
        try:
            while True:
                event = await sse_queue.get()
                if event is None:
                    break
                yield event
        finally:
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
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
            .order_by(Message.created_at.asc())
        )
        messages_list = msg_result.scalars().all()

    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        kb_id=conv.kb_id,
        user_id=conv.user_id,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages_list,
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
            .order_by(Message.created_at.asc())
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


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Delete a conversation. Only the owner (or admin) can delete."""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # Only owner or admin can delete
    if conv.user_id and conv.user_id != current_user.id and current_user.role.value != "admin":
        raise HTTPException(403, "无权删除")
    # Admin can only delete their own conversations
    if conv.user_id and conv.user_id != current_user.id:
        raise HTTPException(403, "管理员只能删除自己的对话")
    # Drop any persisted pending-limit snapshot for this conversation.
    await _clear_pending_state(db, conv_id)
    await db.delete(conv)
    await db.commit()
    return {"status": "deleted"}


# ── Background helpers ──

async def _store_memory_and_cache(
    query: str, answer: str, kb_id: str, conversation_id: str, user_id: str,
    citations: list[dict], skill_id: str, kb_prompt: str = "",
):
    """Background task: store answer cache + Mem0 memory."""
    try:
        answer_cache.put(query, kb_id, answer, citations, skill_id=skill_id, kb_prompt=kb_prompt)
    except Exception:
        pass

    if user_id and answer:
        try:
            from app.services.memory import add_memory
            import json as _json
            # Structured format for better Mem0 extraction
            memory_text = _json.dumps({
                "type": "qa",
                "query": query[:settings.mem0_query_max_chars],
                "answer": answer[:settings.mem0_answer_max_chars],
                "kb_id": kb_id,
                "skill_id": skill_id or "",
            }, ensure_ascii=False)
            await add_memory(
                memory_text,
                user_id=user_id,
                metadata={"kb_id": kb_id, "skill_id": skill_id},
            )
        except Exception:
            pass


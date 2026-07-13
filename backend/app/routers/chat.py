"""Chat API routes with SSE streaming."""

import asyncio
import json
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
from app.models.conversation import Conversation, Message, PendingLimitState
from app.models.document import Document, Chunk
from app.services.auth import get_current_user
from app.services.cache import answer_cache
from app.services.agent_nodes import MAX_SKILL_SWITCHES, MAX_TOOL_ROUNDS
from app.services.kb_service import get_kb_prompt
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


def _sse(event_type: str, payload: dict) -> str:
    """Format a single SSE data line."""
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


async def _save_assistant_message(
    conv_id: str,
    content: str,
    citations: list[dict],
    cache_hit: bool,
    retrieval_ms: int = 0,
    msg_id: str | None = None,
    status: str | None = None,
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
            created_at=datetime.utcnow(),
        )

        session.add(assistant_msg)
        conv = await session.get(Conversation, conv_id)
        if conv:
            conv.updated_at = datetime.utcnow()
        await session.commit()

    return assistant_msg


# ── 挂起快照持久化（DB）──
# 把 Human-in-the-Loop 的纯数据快照落库，刷新 / 进程重启后仍可恢复。
# 这些助手复用调用方的 session，便于测试时用被覆盖的测试 session。

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


# 方案B：手动挂起/恢复状态仓库。
# 持久化到 DB（pending_limit_states 表），刷新 / 重启后仍可恢复，支持多 worker。
# 仅在内存中保留瞬时运行期对象（由 _snapshot_state 剔除），快照本体落库。


def _snapshot_state(state: dict) -> dict:
    """保存挂起所需的纯数据快照（emit 等运行期对象不存）。"""
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


def _build_resume_initial_state(pending, mode, current_user, history, kb_prompt, request, emit_fn, conv_id) -> dict:
    """从快照重建 initial_state：历史一概不动，仅充值限额（continue）或置空 tool_calls（stop）。"""
    pl = pending.get("pending_limit") or {}
    if mode == "continue":
        quota_ss = pending["skill_switch_quota"] + MAX_SKILL_SWITCHES
        quota_tr = pending["tool_round_quota"] + MAX_TOOL_ROUNDS
        tool_calls = pl.get("deferred_tool_call")
        resume_action = "continue"
    else:  # stop
        quota_ss = pending["skill_switch_quota"]
        quota_tr = pending["tool_round_quota"]
        tool_calls = None
        resume_action = "stop"
    return {
        "query": pending.get("query") or request.query,
        "kb_id": request.kb_id,
        "skill_id": request.skill_id,
        "user_id": current_user.id,
        "tenant_id": current_user.tenant_id,
        "conversation_history": history,
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

    # Build conversation history (last N messages)
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
        .limit(20)
    )
    history_msgs = result.scalars().all()
    history = [{"role": m.role, "content": m.content} for m in history_msgs]

    # Fetch the KB's instruction prompt once; reuse for cache key + system prompt.
    kb_prompt = await get_kb_prompt(request.kb_id)

    # Save user message
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
                from app.services.agent_graph import erag_agent_graph
                from app.services.llm_client import llm_client
                from app.services.agent_nodes import _extract_download_links_from_state

                # ── 0. 挂起分诊：本会话是否有待用户确认的限额挂起（持久化快照）──
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
                        # 用户发送新问题（非继续/停止）：视为停止，丢弃挂起，按新问题正常回答
                        await _clear_pending_state(db, conv_id)

                if resume_mode is None:
                    # ── 1. 正常新问题（含缓存）──
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

                    initial_state = {
                        "query": request.query,
                        "kb_id": request.kb_id,
                        "skill_id": request.skill_id,
                        "user_id": current_user.id,
                        "tenant_id": current_user.tenant_id,
                        "conversation_history": history,
                        "conversation_id": conv_id,
                        "workspace_id": conv_id or ("run-" + str(uuid.uuid4())),
                        "active_skill": None,
                        "available_tools": [],
                        "rag_context": "",
                        "citations": [],
                        "memory_context": "",
                        "tool_calls": None,
                        "tool_round": 0,
                        "tool_results": [],
                        "tool_messages": [],
                        "cache_hit": False,
                        "final_answer": "",
                        "retrieval_ms": 0,
                        "skip_cache": request.skip_cache,
                        "kb_prompt": kb_prompt,
                        "skill_switch_quota": MAX_SKILL_SWITCHES,
                        "tool_round_quota": MAX_TOOL_ROUNDS,
                        "pending_limit": None,
                        "resume_action": None,
                        "emit": emit_agent_step,
                    }
                else:
                    # ── 1b. 恢复：从快照重建，历史一概不动，只充值/置空 ──
                    initial_state = _build_resume_initial_state(
                        pending, resume_mode, current_user, history, kb_prompt, request, emit_agent_step, conv_id
                    )

                # ── 1c. 用户手动停止：不重放工具、不生成答案，
                #        仅把原挂起提示落库，并通过 done.stopped 让前端叠加本地化终止说明 ──
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

                # ── 2. 跑图 ──
                async with llm_limiter.acquire(on_queue_position):
                    # Token acquired: build state and run agent graph.
                    state = await erag_agent_graph.run(initial_state)

                    # ── 2b. 挂起检测：图请求用户确认 ──
                    if state.get("pending_limit"):
                        # 先把挂起提示作为一条 assistant 消息落库，记录其 id 以便答复后原地替换
                        pending_msg = await _save_assistant_message(
                            conv_id,
                            state["pending_limit"]["message"],
                            state.get("citations", []),
                            cache_hit=False,
                        )
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
                    messages = erag_agent_graph.build_generation_messages(state)
                    collected_content = ""
                    collected_citations = []

                    async for token in llm_client.chat_stream(messages, conversation_id=conv_id):
                        collected_content += token
                        enqueue("token", {"content": token})

                    # Inject download links from tool results
                    dl_links = _extract_download_links_from_state(state)
                    if dl_links:
                        collected_content += dl_links
                        enqueue("token", {"content": dl_links})

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
                    )
                    enqueue("done", {
                        "conversation_id": conv_id,
                        "message_id": assistant_msg.id,
                        "cache_hit": False,
                        "ttft_ms": 0,
                        "retrieval_ms": final_retr,
                        "llm_ms": 0,
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
    """Server-side paginated messages, paginated by rounds (一问一答为一轮 = 2 条消息).

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
                agent_id=kb_id,
                run_id=conversation_id,
            )
        except Exception:
            pass


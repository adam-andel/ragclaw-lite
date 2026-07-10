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
from app.database import get_db, async_session
from app.models.user import User
from app.models.conversation import Conversation, Message
from app.models.document import Document, Chunk
from app.services.auth import get_current_user
from app.services.cache import answer_cache
from app.services.kb_service import get_kb_prompt
from app.services.llm_semaphore import llm_limiter
from app.services.cron_parser import try_parse_cron_payload
from app.services.cron_graph import run_cron_creation_subgraph
from app.schemas.chat import (
    ChatRequest,
    ConversationResponse,
    ConversationDetail,
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
) -> Message:
    """Persist assistant message and update conversation timestamp."""
    assistant_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        role="assistant",
        content=content,
        citations=citations,
        cache_hit=cache_hit,
        ttft_ms=0,
        retrieval_ms=retrieval_ms,
        llm_ms=0,
        created_at=datetime.utcnow(),
    )

    async with async_session() as session:
        session.add(assistant_msg)
        conv = await session.get(Conversation, conv_id)
        if conv:
            conv.updated_at = datetime.utcnow()
        await session.commit()

    return assistant_msg


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

                # ── 1. Cache-first check (does not consume a token) ──
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

                # ── 2. Queue for a concurrency token ──
                async with llm_limiter.acquire(on_queue_position):
                    # Token acquired: build state and run agent graph.
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
                        "emit": emit_agent_step,
                    }

                    state = await erag_agent_graph.run(initial_state)

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

                    async for token in llm_client.chat_stream(messages):
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
    """List conversations: filter by user_id. Admin can view any user via param."""
    user_id_filter = request.query_params.get("user_id") or current_user.id
    # Only admin can view other users' conversations
    if user_id_filter != current_user.id and current_user.role.value not in ("admin", "moderator"):
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
async def get_conversation(conv_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get a conversation with all messages."""
    result = await db.execute(
        select(Conversation).options(selectinload(Conversation.messages))
        .where(Conversation.id == conv_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # Verify ownership
    if conv.user_id and conv.user_id != current_user.id and current_user.role.value not in ("admin", "moderator"):
        raise HTTPException(403, "无权访问")
    return conv


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
    await db.delete(conv)
    await db.commit()
    return {"status": "deleted"}


# ── Background helpers ──

async def _store_memory_and_cache(
    query: str, answer: str, kb_id: str, user_id: str,
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
                "query": query[:200],
                "answer": answer[:500],
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


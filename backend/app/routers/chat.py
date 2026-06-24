"""Chat API routes with SSE streaming."""

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models.user import User
from app.models.conversation import Conversation, Message
from app.models.document import Document, Chunk
from app.services.auth import get_current_user
from app.schemas.chat import (
    ChatRequest,
    ConversationResponse,
    ConversationDetail,
)

router = APIRouter(prefix="/api", tags=["Chat"])


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE streaming RAG chat endpoint.

    Events:
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
        collected_content = ""
        collected_citations = []
        cache_hit = False
        final_retr = 0

        try:
            from app.services.agent_graph import erag_agent_graph
            from app.services.llm_client import llm_client
            import asyncio as _asyncio

            # Build initial agent state
            initial_state = {
                "query": request.query,
                "kb_id": request.kb_id,
                "skill_id": request.skill_id,
                "user_id": current_user.id,
                "tenant_id": current_user.tenant_id,
                "conversation_history": history,
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
            }

            # Run agent graph (routing → retrieval → tool decision/execution)
            state = await erag_agent_graph.run(initial_state)

            if state.get("cache_hit"):
                # Cache hit: stream cached answer
                cache_hit = True
                collected_content = state["final_answer"]
                collected_citations = state.get("citations", [])
                yield f"data: {json.dumps({'type': 'token', 'content': collected_content}, ensure_ascii=False)}\n\n"
                for c in collected_citations:
                    yield f"data: {json.dumps({'type': 'citation', 'citation': c}, ensure_ascii=False)}\n\n"
            else:
                # Build messages from state and stream LLM generation
                final_retr = state.get("retrieval_ms", 0)
                messages = erag_agent_graph.build_generation_messages(state)

                async for token in llm_client.chat_stream(messages):
                    collected_content += token
                    yield f"data: {json.dumps({'type': 'token', 'content': token}, ensure_ascii=False)}\n\n"

                collected_citations = state.get("citations", [])
                for c in collected_citations:
                    yield f"data: {json.dumps({'type': 'citation', 'citation': c}, ensure_ascii=False)}\n\n"

                # Background: cache + memory
                _asyncio.create_task(_store_memory_and_cache(
                    query=request.query,
                    answer=collected_content,
                    kb_id=request.kb_id,
                    user_id=current_user.id,
                    citations=collected_citations,
                    skill_id=request.skill_id or (state.get("active_skill") or {}).get("id", ""),
                ))

            # Save assistant message
            assistant_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role="assistant",
                content=collected_content,
                citations=collected_citations,
                cache_hit=cache_hit,
                ttft_ms=0,
                retrieval_ms=final_retr,
                llm_ms=0,
                created_at=datetime.utcnow(),
            )

            async with async_session() as session:
                session.add(assistant_msg)
                conv = await session.get(Conversation, conv_id)
                if conv:
                    conv.updated_at = datetime.utcnow()
                await session.commit()

            yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'message_id': assistant_msg.id, 'cache_hit': cache_hit, 'ttft_ms': 0, 'retrieval_ms': final_retr, 'llm_ms': 0}, ensure_ascii=False)}\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

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
    citations: list[dict], skill_id: str,
):
    """Background task: store answer cache + Mem0 memory."""
    try:
        from app.services.cache import answer_cache
        answer_cache.put(query, kb_id, answer, citations, skill_id=skill_id)
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

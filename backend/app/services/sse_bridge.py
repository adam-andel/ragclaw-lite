"""SSE bridge — translates agent graph output to SSE events.

Usage from chat.py:

    from app.services.agent_graph import erag_agent_graph
    from app.services.sse_bridge import stream_agent_response
    from app.services.llm_client import llm_client

    async def generate():
        state = await erag_agent_graph.run(initial_state)

        if state["cache_hit"]:
            yield sse_event("token", state["final_answer"])
            for c in state.get("citations", []):
                yield sse_event("citation", c)
            yield sse_event("done", {"cache_hit": True})
        else:
            messages = erag_agent_graph.build_generation_messages(state)
            full_answer = ""
            async for token in llm_client.chat_stream(messages):
                full_answer += token
                yield sse_event("token", token)

            for c in state.get("citations", []):
                yield sse_event("citation", c)

            yield sse_event("done", {
                "cache_hit": False,
                "retrieval_ms": state.get("retrieval_ms", 0),
            })

            # Post-process in background
            asyncio.create_task(post_process(state, full_answer))
"""

import json
import asyncio
import logging
from datetime import datetime

from app.services.llm_client import llm_client
from app.services.cache import answer_cache

logger = logging.getLogger("erag.sse")


def sse_event(event_type: str, data=None) -> str:
    """Format a single SSE event."""
    if data is None:
        data = ""
    payload = data
    if isinstance(data, dict):
        payload = data
    elif isinstance(data, str):
        return f"data: {json.dumps({'type': event_type, 'content': data}, ensure_ascii=False)}\n\n"
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


async def run_agent_and_stream(state: dict):
    """Full agent pipeline: graph → LLM stream → SSE events.

    This is the main entry point called by chat.py.
    Yields SSE event strings.
    """
    from app.services.agent_graph import erag_agent_graph

    # Run the agent graph (routing + retrieval + tool decision/execution)
    state = await erag_agent_graph.run(state)

    # Cache hit: stream cached answer directly
    if state["cache_hit"]:
        yield sse_event("token", state["final_answer"])
        for c in state.get("citations", []):
            yield sse_event("citation", {"citation": c} if isinstance(c, dict) else c)
        yield sse_event("done", {
            "cache_hit": True,
        })
        return

    # Build messages and stream LLM generation
    messages = erag_agent_graph.build_generation_messages(state)
    full_answer = ""
    try:
        async for token in llm_client.chat_stream(messages):
            full_answer += token
            yield sse_event("token", token)

        # Send citations
        for c in state.get("citations", []):
            yield sse_event("citation", {"citation": c} if isinstance(c, dict) else c)

        # Done event
        yield sse_event("done", {
            "cache_hit": False,
            "retrieval_ms": state.get("retrieval_ms", 0),
        })

    except Exception as e:
        logger.error("LLM stream error: %s", e)
        yield sse_event("error", str(e))

    # Post-process: cache + memory (fire-and-forget)
    asyncio.create_task(_store_memory_and_cache(
        query=state["query"],
        answer=full_answer,
        kb_id=state["kb_id"],
        user_id=state.get("user_id", ""),
        citations=state.get("citations", []),
        skill_id=(state.get("active_skill") or {}).get("id", ""),
        kb_prompt=state.get("kb_prompt", ""),
    ))


async def _store_memory_and_cache(
    query: str, answer: str, kb_id: str, user_id: str,
    citations: list[dict], skill_id: str, kb_prompt: str = "",
):
    """Background task: store to cache and Mem0."""
    try:
        # Cache
        answer_cache.put(query, kb_id, answer, citations, skill_id=skill_id, kb_prompt=kb_prompt)

        # Mem0 (lazy import)
        if user_id and answer:
            try:
                from app.services.memory import add_memory
                await add_memory(
                    f"Q: {query}\nA: {answer[:500]}",
                    user_id=user_id,
                    metadata={"kb_id": kb_id, "skill_id": skill_id},
                )
            except ImportError:
                pass  # Mem0 not available
        logger.info("Post-process: cached (user=%s)", user_id[:8] if user_id else "?")
    except Exception as e:
        logger.warning("Post-process error: %s", e)

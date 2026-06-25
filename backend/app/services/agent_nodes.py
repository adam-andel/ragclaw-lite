"""Agent graph nodes for the ERAG LangGraph state machine.

Each node is an async function receiving EragAgentState, returning a
partial state dict. LangGraph merges the returned dict into the state.

Graph topology:
    router → [cache_hit? → END]
    router → retrieval
    retrieval → tool_decision
    tool_decision → [tool_calls? → tool_executor]
    tool_decision → [no tools → build_context]
    tool_executor → tool_decision  (multi-round loop)
    build_context → END
"""

import asyncio
import json
import logging
import time
from datetime import datetime

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.skill import Skill, SkillTool
from app.services.hybrid_search import hybrid_search
from app.services.llm_client import llm_client
from app.services.cache import answer_cache
from app.services.tool_registry import tool_registry

logger = logging.getLogger("erag.agent")

# Default system prompt when no SKILL is selected
DEFAULT_SYSTEM_PROMPT = """你是一个企业知识库助手。根据提供的文档内容回答问题。

## 规则
1. 只根据提供的文档内容回答，不要编造信息
2. 如果文档中没有相关信息，诚实地说"文档中未找到相关信息"
3. 在回答中标注引用来源，格式：[来源: 文档名 章节名]
4. 回答要简洁、准确，使用中文
5. 如果文档内容包含代码或表格，保留原始格式"""

MAX_TOOL_ROUNDS = 5  # Prevent infinite tool call loops


# ── Node: skill_router ──

async def skill_router_node(state: dict) -> dict:
    """Check cache, then route to the best SKILL for this query.

    Returns cache_hit + final_answer if cache hit.
    Otherwise returns active_skill + available_tools.
    """
    query = state["query"]
    kb_id = state["kb_id"]
    skill_id = state.get("skill_id")
    tenant_id = state.get("tenant_id")
    user_id = state.get("user_id")

    # 1. Check cache
    cache_key_skill = skill_id or ""
    cached = answer_cache.get(query, kb_id, skill_id=cache_key_skill)
    if cached:
        logger.info("Router: cache hit")
        return {
            "cache_hit": True,
            "final_answer": cached.answer,
            "citations": cached.citations or [],
        }

    # 2. If skill_id specified explicitly, load it directly
    active_skill = None
    available_tools = []

    if skill_id:
        active_skill, available_tools = await _load_skill(skill_id)

    # 3. Otherwise, use LLM to route to best skill
    if not active_skill and not skill_id:
        active_skill, available_tools = await _route_to_best_skill(
            query, tenant_id, user_id
        )

    logger.info("Router: skill=%s tools=%d",
                 active_skill.get("name", "default") if active_skill else "default",
                 len(available_tools))

    return {
        "active_skill": active_skill,
        "available_tools": available_tools,
        "cache_hit": False,
        "tool_round": 0,
        "tool_results": [],
    }


async def _load_skill(skill_id: str) -> tuple[dict | None, list[dict]]:
    """Load a specific skill and its tools from the database."""
    async with async_session() as db:
        result = await db.execute(select(Skill).where(Skill.id == skill_id))
        skill = result.scalar_one_or_none()
        if not skill:
            return None, []

        skill_dict = {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "system_prompt": skill.system_prompt or DEFAULT_SYSTEM_PROMPT,
        }

        tools = await tool_registry.get_tools_for_skill_async(skill.id)
        return skill_dict, tools


async def _route_to_best_skill(
    query: str, tenant_id: str, user_id: str
) -> tuple[dict | None, list[dict]]:
    """Use LLM to pick the most appropriate skill."""
    # Load available skills
    async with async_session() as db:
        result = await db.execute(
            select(Skill).where(
                (Skill.tenant_id == tenant_id) & (Skill.is_active == True)  # noqa: E712
            )
        )
        skills = result.scalars().all()

    if not skills:
        return None, []

    # Build routing prompt
    skill_list = "\n".join(
        f"- {s.name}: {s.description or '(无描述)'}"
        for s in skills
    )

    routing_prompt = f"""你是一个意图路由器。根据用户的问题，从以下技能中选择最合适的一个。

可用技能：
{skill_list}

规则：
- 如果用户的问题与某个技能高度匹配，选择该技能
- 如果用户的问题与所有技能都不匹配，返回 "default"
- 只返回技能名称，不要有任何其他输出

用户问题：{query}

技能名称："""

    try:
        response = await llm_client.chat(
            messages=[{"role": "user", "content": routing_prompt}],
            temperature=0,
            max_tokens=50,
        )
        chosen_name = response.strip().strip('"').strip('"').strip('。'.'!')
        logger.info("Route LLM returned: '%s' (skills: %s)",
                     chosen_name,
                     [s.name for s in skills])

        # Fuzzy match: try exact first, then case-insensitive, then substring
        for s in skills:
            if s.name == chosen_name or s.name.lower() == chosen_name.lower() or s.name.lower().replace(" ", "") == chosen_name.lower().replace(" ", ""):
                tools = await tool_registry.get_tools_for_skill_async(s.id)
                logger.info("Route matched: %s (tools=%d)", s.name, len(tools))
                return {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "system_prompt": s.system_prompt or DEFAULT_SYSTEM_PROMPT,
                }, tools
    except Exception as e:
        logger.warning("Skill routing failed: %s, using default", e)

    return None, []


# ── Node: parallel_retrieval ──

async def parallel_retrieval_node(state: dict) -> dict:
    """Run hybrid search + Mem0 memory search in parallel.

    All ChromaDB/BM25 calls go through run_in_executor to avoid
    blocking the async event loop.
    """
    if state.get("cache_hit"):
        return {}

    query = state["query"]
    kb_id = state["kb_id"]
    user_id = state.get("user_id", "")
    t_start = time.time()

    loop = asyncio.get_running_loop()

    # Run both searches in parallel
    rag_task = loop.run_in_executor(
        None,
        lambda: hybrid_search.search(kb_id, query),
    )

    # Lazy-import memory to avoid hard dependency on mem0 at module level
    mem_task_coro = _search_memories_safe(query, user_id) if user_id else None

    if mem_task_coro:
        results = await asyncio.gather(rag_task, mem_task_coro, return_exceptions=True)
        memories = results[1] if not isinstance(results[1], Exception) and results[1] is not None else []
        if isinstance(results[1], Exception):
            logger.warning("Mem0 search error: %s", results[1])
    else:
        retrieved_list = await rag_task
        results = [retrieved_list, []]
        memories = []

    retrieved = results[0] if not isinstance(results[0], Exception) else []
    if isinstance(results[0], Exception):
        logger.warning("Retrieval error: %s", results[0])

    retrieval_ms = round((time.time() - t_start) * 1000)

    # Build context and citations
    rag_context, citations = _build_context(retrieved)
    memory_context = _build_memory_context(memories) if memories else ""

    logger.info("Retrieval: %d chunks + %d memories in %.0fms",
                 len(retrieved), len(memories) if memories else 0, retrieval_ms)

    return {
        "rag_context": rag_context,
        "citations": citations,
        "memory_context": memory_context,
        "retrieval_ms": retrieval_ms,
    }


async def _search_memories_safe(query: str, user_id: str, limit: int = 5) -> list[dict]:
    """Lazy-load memory search; returns empty list if mem0 not available."""
    try:
        from app.services.memory import search_memories
        return await search_memories(query, user_id=user_id, limit=limit) or []
    except ImportError:
        logger.debug("Mem0 not available, skipping memory search")
        return []
    except Exception as e:
        logger.warning("Mem0 search failed: %s", e)
        return []


def _build_context(retrieved: list[dict]) -> tuple[str, list[dict]]:
    """Build formatted context text and citation list from search results."""
    if not retrieved:
        return "未找到相关文档", []

    parts, citations = [], []
    for i, r in enumerate(retrieved):
        doc_name = r.get("doc_name", r.get("doc_id", "?")[:8])
        heading = r.get("heading", "") or ""
        parts.append(f"[{i + 1}] {doc_name} {heading}\n{r['content']}")
        citations.append({
            "doc_id": r.get("doc_id", ""),
            "doc_name": doc_name,
            "heading": heading,
            "page": r.get("page"),
            "content_snippet": r["content"][:200],
            "score": round(r.get("fusion_score", 0), 4),
        })

    return "\n\n---\n\n".join(parts), citations


def _build_memory_context(memories: list[dict]) -> str:
    """Build memory context text from Mem0 results."""
    lines = []
    for m in memories:
        text = m.get("memory", m.get("text", str(m)))
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) if lines else ""


# ── Node: tool_decision ──

async def tool_decision_node(state: dict) -> dict:
    """Decide whether to call MCP tools based on query + context.

    In multi-round mode, includes previous tool calls and results
    as messages so the LLM can see what's already been done.

    Returns tool_calls (list of tool calls) or None if no tools needed.
    """
    if state.get("cache_hit"):
        return {}

    available_tools = state.get("available_tools", [])
    if not available_tools:
        logger.info("Tool decision: no available tools, skipping")
        return {"tool_calls": None}

    # Prevent infinite loops
    tool_round = state.get("tool_round", 0)
    if tool_round >= MAX_TOOL_ROUNDS:
        logger.warning("Tool decision: max rounds (%d) reached", MAX_TOOL_ROUNDS)
        return {"tool_calls": None}

    # Build messages for tool decision
    active_skill = state.get("active_skill") or {}
    system_prompt = active_skill.get("system_prompt", DEFAULT_SYSTEM_PROMPT)

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    # Add conversation history
    history = state.get("conversation_history", [])
    if history:
        messages.extend(history)

    # Build user message
    user_parts = []
    if state.get("memory_context"):
        user_parts.append(f"## 用户偏好与历史记忆\n{state['memory_context']}")
    if state.get("rag_context"):
        user_parts.append(f"## 参考文档\n{state['rag_context']}")
    user_parts.append(f"## 问题\n{state['query']}")
    messages.append({"role": "user", "content": "\n\n".join(user_parts)})

    # Add accumulated tool messages from previous rounds
    tool_messages = state.get("tool_messages", [])
    if tool_messages:
        messages.extend(tool_messages)

    try:
        response = await llm_client.chat_with_tools(
            messages=messages,
            tools=available_tools,
            temperature=0.1,
            max_tokens=512,
        )

        tool_calls = response.get("tool_calls")
        if tool_calls:
            logger.info("Tool decision: %d tool call(s) (round %d)", len(tool_calls), tool_round + 1)
            # Build assistant message with tool_calls for multi-round context
            tool_msg = {
                "role": "assistant",
                "content": response.get("content") or "",
                "tool_calls": tool_calls,
            }
            return {
                "tool_calls": tool_calls,
                "tool_messages": [tool_msg],
            }
        else:
            logger.info("Tool decision: no tools needed (finish_reason=%s)",
                         response.get("finish_reason"))
            return {"tool_calls": None}

    except Exception as e:
        logger.warning("Tool decision error: %s, skipping tools", e)
        return {"tool_calls": None}


# ── Node: tool_executor ──

async def tool_executor_node(state: dict) -> dict:
    """Execute tool calls decided by tool_decision_node.

    Each tool call is executed in parallel with an independent timeout.
    Errors in individual tools do not block other tools.
    """
    tool_calls = state.get("tool_calls", [])
    if not tool_calls:
        return {"tool_results": []}

    from app.services.mcp_client import mcp_client
    from app.models.skill import MCPServer

    async def execute_one(tc: dict) -> str:
        """Execute a single tool call, return result string."""
        func = tc.get("function", {})
        tool_name = func.get("name", "unknown")
        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}

        # Find which MCP server this tool belongs to
        # We look up the SkillTool binding to get the server_id
        skill_id = state.get("active_skill", {}).get("id") if state.get("active_skill") else None
        if not skill_id:
            return f"Tool '{tool_name}' error: no active skill"

        async with async_session() as db:
            result = await db.execute(
                select(SkillTool).where(
                    (SkillTool.skill_id == skill_id)
                    & (SkillTool.tool_name == tool_name)
                )
            )
            binding = result.scalar_one_or_none()
            if not binding:
                return f"Tool '{tool_name}' error: not found in skill bindings"

            server = await db.get(MCPServer, binding.mcp_server_id)
            if not server:
                return f"Tool '{tool_name}' error: MCP server not found"

            server_config = {
                "id": server.id,
                "transport_type": server.transport_type,
                "endpoint": server.endpoint,
                "command": server.command,
                "args_json": server.args_json,
                "env_json": server.env_json,
                "timeout_seconds": server.timeout_seconds,
            }

        try:
            res = await mcp_client.call_tool(server_config, tool_name, arguments)
            if res.ok:
                return f"[{tool_name}] {res.result}"
            else:
                return f"[{tool_name}] 错误: {res.error}"
        except Exception as e:
            return f"[{tool_name}] 执行异常: {str(e)}"

    # Execute all tool calls in parallel
    tasks = [execute_one(tc) for tc in tool_calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Convert any exceptions to error strings
    tool_results = []
    for r in results:
        if isinstance(r, Exception):
            tool_results.append(f"工具调用异常: {str(r)}")
        else:
            tool_results.append(r)

    logger.info("Tool executor: %d results (round %d)", len(tool_results), state.get("tool_round", 0) + 1)

    # Build tool result messages for multi-round context
    tool_result_messages = []
    for i, tc in enumerate(tool_calls):
        func = tc.get("function", {})
        tool_name = func.get("name", "unknown")
        tool_call_id = tc.get("id", f"call_{i}")
        result_text = tool_results[i] if i < len(tool_results) else ""
        tool_result_messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result_text,
        })

    return {
        "tool_results": tool_results,
        "tool_messages": tool_result_messages,
        "tool_round": state.get("tool_round", 0) + 1,
    }


# ── Node: build_context ──

async def build_context_node(state: dict) -> dict:
    """Final context assembly node — marks the end of the graph.

    This node doesn't add new state; it signals that retrieval + tools
    are complete and the chat.py can proceed with LLM generation.
    """
    return {}

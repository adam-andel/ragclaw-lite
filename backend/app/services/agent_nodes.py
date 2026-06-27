"""Agent graph nodes for the ERAG LangGraph state machine."""
import asyncio, json, logging, time
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

DEFAULT_SYSTEM_PROMPT = """你是一个企业知识库助手。根据提供的文档内容回答问题。

## 规则
1. 只根据提供的文档内容回答，不要编造信息
2. 如果文档中没有相关信息，诚实地说"文档中未找到相关信息"
3. 在回答中标注引用来源，格式：[来源: 文档名 章节名]
4. 回答要简洁、准确，使用中文
5. 如果文档内容包含代码或表格，保留原始格式"""

MAX_TOOL_ROUNDS = 3


def _try_parse_tool_call(content: str, available_tools: list[dict]) -> list[dict] | None:
    import re
    # Strip code blocks
    cleaned = re.sub(r'```(?:json)?\s*|```', '', content).strip()
    # Strip leading conversational text — models often add greetings before JSON
    # Look for the first '{' and try parsing from there
    obj_match = re.search(r'\{[\s\S]*\}', cleaned)
    if obj_match:
        cleaned = obj_match.group(0)
    if not cleaned.startswith('{'):
        return None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if data.get("jsonrpc") == "2.0" and data.get("method"):
        tool_name = data["method"]
        if any(t.get("function", {}).get("name") == tool_name for t in available_tools):
            return [{"id": f"call_{tool_name}", "type": "function",
                     "function": {"name": tool_name, "arguments": json.dumps(data.get("params", {}), ensure_ascii=False)}}]
    if data.get("tool") and any(t.get("function", {}).get("name") == data["tool"] for t in available_tools):
        return [{"id": f"call_{data['tool']}", "type": "function",
                 "function": {"name": data["tool"], "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False)}}]

    if data.get("name") and any(t.get("function", {}).get("name") == data["name"] for t in available_tools):
        return [{"id": f"call_{data['name']}", "type": "function",
                 "function": {"name": data["name"], "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False)}}]
    return None


def _try_extract_code_as_tool(content: str, available_tools: list[dict]) -> list[dict] | None:
    """LLM output code blocks instead of JSON? Extract Python code and build run_python call."""
    import re, logging
    _clog = logging.getLogger("erag.agent")
    _has_tool = any(t.get('function', {}).get('name') == 'run_python' for t in available_tools)
    if not _has_tool:
        _clog.info("code_extract: run_python tool not available, skip")
        return None

    # Try ```python ... ``` code blocks
    m = re.search(r'```python\s*\n([\s\S]*?)```', content)
    if not m:
        # Try ``` ... ``` without language marker
        m = re.search(r'```\s*\n([\s\S]*?)```', content)
    if m:
        code = m.group(1).strip()
        if code:
            _clog.info("code_extract: found fenced code block, len=%d", len(code))
            return [{"id": "call_run_python_from_fenced", "type": "function",
                     "function": {"name": "run_python", "arguments": json.dumps({"code": code}, ensure_ascii=False)}}]
        return None

    # Try indented code blocks (no fences)
    _CODE_STARTS = ('import ', 'from ', 'with ', 'def ', 'class ', 'print(', 'for ', 'if ',
                    '#', 'try:', 'except', 'while ', 'return ', 'open(', 'f.write(',
                    'df.', 'pd.', 'plt.', 'data', 'text', 'result', 'content')
    lines = content.strip().split('\n')
    code_lines = []
    in_code = False
    for line in lines:
        s = line.strip()
        if not in_code:
            if s.startswith(_CODE_STARTS):
                in_code = True
                code_lines.append(line)
        else:
            if line.startswith(' ') or line.startswith('\t') or s == '':
                code_lines.append(line)
            else:
                break
    if code_lines:
        code = '\n'.join(code_lines).strip()
        # Confirm it looks like real Python
        if any(kw in code for kw in ('open(', 'write(', 'print(', 'with ', 'import ', 'def ')):
            _clog.info("code_extract: found indented code block, len=%d", len(code))
            return [{"id": "call_run_python_from_indent", "type": "function",
                     "function": {"name": "run_python", "arguments": json.dumps({"code": code}, ensure_ascii=False)}}]

    _clog.info("code_extract: no code found in content=%.200s", content[:200])
    return None


# ── Router ──

async def skill_router_node(state: dict) -> dict:
    query, kb_id = state["query"], state["kb_id"]
    skill_id, tenant_id, user_id = state.get("skill_id"), state.get("tenant_id"), state.get("user_id")
    cached = answer_cache.get(query, kb_id, skill_id=skill_id or "")
    if cached:
        return {"cache_hit": True, "final_answer": cached.answer, "citations": cached.citations or [], "tool_results": [], "tool_messages": []}

    active_skill, available_tools = None, []
    if skill_id:
        active_skill, available_tools = await _load_skill(skill_id)
        logger.info("Router: loaded skill_id=%s name=%s tools=%d", skill_id, active_skill.get('name') if active_skill else 'NONE', len(available_tools))
    if not active_skill and not skill_id:
        active_skill, available_tools = await _route_to_best_skill(query, tenant_id, user_id)
        logger.info("Router: auto-routed to skill=%s tools=%d", active_skill.get('name') if active_skill else 'NONE', len(available_tools))

    return {"active_skill": active_skill, "available_tools": available_tools,
            "cache_hit": False, "tool_round": 0, "tool_results": [], "tool_messages": []}


async def _load_skill(skill_id: str):
    async with async_session() as db:
        s = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
        if not s:
            return None, []
        tools = await tool_registry.get_tools_for_skill_async(s.id)
        return {"id": s.id, "name": s.name, "description": s.description, "system_prompt": s.system_prompt or DEFAULT_SYSTEM_PROMPT}, tools


async def _route_to_best_skill(query, tenant_id, user_id):
    async with async_session() as db:
        skills = (await db.execute(select(Skill).where((Skill.tenant_id == tenant_id) & (Skill.is_active == True)))).scalars().all()
    if not skills:
        return None, []
    skill_list = "\n".join(f"- {s.name}: {s.description or '(无描述)'}" for s in skills)
    prompt = f"你是一个意图路由器。根据用户的问题，从以下技能中选择最合适的一个。\n\n可用技能：\n{skill_list}\n\n规则：\n- 如果用户的问题与某个技能高度匹配，选择该技能\n- 如果用户的问题与所有技能都不匹配，返回 \"default\"\n- 只返回技能名称，不要有任何其他输出\n\n用户问题：{query}\n\n技能名称："
    try:
        chosen = (await llm_client.chat(messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=50)).strip().strip('"').strip("'").strip('。!').strip()
        for s in skills:
            if s.name == chosen or s.name.lower() == chosen.lower() or s.name.lower().replace(" ", "") == chosen.lower().replace(" ", ""):
                tools = await tool_registry.get_tools_for_skill_async(s.id)
                return {"id": s.id, "name": s.name, "description": s.description, "system_prompt": s.system_prompt or DEFAULT_SYSTEM_PROMPT}, tools
    except Exception as e:
        logger.warning("Skill routing failed: %s", e)
    return None, []


# ── Retrieval ──

async def parallel_retrieval_node(state: dict) -> dict:
    if state.get("cache_hit"):
        return {}
    query, kb_id, user_id = state["query"], state["kb_id"], state.get("user_id", "")
    t_start = time.time()
    loop = asyncio.get_running_loop()
    rag_task = loop.run_in_executor(None, lambda: hybrid_search.search(kb_id, query))
    mem_coro = _search_memories_safe(query, user_id) if user_id else None
    if mem_coro:
        results = await asyncio.gather(rag_task, mem_coro, return_exceptions=True)
        mem_raw = results[1] if not isinstance(results[1], Exception) and results[1] is not None else []
        if isinstance(results[1], Exception):
            logger.warning("Mem0 search error: %s", results[1])
    else:
        results = [await rag_task, []]; mem_raw = []
    retrieved = results[0] if not isinstance(results[0], Exception) else []
    if isinstance(results[0], Exception):
        logger.warning("Retrieval error: %s", results[0])
    rag_context, citations = _build_context(retrieved)
    memory_context = _build_memory_context(mem_raw) if mem_raw else ""
    return {"rag_context": rag_context, "citations": citations, "memory_context": memory_context,
            "retrieval_ms": round((time.time() - t_start) * 1000)}


async def _search_memories_safe(query: str, user_id: str, limit: int = 5) -> list[dict]:
    try:
        from app.services.memory import search_memories
        return await search_memories(query, user_id=user_id, limit=limit) or []
    except ImportError:
        return []
    except Exception as e:
        logger.warning("Mem0 search failed: %s", e)
        return []


def _build_context(retrieved: list[dict]) -> tuple[str, list[dict]]:
    if not retrieved:
        return "未找到相关文档", []
    parts, citations = [], []
    for i, r in enumerate(retrieved):
        doc_name = r.get("doc_name", r.get("doc_id", "?")[:8])
        heading = r.get("heading", "") or ""
        parts.append(f"[{i + 1}] {doc_name} {heading}\n{r['content']}")
        citations.append({"doc_id": r.get("doc_id", ""), "doc_name": doc_name, "heading": heading,
                          "page": r.get("page"), "content_snippet": r["content"][:200],
                          "score": round(r.get("fusion_score", 0), 4)})
    return "\n\n---\n\n".join(parts), citations


def _build_memory_context(memories: list[dict]) -> str:
    return "\n".join(f"- {m.get('memory', m.get('text', str(m)))}" for m in memories if m.get('memory') or m.get('text'))


# ── Tool Decision ──

async def tool_decision_node(state: dict) -> dict:
    if state.get("cache_hit"):
        return {}
    available_tools = state.get("available_tools", [])
    if not available_tools:
        logger.warning("Tool decision: no available tools — skipping tool phase")
        return {"tool_calls": None}
    tool_round = state.get("tool_round", 0)
    if tool_round >= MAX_TOOL_ROUNDS:
        logger.warning("Tool decision: max rounds reached")
        return {"tool_calls": None}

    # Force-stop if all previous tool results were errors
    prev_results = state.get("tool_results", [])
    if prev_results and all("error" in r.lower() or "异常" in r for r in prev_results):
        logger.warning("Tool decision: all tools errored, stopping loop")
        return {"tool_calls": None}
    active = state.get("active_skill") or {}
    skill_prompt = active.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    if available_tools:
        tool_desc = "\n".join(
            f"- {t['function']['name']}: {t['function']['description']}"
            for t in available_tools
        )
        # Tencent tokenhub does not support tool_choice="required" (400/502).
        # Use tool_choice="auto" and steer via prompt. Even when the LLM ignores
        # the JSON instruction, its alternate output (Python code blocks) is caught
        # by _try_extract_code_as_tool — plain text hallucination would be the worst case.
        tool_system = (
            "# ⚠️ 关键指令：输出工具调用 JSON\n\n"
            "**绝对禁止**直接回复用户、编造文件链接或下载地址。"
            "你必须立即输出一个纯 JSON 对象来调用工具。\n\n"
            + "## 何时必须使用工具\n"
            + "- 用户要求「生成」「创建」「写入」「保存」文件 → **必须**调用 run_python\n"
            + "- 用户要求执行代码、数据处理、计算 → **必须**调用 run_python\n"
            + "- 任何读写文件的操作 → **必须**调用 run_python\n\n"
            + "## 可用工具\n" + tool_desc + "\n\n"
            + "## 输出格式\n"
            + '{"tool": "工具名", "arguments": {"参数名": "参数值"}}\n\n'
            + "## 规则\n"
            + "- 只输出上述 JSON 对象，不要附加任何文字\n"
            + "- 不要用 ``` 包裹 JSON\n"
            + "- 不要输出最终回复——那是下一阶段的事\n"
            + "- **绝对不要**编造下载链接、文件路径或 uuid"
        )
        messages = [
            {"role": "system", "content": tool_system},
            {"role": "system", "content": "## 任务背景（仅供参考）\n" + skill_prompt},
        ]
    else:
        messages = [{"role": "system", "content": skill_prompt}]
    history = state.get("conversation_history", [])
    if history:
        messages.extend(history)
    user_parts = []
    if state.get("memory_context"):
        user_parts.append(f"## 用户偏好与历史记忆\n{state['memory_context']}")
    if state.get("rag_context"):
        user_parts.append(f"## 参考文档\n{state['rag_context']}")
    user_parts.append(f"## 问题\n{state['query']}")
    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
    tool_messages = state.get("tool_messages", [])
    if tool_messages:
        messages.extend(tool_messages)
    try:
        # Use "auto" — Tencent tokenhub proxy does not support "required" (returns 502).
        # Rely on a strong system prompt to steer the LLM toward tool usage.
        tc = "auto"
        logger.info("Tool decision: calling LLM with %d tools, max_tokens=2048, tool_choice=%s, round=%d",
                   len(available_tools), tc, tool_round)
        response = await llm_client.chat_with_tools(messages=messages, tools=available_tools,
                                                     temperature=0.1, max_tokens=2048, tool_choice=tc)
        
        if not response:
            logger.warning("Tool decision: LLM returned empty content and no native tool_calls")

        tool_calls = response.get("tool_calls")
        content_preview = (response.get("content") or "")[:200]
        logger.info("Tool decision: native_tool_calls=%s content_preview=%s", bool(tool_calls), content_preview)
        if not tool_calls:
            parsed = None
            if response.get("content"):
                parsed = _try_parse_tool_call(response["content"], available_tools)
                if parsed:
                    logger.info("Tool decision: parsed tool_calls from JSON in content")
                    tool_calls = parsed
                    response["content"] = ""
            if not tool_calls and response.get("content"):
                logger.warning("Tool decision: no tool_calls in response, content does not contain parseable JSON")
                # Fallback: LLM might have output Python code instead of JSON.
                # Extract code blocks and build a run_python tool call automatically.
                logger.info("Tool decision: trying code extraction, content_len=%d preview=%.200s",
                           len(response["content"]), response["content"][:200])
                code_tool = _try_extract_code_as_tool(response["content"], available_tools)
                if code_tool:
                    logger.info("Tool decision: extracted code from LLM response, built run_python call")
                    tool_calls = code_tool
                    response["content"] = ""
                else:
                    logger.warning("Tool decision: code extraction also failed, content=%.300s",
                                 (response["content"] or "")[:300])
        if tool_calls:
            tool_msg = {"role": "assistant", "content": response.get("content") or "", "tool_calls": tool_calls}
            return {"tool_calls": tool_calls, "tool_messages": [tool_msg]}
        logger.warning("Tool decision: no tool_calls produced, proceeding to final generation")
        return {"tool_calls": None}
    except Exception as e:
        logger.warning("Tool decision error: %s", e)
        return {"tool_calls": None}

def _dump_state_for_debug(state: dict) -> str:
    """Quick state dump for debugging."""
    skill = state.get("active_skill") or {}
    return f"skill={skill.get('name','?')} tools={len(state.get('available_tools',[]))} content_len={len(state.get('final_answer',''))}"


# ── Tool Executor ──

import re as _re

def _enrich_with_download_links(result: str) -> str:
    """If tool result contains [workspace: XXXXXXXX/], append download links.

    This keeps URL construction in code, preventing the LLM from hallucinating links.
    """
    # Match [workspace: XXXXXXXX/] pattern (8-char UUID)
    m = _re.search(r'\[workspace:\s*([a-f0-9]{8})/\]', result)
    if not m:
        return result
    uuid_dir = m.group(1)
    # Build download base URL from MCP server endpoint (same host, port 9200)
    base = "http://localhost:9200/files"
    # If result mentions specific files, add inline links
    enriched = result
    # Find filename patterns in the output: common extensions
    file_pattern = _re.findall(r'(?<![\w/])([\w\-]+(?:\.(?:txt|csv|json|xlsx?|docx|pptx|pdf|png|jpg|html|md|py)))(?![\w/])', result)
    if file_pattern:
        links = "\n".join(f"  → {base}/{uuid_dir}/{f}" for f in set(file_pattern))
        enriched = result + f"\n\n[下载链接]\n{links}"
    else:
        # No specific file found, provide workspace link
        enriched = result + f"\n\n[下载链接] 工作目录: {base}/{uuid_dir}/"
    return enriched


async def tool_executor_node(state: dict) -> dict:
    tool_calls = state.get("tool_calls", [])
    if not tool_calls:
        return {"tool_results": [], "tool_round": state.get("tool_round", 0) + 1}
    from app.services.mcp_client import mcp_client as _mc
    from app.models.skill import MCPServer

    async def execute_one(tc: dict) -> str:
        func = tc.get("function", {})
        tname = func.get("name", "unknown")
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        skill_id = state.get("active_skill", {}).get("id") if state.get("active_skill") else None
        if not skill_id:
            return f"Tool '{tname}' error: no active skill"
        async with async_session() as db:
            b = (await db.execute(select(SkillTool).where((SkillTool.skill_id == skill_id) & (SkillTool.tool_name == tname)))).scalar_one_or_none()
            if not b:
                return f"Tool '{tname}' error: not found in skill bindings"
            srv = await db.get(MCPServer, b.mcp_server_id)
            if not srv:
                return f"Tool '{tname}' error: MCP server not found"
            cfg = {"id": srv.id, "transport_type": srv.transport_type, "endpoint": srv.endpoint,
                   "command": srv.command, "args_json": srv.args_json, "env_json": srv.env_json,
                   "timeout_seconds": srv.timeout_seconds}
        try:
            res = await _mc.call_tool(cfg, tname, args)
            if res.ok:
                return f"[{tname}] {res.result}"
            return f"[{tname}] 错误: {res.error}"
        except Exception as e:
            return f"[{tname}] 执行异常: {str(e)}"

    tasks = [execute_one(tc) for tc in tool_calls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    tr = [str(r) if isinstance(r, Exception) else r for r in results]

    # Enrich tool results with download links (system-generated, not LLM-hallucinated)
    tr = [_enrich_with_download_links(r) for r in tr]

    for i, r in enumerate(tr):
        logger.info("Tool executor round=%d result[%d]: %s", state.get("tool_round", 0) + 1, i, r[:300])
    result_msgs = []
    for i, tc in enumerate(tool_calls):
        func = tc.get("function", {})
        result_msgs.append({"role": "tool", "tool_call_id": tc.get("id", f"call_{i}"),
                            "name": func.get("name", "unknown"),
                            "content": tr[i] if i < len(tr) else ""})
    return {"tool_results": tr, "tool_messages": result_msgs, "tool_round": state.get("tool_round", 0) + 1}


# ── Build Context ──

async def build_context_node(state: dict) -> dict:
    return {}

"""Agent graph nodes for the ERAG LangGraph state machine."""
import asyncio, json, logging, time
from datetime import datetime
from sqlalchemy import select
from app.config import settings
from app.database import async_session
from app.models.skill import Skill, MCPServer
from app.services.hybrid_search import hybrid_search
from app.services.llm_client import llm_client
from app.services.config_manager import config_manager
from app.services.cache import answer_cache
from app.services.skill_manager import (
    get_skill_by_id, get_skill_by_folder, read_skill_md, parse_skill_md,
)
from app.services.skill_script_loader import discover_tools, execute_script_tool
from app.services.tool_registry import tool_registry

logger = logging.getLogger("erag.agent")

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


# ── Router (Layer 1: name + description only) ──

async def skill_router_node(state: dict) -> dict:
    """Layer 1 routing — only queries DB index (name + description).

    Does NOT load SKILL.md full text or tools. That happens in skill_loader_node.
    """
    query, kb_id = state["query"], state["kb_id"]
    skill_id, tenant_id, user_id = state.get("skill_id"), state.get("tenant_id"), state.get("user_id")
    cached = answer_cache.get(query, kb_id, skill_id=skill_id or "")
    if cached:
        return {"cache_hit": True, "final_answer": cached.answer, "citations": cached.citations or [], "tool_results": [], "tool_messages": []}

    active_skill = None
    if skill_id:
        # User explicitly selected a skill — just fetch the DB index
        active_skill = await _get_skill_index(skill_id)
        logger.info("Router: explicit skill_id=%s name=%s", skill_id, active_skill.get('name') if active_skill else 'NONE')
    if not active_skill and not skill_id:
        # Auto-route using name + description only
        active_skill = await _route_to_best_skill(query, tenant_id, user_id)
        logger.info("Router: auto-routed to skill=%s", active_skill.get('name') if active_skill else 'NONE')

    # Layer 1 output: only id/name/description/folder_name — no system_prompt, no tools
    return {"active_skill": active_skill, "available_tools": [],
            "cache_hit": False, "tool_round": 0, "tool_results": [], "tool_messages": []}


async def _get_skill_index(skill_id: str) -> dict | None:
    """Fetch skill DB index row by ID. Returns {id, name, description, folder_name}."""
    async with async_session() as db:
        skill = await get_skill_by_id(db, skill_id)
        if not skill or not skill.is_active:
            return None
        return {"id": skill.id, "name": skill.name, "description": skill.description, "folder_name": skill.folder_name}


async def _route_to_best_skill(query, tenant_id, user_id) -> dict | None:
    """Auto-route using LLM to match query against skill name + description (Layer 1).

    Returns {id, name, description, folder_name} or None.
    """
    async with async_session() as db:
        skills = (await db.execute(
            select(Skill).where((Skill.tenant_id == tenant_id) & (Skill.is_active == True))  # noqa: E712
        )).scalars().all()
    if not skills:
        return None
    skill_list = "\n".join(f"- {s.name}: {s.description or '(无描述)'}" for s in skills)
    prompt = f"你是一个意图路由器。根据用户的问题，从以下技能中选择最合适的一个。\n\n可用技能：\n{skill_list}\n\n规则：\n- 如果用户的问题与某个技能高度匹配，选择该技能\n- 如果用户的问题与所有技能都不匹配，返回 \"default\"\n- 只返回技能名称，不要有任何其他输出\n\n用户问题：{query}\n\n技能名称："
    try:
        chosen = (await llm_client.chat(messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=50)).strip().strip('"').strip("'").strip('。!').strip()
        for s in skills:
            if s.name == chosen or s.name.lower() == chosen.lower() or s.name.lower().replace(" ", "") == chosen.lower().replace(" ", ""):
                return {"id": s.id, "name": s.name, "description": s.description, "folder_name": s.folder_name}
    except Exception as e:
        logger.warning("Skill routing failed: %s", e)
    return None


# ── Skill Loader (Layer 2: SKILL.md full text + tools) ──

async def skill_loader_node(state: dict) -> dict:
    """Layer 2 — load SKILL.md full text, discover script tools, load MCP tools.

    Runs after router (if a skill was selected) and before retrieval.
    If no active skill, passes through with default system prompt.
    """
    if state.get("cache_hit"):
        return {}

    active_skill = state.get("active_skill")
    if not active_skill:
        return {}

    folder_name = active_skill.get("folder_name")
    if not folder_name:
        return {}

    # Read and parse SKILL.md
    skill_md_content = read_skill_md(folder_name)
    if not skill_md_content:
        logger.warning("Skill loader: SKILL.md not found for folder=%s", folder_name)
        return {}

    parsed = parse_skill_md(skill_md_content)
    system_prompt = parsed["body"] or config_manager.system_prompt
    mcp_server_names = parsed.get("mcp_servers", [])

    # Update active_skill with system_prompt
    updated_skill = {**active_skill, "system_prompt": system_prompt}

    # Discover script tools (from scripts/*.py)
    script_tools = discover_tools(folder_name)
    logger.info("Skill loader: folder=%s script_tools=%d", folder_name, len(script_tools))

    # Load MCP tools (from front matter mcp_servers declaration)
    mcp_tools = await tool_registry.get_mcp_tools(mcp_server_names)
    logger.info("Skill loader: folder=%s mcp_tools=%d (servers=%s)", folder_name, len(mcp_tools), mcp_server_names)

    all_tools = script_tools + mcp_tools

    return {"active_skill": updated_skill, "available_tools": all_tools}


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
        page = r.get("page")
        if page == 0:
            page = None
        parts.append(f"[{i + 1}] {doc_name} {heading}\n{r['content']}")
        citations.append({"doc_id": r.get("doc_id", ""), "doc_name": doc_name,
                          "chunk_index": r.get("chunk_index", 0), "heading": heading,
                          "page": page, "score": round(r.get("fusion_score", 0), 4)})
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
    skill_prompt = active.get("system_prompt", config_manager.system_prompt)
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
            + "- **绝对不要**编造File、文件路径或 uuid"
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
                    logger.info("Tool decision: parsed tool_calls from JSON in content (round %d)", tool_round)
                    tool_calls = parsed
                    response["content"] = ""
            if not tool_calls and response.get("content"):
                # No native tool_calls and no JSON — try code extraction as last resort.
                # If this is round 1+, this is expected (LLM received tool results and needs
                # no more tools). Using info-level to avoid false-alarm warnings.
                logger.info("Tool decision: no native/JSON tool call (round %d), trying code extraction", tool_round)
                logger.info("Tool decision: code_extract input preview=%.200s", response["content"][:200])
                code_tool = _try_extract_code_as_tool(response["content"], available_tools)
                if code_tool:
                    logger.info("Tool decision: extracted code from LLM response, built run_python call")
                    tool_calls = code_tool
                    response["content"] = ""
                else:
                    # Not an error — LLM may be done using tools (round 1+) or responded with plain text
                    logger.info("Tool decision: code extraction yielded nothing (round %d, this is normal after tools)",
                               tool_round)
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

def _enrich_with_download_links(result: str, mcp_endpoint: str | None = None) -> str:
    """Ensure download links are present in tool result.

    If the MCP server already included a [File] (via --public-url), pass through.
    Otherwise, construct one from the MCP endpoint as fallback.
    """
    if "[File]" in result:
        return result  # MCP server already provided links

    m = _re.search(r'\[workspace:\s*([a-f0-9]{8})/\]', result)
    if not m:
        return result
    uuid_dir = m.group(1)
    base = ""
    if mcp_endpoint:
        ep_match = _re.match(r'(https?://[^/]+)', mcp_endpoint)
        if ep_match:
            base = ep_match.group(1)
    if not base:
        return result  # can't construct without base URL
    return result + f"\n\n[File] {base}/files/{uuid_dir}/"


def _extract_download_links_from_state(state: dict) -> str:
    """Scan tool results for download links and format them for final display.

    This runs OUTSIDE the LLM — links are system-generated, never hallucinated.
    """
    tool_results = state.get("tool_results", [])
    links = []
    for r in tool_results:
        if "[File]" in r:
            idx = r.index("[File]")
            links.append(r[idx:])
    if links:
        return "\n\n---\n\n" + "\n\n".join(links)
    return ""


async def tool_executor_node(state: dict) -> dict:
    tool_calls = state.get("tool_calls", [])
    if not tool_calls:
        return {"tool_results": [], "tool_round": state.get("tool_round", 0) + 1}
    from app.services.mcp_client import mcp_client as _mc

    # Build a lookup: tool_name → tool_definition (for _source routing)
    available_tools = state.get("available_tools", [])
    tool_lookup = {}
    for t in available_tools:
        fname = t.get("function", {}).get("name")
        if fname:
            tool_lookup[fname] = t

    active_skill = state.get("active_skill") or {}
    folder_name = active_skill.get("folder_name")

    async def execute_one(tc: dict):
        func = tc.get("function", {})
        tname = func.get("name", "unknown")
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}

        tool_def = tool_lookup.get(tname, {})
        tool_source = tool_def.get("_source", "mcp")

        # ── Script tool path ──
        if tool_source == "script" and folder_name:
            script_path = tool_def.get("_script_path", "")
            func_name = tool_def.get("_func_name", tname)
            # Find the python_repl MCP server config
            repl_config = await _get_repl_server_config()
            if not repl_config:
                return {"result": f"[{tname}] 错误: Python执行器 MCP Server 未配置", "endpoint": None}
            result = await execute_script_tool(folder_name, script_path, func_name, args, repl_config)
            if result.ok:
                return {"result": f"[{tname}] {result.result}", "endpoint": repl_config.get("endpoint")}
            return {"result": f"[{tname}] 错误: {result.error}", "endpoint": repl_config.get("endpoint")}

        # ── MCP tool path ──
        # Get MCP server config from tool definition metadata
        mcp_server_id = tool_def.get("_mcp_server_id")
        endpoint = None
        if mcp_server_id:
            async with async_session() as db:
                srv = await db.get(MCPServer, mcp_server_id)
                if srv:
                    endpoint = srv.endpoint
                    cfg = {"id": srv.id, "transport_type": srv.transport_type, "endpoint": srv.endpoint,
                           "command": srv.command, "args_json": srv.args_json, "env_json": srv.env_json,
                           "timeout_seconds": srv.timeout_seconds}
                else:
                    return {"result": f"[{tname}] 错误: MCP server not found", "endpoint": None}
        else:
            # Fallback: try to find run_python on the default python_repl server
            if tname == "run_python":
                repl_config = await _get_repl_server_config()
                if not repl_config:
                    return {"result": f"[{tname}] 错误: Python执行器 MCP Server 未配置", "endpoint": None}
                endpoint = repl_config.get("endpoint")
                cfg = repl_config
            else:
                return {"result": f"[{tname}] 错误: no MCP server binding for tool", "endpoint": None}
        try:
            res = await _mc.call_tool(cfg, tname, args)
            if res.ok:
                return {"result": f"[{tname}] {res.result}", "endpoint": endpoint}
            return {"result": f"[{tname}] 错误: {res.error}", "endpoint": endpoint}
        except Exception as e:
            return {"result": f"[{tname}] 执行异常: {str(e)}", "endpoint": endpoint}

    tasks = [execute_one(tc) for tc in tool_calls]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    # Unwrap: each item is a dict {"result": str, "endpoint": str|None} or Exception
    tr = []
    for item in raw:
        if isinstance(item, Exception):
            tr.append(str(item))
        elif isinstance(item, dict):
            tr.append(_enrich_with_download_links(item.get("result", ""), item.get("endpoint")))
        else:
            tr.append(str(item))

    for i, r in enumerate(tr):
        logger.info("Tool executor round=%d result[%d]: %s", state.get("tool_round", 0) + 1, i, r[:300])
    result_msgs = []
    for i, tc in enumerate(tool_calls):
        func = tc.get("function", {})
        result_msgs.append({"role": "tool", "tool_call_id": tc.get("id", f"call_{i}"),
                            "name": func.get("name", "unknown"),
                            "content": tr[i] if i < len(tr) else ""})
    return {"tool_results": tr, "tool_messages": result_msgs, "tool_round": state.get("tool_round", 0) + 1}


async def _get_repl_server_config() -> dict | None:
    """Get the python_repl MCP server config by name 'Python执行器'."""
    async with async_session() as db:
        srv = (await db.execute(
            select(MCPServer).where(MCPServer.name == "Python执行器").limit(1)
        )).scalar_one_or_none()
        if not srv:
            return None
        return {"id": srv.id, "transport_type": srv.transport_type, "endpoint": srv.endpoint,
                "command": srv.command, "args_json": srv.args_json, "env_json": srv.env_json,
                "timeout_seconds": srv.timeout_seconds}


# ── Build Context ──

async def build_context_node(state: dict) -> dict:
    return {}

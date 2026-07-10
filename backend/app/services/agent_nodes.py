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
    get_skill_by_id, get_skill_by_folder, get_skill_by_name,
    read_skill_md, parse_skill_md,
    get_skill_resource, list_resource_paths,
)
from app.services.skill_script_loader import discover_tools, execute_script_tool
from app.services.tool_registry import tool_registry
from app.services.kb_service import get_kb_prompt

logger = logging.getLogger("erag.agent")
logger.setLevel(logging.INFO)

MAX_TOOL_ROUNDS = 5
MAX_SKILL_SWITCHES = 4  # Route D: cap on use_skill pushes to prevent runaway chaining


def _try_parse_tool_call(content: str, available_tools: list[dict]) -> list[dict] | None:
    import re
    _clog = logging.getLogger("erag.agent")

    # ── Step 0: strip [TOOL_CALL]...[/TOOL_CALL] wrappers (LLMs sometimes add these) ──
    tool_call_match = re.search(r'\[TOOL_CALL\]([\s\S]*?)\[/TOOL_CALL\]', content, re.IGNORECASE)
    if tool_call_match:
        _clog.info("parse_tool_call: found [TOOL_CALL] wrapper, extracting inner content")
        content = tool_call_match.group(1)

    # Strip code blocks
    cleaned = re.sub(r'```(?:json)?\s*|```', '', content).strip()
    # Strip leading conversational text — models often add greetings before JSON
    # Look for the first '{' and try parsing from there
    obj_match = re.search(r'\{[\s\S]*\}', cleaned)
    if obj_match:
        cleaned = obj_match.group(0)
    if not cleaned.startswith('{'):
        return None

    # ── Step 1: try standard JSON parsing first ──
    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # ── Step 2: if JSON parsing failed, try => (arrow) syntax fixup ──
    if data is None:
        _clog.info("parse_tool_call: JSON parse failed, trying arrow-syntax fixup")
        fixed = _fix_arrow_syntax(cleaned)
        try:
            data = json.loads(fixed)
        except json.JSONDecodeError:
            _clog.info("parse_tool_call: arrow-syntax fixup also failed, content=%.200s", cleaned[:200])
            # ── Step 2b: heuristic extraction — try to extract code from broken JSON ──
            has_python_tool = any(t.get('function', {}).get('name') == 'run_python' for t in available_tools)
            if has_python_tool and ('code' in cleaned.lower() or 'run_python' in cleaned):
                heuristic = _try_heuristic_code_extract(cleaned)
                if heuristic:
                    return heuristic
            return None

    # ── Step 3: dispatch by format ──
    # Format: {"jsonrpc": "2.0", "method": "...", "params": {...}}
    if data.get("jsonrpc") == "2.0" and data.get("method"):
        tool_name = data["method"]
        if any(t.get("function", {}).get("name") == tool_name for t in available_tools):
            return [{"id": f"call_{tool_name}", "type": "function",
                     "function": {"name": tool_name, "arguments": json.dumps(data.get("params", {}), ensure_ascii=False)}}]

    # Format: {"tool": "...", "arguments": {...}}   OR   {"tool": "...", "args": {...}}
    if data.get("tool") and any(t.get("function", {}).get("name") == data["tool"] for t in available_tools):
        tool_args = data.get("arguments") or data.get("args") or data.get("params") or {}
        return [{"id": f"call_{data['tool']}", "type": "function",
                 "function": {"name": data["tool"], "arguments": json.dumps(tool_args, ensure_ascii=False)}}]

    # Format: {"name": "...", "arguments": {...}}
    if data.get("name") and any(t.get("function", {}).get("name") == data["name"] for t in available_tools):
        tool_args = data.get("arguments") or data.get("args") or data.get("params") or {}
        return [{"id": f"call_{data['name']}", "type": "function",
                 "function": {"name": data["name"], "arguments": json.dumps(tool_args, ensure_ascii=False)}}]

    return None


def _fix_arrow_syntax(text: str) -> str:
    """Convert Ruby-style => arrows and JS-style unquoted keys to valid JSON.

    Handles patterns like:
        {tool => "run_python", args => {code: "...", timeout: 15}}
        {tool => "run_python", args => {"--code": "..."}}
        {tool => 'run_python', args => {code => 'print(1)'}}

    Strategy:
      1. Convert => to :
      2. Quote unquoted keys
      3. Remove -- prefix hallucination
      4. Try ast.literal_eval for single-quoted values (Python dict literal)
      5. Fallback: safe single-to-double quote for simple values only
    """
    import re, ast
    result = text

    # 1. Convert => to :
    result = re.sub(r'\s*=>\s*', ': ', result)

    # 2. Quote unquoted keys: word: → "word":
    #    Handles keys after { or , even across newlines.
    result = re.sub(r'([\{,])\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*:', r'\1 "\2":', result)

    # 3. Remove -- prefix from keys that LLM might hallucinate
    result = re.sub(r'"--([a-zA-Z_][a-zA-Z0-9_]*)"', r'"\1"', result)

    # 4. Try ast.literal_eval — can handle mixed single/double quote dicts
    #    as long as the code string inside is properly Python-escaped.
    try:
        data = ast.literal_eval(result)
        return json.dumps(data, ensure_ascii=False)
    except (SyntaxError, ValueError):
        pass

    # 5. Safe single→double quote: only for values that contain no nested quotes.
    #    Uses a callback to escape any double-quotes found inside the value.
    def _safe_value(m):
        inner = m.group(1)
        inner = inner.replace('\\', '\\\\').replace('"', '\\"')
        return '"' + inner + '"'

    # Only match single-quoted values at VALUE positions (after colon + optional whitespace)
    # This avoids matching single quotes inside already-double-quoted JSON values.
    result = re.sub(r":\s*'([^']*)'", lambda m: ': "' + m.group(1).replace('\\', '\\\\').replace('"', '\\"') + '"', result)

    return result


def _try_heuristic_code_extract(content: str) -> list[dict] | None:
    """Last-resort: extract code from broken JSON/arrow-syntax tool call.

    Called when json.loads and _fix_arrow_syntax both failed, but the content
    clearly looks like a run_python tool call with embedded code.

    Strategy: find the 'code' key and extract everything between its value
    delimiters and the closing braces. Handles single/double quotes.
    """
    import re as _hre
    import logging as _hlog
    _clog = _hlog.getLogger("erag.agent")

    patterns = [
        r"""code\s*[:=]>\s*'([\s\S]+?)'\s*\}?\s*\}?\s*$""",
        r'code\s*[:=]>\s*"([\s\S]+?)"\s*\}?\s*\}?\s*$',
        r'''''"code"\s*:\s*'([\s\S]+?)'\s*\}?\s*\}?\s*$''''',
    ]

    for pat in patterns:
        m = _hre.search(pat, content)
        if m:
            code = m.group(1).strip()
            code = code.replace('\\n', '\n').replace('\\t', '\t')
            code = code.replace('\\"', '"').replace("\\'", "'")
            code = code.replace('\\\\', '\\')
            if code and len(code) > 10:
                _clog.info("heuristic_extract: found code, len=%d", len(code))
                return [{"id": "call_run_python_heuristic", "type": "function",
                         "function": {"name": "run_python",
                                      "arguments": json.dumps({"code": code}, ensure_ascii=False)}}]
    _clog.info("heuristic_extract: no code found")
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
    kb_prompt = await get_kb_prompt(kb_id)
    if not state.get("skip_cache"):
        cached = answer_cache.get(query, kb_id, skill_id=skill_id or "", kb_prompt=kb_prompt)
        if cached:
            return {"cache_hit": True, "final_answer": cached.answer, "citations": cached.citations or [], "tool_results": [], "tool_messages": [], "kb_prompt": kb_prompt}

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
            "cache_hit": False, "tool_round": 0, "tool_results": [], "tool_messages": [],
            "kb_prompt": kb_prompt}


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

def _build_meta_skill_tools() -> list[dict]:
    """Always-available meta tools that let the LLM orchestrate skills (Route D).

    These are injected into available_tools whenever a skill is loaded, so the
    LLM can list skills and chain into another skill mid-conversation.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "list_skills",
                "description": "列出当前可用的所有技能（名称与描述）。当需要决定使用哪个技能、或想确认某个技能是否存在时调用。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            "_source": "meta",
        },
        {
            "type": "function",
            "function": {
                "name": "use_skill",
                "description": (
                    "加载并使用另一个技能（例如「文档生成助手」）。加载后该技能的规则与工具立即生效，"
                    "当前对话即可调用其能力。适用于当前技能无法直接完成的子任务"
                    "（如「PPT美化」需要先有 PPT 文件，可临时 use_skill「文档生成助手」生成后再返回美化）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "要使用的技能名称，例如「文档生成助手」。可先调用 list_skills 查看可用技能。",
                        },
                        "reason": {
                            "type": "string",
                            "description": "调用该技能的原因/目的，例如「需要先生成 PPT 文档，再返回进行美化」。会展示给用户作为处理过程。",
                        },
                    },
                    "required": ["skill_name"],
                },
            },
            "_source": "meta",
        },
        {
            "type": "function",
            "function": {
                "name": "done_skill",
                "description": (
                    "结束当前临时技能，返回到上一层技能（例如用「文档生成助手」生成文件后，"
                    "调用 done_skill 回到「PPT美化」继续美化）。无需返回时不必调用。"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            "_source": "meta",
        },
    ]


async def _load_skill_body_and_tools(folder_name: str) -> tuple[str, list[dict]]:
    """Load a skill's SKILL.md body + tools.

    Shared by the initial skill_loader_node and Route D chaining (skill_switcher_node).
    Returns (system_prompt, tools) where tools already include the
    read_skill_resource tool when the skill has reference/data files.
    """
    skill_md_content = read_skill_md(folder_name)
    if not skill_md_content:
        logger.warning("_load_skill_body_and_tools: SKILL.md not found for folder=%s", folder_name)
        return config_manager.system_prompt, []

    parsed = parse_skill_md(skill_md_content)
    system_prompt = parsed["body"] or config_manager.system_prompt
    mcp_server_names = parsed.get("mcp_servers", [])

    script_tools = discover_tools(folder_name)
    logger.info("_load_skill_body_and_tools: folder=%s script_tools=%d", folder_name, len(script_tools))

    mcp_tools = await tool_registry.get_mcp_tools(mcp_server_names)
    logger.info("_load_skill_body_and_tools: folder=%s mcp_tools=%d (servers=%s)",
                folder_name, len(mcp_tools), mcp_server_names)

    all_tools = script_tools + mcp_tools

    # Layer 3: add read_skill_resource tool if skill has reference/data files
    resource_paths = list_resource_paths(folder_name)
    if resource_paths:
        path_list = "\n".join(f"  - {p}" for p in resource_paths[:20])
        more = f"\n  ... and {len(resource_paths) - 20} more" if len(resource_paths) > 20 else ""
        resource_tool = {
            "type": "function",
            "function": {
                "name": "read_skill_resource",
                "description": f"Read a resource file from the skill folder. Available files:\n{path_list}{more}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path to the resource file (e.g. 'references/guide.md', 'data/config.json')",
                        },
                    },
                    "required": ["path"],
                },
            },
            "_source": "resource",
            "_folder_name": folder_name,
        }
        all_tools.append(resource_tool)
        logger.info("_load_skill_body_and_tools: folder=%s added read_skill_resource tool (%d files)",
                    folder_name, len(resource_paths))

    return system_prompt, all_tools


# ── Agent-step streaming (Route D observability) ──
_TOOL_LABELS = {
    "run_python": "执行 Python 脚本",
    "read_skill_resource": "读取技能资料",
}


def _emit(state: dict, stage: str, message: str, **extra) -> None:
    """Push an agent_step progress event to the SSE stream, if a callback is wired.

    Must never raise — a broken emit must not interrupt the agent graph.
    """
    fn = state.get("emit")
    if not fn:
        return
    try:
        fn(stage, message, **extra)
    except Exception:
        pass


async def skill_loader_node(state: dict) -> dict:
    """Layer 2 — load SKILL.md full text, discover script tools, load MCP tools.

    Runs after router (if a skill was selected) and before retrieval.
    Also initialises the Route D skill stack and injects the always-available
    meta tools (list_skills / use_skill / done_skill) for orchestration.
    """
    if state.get("cache_hit"):
        return {}

    active_skill = state.get("active_skill")
    if not active_skill:
        return {}

    folder_name = active_skill.get("folder_name")
    if not folder_name:
        return {}

    system_prompt, all_tools = await _load_skill_body_and_tools(folder_name)
    updated_skill = {**active_skill, "system_prompt": system_prompt, "source": "primary"}

    # Always-available meta tools for orchestration (Route D)
    all_tools = all_tools + _build_meta_skill_tools()

    _emit(state, "skill_load", f"已加载技能：{active_skill.get('name', '?')}", skill=active_skill.get("name"))

    return {
        "active_skill": updated_skill,
        "available_tools": all_tools,
        "skill_stack": [updated_skill],
        "loaded_skill_ids": [active_skill["id"]] if active_skill.get("id") else [],
        "skill_switch_count": 0,
    }


# ── Skill Switcher (Route D: stack-based chaining) ──

_META_CONTROL_TOOLS = {"use_skill", "done_skill", "list_skills"}


def _skill_control_return(tc: dict, result: str, stack: list[dict], state: dict) -> dict:
    """Build a no-op state update for a meta control call (stack unchanged)."""
    top = stack[-1] if stack else state.get("active_skill")
    return {
        "active_skill": top,
        "skill_stack": stack,
        "tool_results": [result],
        "tool_messages": [{
            "role": "tool",
            "tool_call_id": tc.get("id", "call_meta"),
            "name": tc.get("function", {}).get("name", "meta"),
            "content": result,
        }],
        "tool_round": state.get("tool_round", 0) + 1,
    }


async def skill_switcher_node(state: dict) -> dict:
    """Route D — handle use_skill / done_skill / list_skills control calls.

    * use_skill(name): push the target skill onto the stack, swap its system
      prompt in as the active one, and UNION its tools into available_tools.
      The previous skill's body is dropped from the prompt (stack model) so the
      prompt stays short and only one skill's rules are "in force" at a time,
      while all tools accumulated so far remain callable.
    * done_skill(): pop the stack, returning to the previous skill's prompt.
    * list_skills(): return the active skill catalogue without changing state.
    """
    tool_calls = state.get("tool_calls", []) or []
    if not tool_calls:
        return {}
    tc = tool_calls[0]
    fname = tc.get("function", {}).get("name", "")
    try:
        args = json.loads(tc.get("function", {}).get("arguments", "{}") or "{}")
    except Exception:
        args = {}

    stack = list(state.get("skill_stack") or [])
    loaded = list(state.get("loaded_skill_ids") or [])
    switch_count = state.get("skill_switch_count", 0)
    tenant_id = state.get("tenant_id")

    async with async_session() as db:
        # ── list_skills ──
        if fname == "list_skills":
            if tenant_id:
                skills = (await db.execute(
                    select(Skill).where(
                        (Skill.tenant_id == tenant_id) & (Skill.is_active == True)  # noqa: E712
                    )
                )).scalars().all()
            else:
                skills = (await db.execute(
                    select(Skill).where(Skill.is_active == True)  # noqa: E712
                )).scalars().all()
            skill_list = "\n".join(f"- {s.name}: {s.description or '(无描述)'}" for s in skills) or "(无可用技能)"
            result = f"当前可用技能：\n{skill_list}"
            return _skill_control_return(tc, result, stack, state)

        # ── done_skill ──
        if fname == "done_skill":
            if len(stack) <= 1:
                result = "done_skill：已经是最顶层技能，没有可返回的上一层。"
                return _skill_control_return(tc, result, stack, state)
            stack = stack[:-1]
            prev = stack[-1]
            result = f"已返回上一层技能：「{prev.get('name')}」。其规则与工具现已生效。"
            _emit(state, "skill_return", f"返回上一层技能：「{prev.get('name')}」", skill=prev.get("name"))
            return {
                "active_skill": prev,
                "skill_stack": stack,
                "tool_results": [result],
                "tool_messages": [{
                    "role": "tool",
                    "tool_call_id": tc.get("id", "call_meta"),
                    "name": fname,
                    "content": result,
                }],
                "tool_round": state.get("tool_round", 0) + 1,
            }

        # ── use_skill ──
        if fname == "use_skill":
            name = (args.get("skill_name") or "").strip()
            if not name:
                _emit(state, "skill_switch_fail", "use_skill：未提供 skill_name。")
                return _skill_control_return(tc, "use_skill：未提供 skill_name。", stack, state)
            if switch_count >= MAX_SKILL_SWITCHES:
                result = f"use_skill：已达技能切换上限（{MAX_SKILL_SWITCHES}），无法加载「{name}」。"
                _emit(state, "skill_switch_fail", result, skill=name)
                return _skill_control_return(tc, result, stack, state)
            skill = await get_skill_by_name(db, name, tenant_id)
            if not skill or not skill.is_active:
                result = f"use_skill：未找到可用技能「{name}」（可先调用 list_skills 查看）。"
                _emit(state, "skill_switch_fail", result, skill=name)
                return _skill_control_return(tc, result, stack, state)
            if skill.id in loaded:
                result = f"use_skill：技能「{skill.name}」已在生效栈中，无需重复加载。"
                _emit(state, "skill_switch_fail", result, skill=skill.name)
                return _skill_control_return(tc, result, stack, state)

            system_prompt, new_tools = await _load_skill_body_and_tools(skill.folder_name)
            new_skill = {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "folder_name": skill.folder_name,
                "system_prompt": system_prompt,
                "source": "chained",
            }

            # Union tools (dedup by name) so previously-loaded tools persist.
            existing = state.get("available_tools", []) or []
            existing_names = {t.get("function", {}).get("name") for t in existing}
            added = [t for t in new_tools if t.get("function", {}).get("name") not in existing_names]
            union_tools = existing + added
            added_names = [t.get("function", {}).get("name") for t in added]

            stack = stack + [new_skill]
            result = (
                f"已加载技能「{skill.name}」，新增工具：{added_names or '（无新工具）'}。"
                "其规则现已生效，可直接调用相关工具；如需结束该技能请调用 done_skill 返回上一层。"
            )
            logger.info("Skill switcher: use_skill '%s' → stack depth=%d, added_tools=%d",
                        skill.name, len(stack), len(added))
            reason = (args.get("reason") or "").strip()
            switch_msg = f"切换并加载「{skill.name}」"
            if reason:
                switch_msg += f"：{reason}"
            _emit(state, "skill_switch", switch_msg, skill=skill.name, reason=reason)
            return {
                "active_skill": new_skill,
                "skill_stack": stack,
                "loaded_skill_ids": loaded + [skill.id],
                "skill_switch_count": switch_count + 1,
                "available_tools": union_tools,
                "tool_results": [result],
                "tool_messages": [{
                    "role": "tool",
                    "tool_call_id": tc.get("id", "call_meta"),
                    "name": fname,
                    "content": result,
                }],
                "tool_round": state.get("tool_round", 0) + 1,
            }

    # Unknown control tool — should not happen, but fail safe.
    result = f"未知元工具：{fname}"
    _emit(state, "skill_switch_fail", result)
    return _skill_control_return(tc, result, stack, state)


# ── Retrieval ──

async def parallel_retrieval_node(state: dict) -> dict:
    if state.get("cache_hit"):
        return {}
    _emit(state, "retrieval", "检索知识库…")
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
    chunk_count = len(retrieved) if isinstance(retrieved, list) else 0
    _emit(state, "retrieval_done", f"检索完成，命中 {chunk_count} 段", detail=f"{chunk_count} 段")
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
    tool_round = state.get("tool_round", 0)
    prev_results = state.get("tool_results", [])
    # ── Force WARNING for debugging: print state on every entry ──
    logger.warning("🔍 tool_decision ENTER: round=%d tools=%d prev_results=%d cache=%s",
                   tool_round, len(available_tools), len(prev_results),
                   state.get("cache_hit"))
    if not available_tools:
        logger.warning("Tool decision: no available tools — skipping tool phase")
        return {"tool_calls": None}
    if tool_round >= MAX_TOOL_ROUNDS:
        logger.warning("Tool decision: max rounds reached (round=%d, max=%d)", tool_round, MAX_TOOL_ROUNDS)
        return {"tool_calls": None}

    # ── Error guard: only stop the tool loop when ALL results are errors
    # AND we've already given the LLM at least one chance to fix (tool_round >= 2).
    # A single error on the first attempt is often fixable by the LLM on retry.
    prev_results = state.get("tool_results", [])
    if prev_results and all("error" in r.lower() or "异常" in r for r in prev_results):
        if tool_round >= 2:
            logger.warning("Tool decision: all tools errored after %d rounds, stopping loop. Last error: %.200s",
                          tool_round, prev_results[-1][:200])
            return {"tool_calls": None}
        else:
            logger.info("Tool decision: tool errored (round %d), giving LLM one chance to fix. Error: %.200s",
                       tool_round, prev_results[-1][:200])
    active = state.get("active_skill") or {}
    skill_prompt = active.get("system_prompt", config_manager.system_prompt)
    kb_prompt = state.get("kb_prompt") or ""
    if not kb_prompt:
        kb_prompt = await get_kb_prompt(state["kb_id"])
    kb_context = f"\n\n## 知识库背景与偏好\n{kb_prompt}" if kb_prompt else ""
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
            + "- **必须**使用双引号（\"），**绝对不能**使用单引号（'）\n"
            + "- **绝对不能**使用 => 箭头语法，必须是 JSON 标准的 : 冒号\n"
            + "- 不要用 ``` 包裹 JSON\n"
            + "- 不要输出 [TOOL_CALL] 或 <tool_call> 标签\n"
            + "- 代码参数中的双引号需用 \\\" 转义，换行用 \\n\n"
            + "- 不要输出最终回复——那是下一阶段的事\n"
            + "- **绝对不要**编造File、文件路径或 uuid"
        )
        messages = [
            {"role": "system", "content": tool_system},
            {"role": "system", "content": "## 任务背景（仅供参考）\n" + skill_prompt + kb_context},
        ]
    else:
        messages = [{"role": "system", "content": skill_prompt + kb_context}]
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
        # ── Sanitize tool_messages before sending to LLM ──
        # TokenHub validates that assistant.tool_calls[].function.arguments is valid JSON.
        # If the LLM's output was truncated by max_tokens, the arguments string may be
        # incomplete JSON → TokenHub returns 400 on the next round.
        # Fix: validate each tool_call's arguments; if invalid, replace with error stub.
        sanitized_msgs = []
        for msg in tool_messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                clean_tcs = []
                for tc in msg["tool_calls"]:
                    args_str = tc.get("function", {}).get("arguments", "")
                    try:
                        json.loads(args_str)
                        clean_tcs.append(tc)  # valid JSON, keep as-is
                    except json.JSONDecodeError:
                        logger.warning("Tool decision: dropping invalid tool_call arguments (truncated?), preview=%.100s", args_str[:100])
                        clean_tcs.append({
                            "id": tc.get("id", "call_invalid"),
                            "type": "function",
                            "function": {
                                "name": tc.get("function", {}).get("name", "unknown"),
                                "arguments": json.dumps({"error": "arguments were truncated/invalid"})
                            }
                        })
                sanitized_msgs.append({**msg, "tool_calls": clean_tcs})
            else:
                sanitized_msgs.append(msg)
        messages.extend(sanitized_msgs)
    try:
        # ── Dual-mode strategy: try native function calling first, fall back to text mode ──
        # TokenHub officially supports tools + tool_choice="auto", but some models may
        # have compatibility issues. We try chat_with_tools first; on 400/error,
        # we fall back to chat() + prompt-based JSON parsing.
        tool_calls = None
        content = ""

        try:
            logger.warning("Tool decision: trying chat_with_tools (native), %d tools, round=%d",
                       len(available_tools), tool_round)
            response = await llm_client.chat_with_tools(
                messages=messages, tools=available_tools,
                temperature=0.1, max_tokens=config_manager.agent_max_tokens, tool_choice="auto",
            )
            tool_calls = response.get("tool_calls")
            content = response.get("content") or ""
            logger.warning("Tool decision: native_tool_calls=%s content_preview=%.200s",
                       bool(tool_calls), content[:200])
        except Exception as native_err:
            logger.warning("Tool decision: chat_with_tools failed (%s), falling back to text mode", str(native_err)[:200])
            content = await llm_client.chat(messages=messages, temperature=0.1, max_tokens=config_manager.agent_max_tokens)
            logger.warning("Tool decision: text mode content_preview=%.200s", content[:200])

        if not content and not tool_calls:
            logger.warning("Tool decision: LLM returned empty content and no tool_calls")
            return {"tool_calls": None}

        # Try structured JSON parsing from content (handles [TOOL_CALL], =>, etc.)
        if not tool_calls and content:
            parsed = _try_parse_tool_call(content, available_tools)
            if parsed:
                logger.warning("Tool decision: parsed tool_calls from JSON in content (round %d)", tool_round)
                tool_calls = parsed
                content = ""

        # Fallback: try extracting Python code blocks from LLM response
        if not tool_calls and content:
            logger.warning("Tool decision: no JSON tool call (round %d), trying code extraction", tool_round)
            code_tool = _try_extract_code_as_tool(content, available_tools)
            if code_tool:
                logger.warning("Tool decision: extracted code from LLM response, built run_python call")
                tool_calls = code_tool
                content = ""
            else:
                logger.info("Tool decision: code extraction yielded nothing (round %d)", tool_round)

        if tool_calls:
            tool_msg = {"role": "assistant", "content": content or "", "tool_calls": tool_calls}
            _emit(state, "round", f"第 {tool_round + 1} 轮工具调用")
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

    Generates download URLs pointing to ERAG's own /api/download/{uuid}/ proxy
    endpoint, so the user only needs access to ERAG — the MCP server stays
    fully internal with no host port exposure.
    """
    m = _re.search(r'\[workspace:\s*([a-f0-9]{8})/\]', result)
    if not m:
        return result
    uuid_dir = m.group(1)

    from app.config import settings
    public_base = settings.public_url.rstrip("/") if settings.public_url else ""
    proxy_prefix = f"{public_base}/api/download/{uuid_dir}" if public_base else f"/api/download/{uuid_dir}"

    if "[File]" in result:
        # MCP server included [File] tags with its own URL — rewrite to ERAG proxy
        result = _re.sub(
            r'(?<=\[File\] )\S+/files/' + uuid_dir + r'/(\S+)',
            f'{proxy_prefix}/\\1',
            result
        )
    # If MCP didn't generate [File] links (missing REPL_PUBLIC_URL),
    # we don't fabricate broken links — let the result speak for itself.

    return result

def _extract_download_links_from_state(state: dict) -> str:
    """Scan tool results for download links and format them for final display.

    This runs OUTSIDE the LLM — links are system-generated, never hallucinated.
    Formats [File] tags as clickable Markdown links the frontend can render.
    Supports both absolute (https://...) and relative (/api/download/...) URLs.
    """
    tool_results = state.get("tool_results", [])
    links = []
    seen = set()
    for r in tool_results:
        # Match both absolute and relative [File] URLs
        for url_match in _re.finditer(
            r'\[File\]\s*((?:https?://\S+|/api/download/\S+))', r
        ):
            url = url_match.group(1)
            if url not in seen:
                seen.add(url)
                filename = url.rstrip("/").rsplit("/", 1)[-1]
                links.append(f"- [📥 {filename}]({url})")
    if links:
        return "\n\n---\n" + "\n".join(links)
    return ""

async def tool_executor_node(state: dict) -> dict:
    tool_calls = state.get("tool_calls", [])
    logger.warning(">>> tool_executor ENTER: tool_calls=%d round=%d <<<",
                   len(tool_calls), state.get("tool_round", 0))
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
        label = _TOOL_LABELS.get(tname, tname)
        _emit(state, "tool", f"执行工具：{label}", tool=tname)

        tool_def = tool_lookup.get(tname, {})
        tool_source = tool_def.get("_source", "mcp")

        # ── Meta control tools should never reach the executor ──
        if tool_source == "meta":
            return {"result": f"[{tname}] 元工具不应在工具执行阶段调用", "endpoint": None}

        # ── Script tool path ──
        if tool_source == "script" and folder_name:
            script_path = tool_def.get("_script_path", "")
            func_name = tool_def.get("_func_name", tname)
            # Find the python_repl MCP server config
            repl_config = await _get_repl_server_config()
            if not repl_config:
                return {"result": f"[{tname}] 错误: Python执行器 MCP Server 未配置", "endpoint": None}
            result = await execute_script_tool(
                folder_name, script_path, func_name, args, repl_config,
                workspace_id=state.get("workspace_id"),
            )
            if result.ok:
                return {"result": f"[{tname}] {result.result}", "endpoint": repl_config.get("endpoint")}
            return {"result": f"[{tname}] 错误: {result.error}", "endpoint": repl_config.get("endpoint")}

        # ── Resource tool path (Layer 3: on-demand) ──
        if tool_source == "resource" and folder_name:
            resource_path = args.get("path", "")
            content = get_skill_resource(folder_name, resource_path)
            if content:
                preview = content[:2000]
                truncated = " ... (truncated)" if len(content) > 2000 else ""
                logger.info("Resource loaded: %s/%s (%d chars)", folder_name, resource_path, len(content))
                return {
                    "result": f"[{tname}] Resource '{resource_path}' ({len(content)} chars):\n\n{preview}{truncated}",
                    "endpoint": None,
                }
            return {"result": f"[{tname}] Resource '{resource_path}' not found in skill folder", "endpoint": None}

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
            # Share the conversation workspace so chained skills can read
            # files produced by an earlier skill's tool call.
            call_args = dict(args)
            ws_id = state.get("workspace_id")
            if ws_id:
                call_args["workspace_id"] = ws_id
            res = await _mc.call_tool(cfg, tname, call_args)
            logger.warning(">>> tool_executor RESULT: tool=%s ok=%s result=%.200s <<<",
                          tname, res.ok, (res.result or res.error)[:200])
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
        fm = _re.search(r'\[File\]\s*((?:https?://\S+|/api/download/\S+))', r)
        if fm:
            fname = fm.group(1).rstrip("/").rsplit("/", 1)[-1]
            tcname = tool_calls[i].get("function", {}).get("name", "unknown") if i < len(tool_calls) else "unknown"
            _emit(state, "tool_done", f"已生成文件：{fname}", tool=tcname, detail=fname)
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

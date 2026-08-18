"""Agent graph nodes for the RAGClaw LangGraph state machine."""
import asyncio, json, logging, re, time
from datetime import datetime
from urllib.parse import quote, unquote
from sqlalchemy import select
from app.config import settings
from app.database import async_session
from app.models.skill import Skill, MCPServer
from app.services.hybrid_search import hybrid_search
from app.services.vector_store import vector_store
from app.services.bm25_index import bm25_index
from app.services import memory_archive
from app.services.llm_client import llm_client
from app.services.config_manager import config_manager
from app.services.cache import answer_cache
from app.services.skill_manager import (
    get_skill_by_id, get_skill_by_folder, get_skill_by_name,
    is_skill_effectively_enabled,
    read_skill_md, parse_skill_md, read_ragclaw_skill_doc,
    get_skill_resource, list_resource_paths,
)
from app.services.skill_script_loader import discover_tools, execute_script_tool
from app.services.tool_registry import tool_registry
from app.services.kb_service import get_kb_prompt
from app.services.token_count import count_messages_tokens
from app.services.conversation_summary import (
    MEM_CHUNK_DELIM,
    context_breakdown,
    fit_assembly_context,
)

logger = logging.getLogger("ragclaw.agent")
logger.setLevel(logging.INFO)

# ── Agent tool-decision output cap ──
# Hard ceiling (safety rail against runaway generation) of 32768 tokens; but
# never allowed to exceed the *remaining* context window, so we don't 400 on
# small-context models. effective = min(32768, context_window - input - 256).
AGENT_MAX_TOKENS_HARD_CAP = 32768
AGENT_MAX_TOKENS_SAFETY_MARGIN = 256


# ── Current-date note for the system prompt ──
def _current_date_note(tz_str: str | None) -> str:
    """Render a 'current date' note in the user's timezone for the system prompt.

    The LLM must anchor relative-time requests ('today', 'this year') to the real
    current date instead of guessing a year from training data. Computed in the
    user's IANA timezone (state['timezone']) with a UTC fallback.
    """
    tz_name = tz_str or "UTC"
    try:
        from datetime import datetime as _dt
        import pytz as _pytz
        now = _dt.now(_pytz.timezone(tz_name))
    except Exception:
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        tz_name = "UTC"
    return _t(
        "current_date_note",
        config_manager.prompt_language,
        date=now.strftime("%Y-%m-%d"),
        tz=tz_name,
    )


def _compute_agent_max_tokens(messages: list[dict]) -> int:
    """Output cap (tokens) for a single Agent tool-decision response.

    Only bounds the model-written function-call arguments, not tool results.
    Uses min(HARD_CAP, remaining context) so large-arg tools don't truncate
    while small-context models stay safely under their window.
    """
    context_window = config_manager.context_window
    input_tokens = count_messages_tokens(messages)
    remaining = context_window - input_tokens - AGENT_MAX_TOKENS_SAFETY_MARGIN
    if remaining < AGENT_MAX_TOKENS_SAFETY_MARGIN:
        remaining = AGENT_MAX_TOKENS_SAFETY_MARGIN
    return min(AGENT_MAX_TOKENS_HARD_CAP, remaining)

# ── Tool-call robustness (3-layer defense against malformed tool calls) ──
# Layer 1: force a named tool via tool_choice dict when intent is unambiguous.
# Layer 2: self-heal retry — on parse failure, ask the LLM to rewrite as valid JSON.
# Layer 3: regex fallback parser (see _try_parse_tool_call / _try_heuristic_code_extract).
SELF_HEAL_MAX_RETRIES = 2          # max self-heal rewrite attempts on parse failure
TRANSIENT_RETRY_MAX = 3            # max retries on transient upstream errors (502)
TRANSIENT_BACKOFF_BASE = 0.5       # seconds; exponential backoff base for 502 retries
# Keywords that strongly imply a run_python call (file generation / code / compute).
_FORCE_PY_KEYWORDS = (
    "生成", "创建", "写入", "保存", "导出", "输出文件", "下载",
    "运行代码", "执行代码", "跑一下", "计算", "数据处理", "画图", "绘图", "统计",
    "generate", "create", "write", "save", "export", "download",
    "run code", "execute", "compute", "plot", "chart", "csv", "excel", "txt",
    # CRUD on workspace files (read / update / delete) -> force run_python too
    "读取文件", "查看文件", "读文件", "修改文件", "更新文件", "编辑文件",
    "改动文件", "删除文件", "删掉文件", "移除文件", "重命名", "移动文件",
    "read file", "view file", "edit file", "update file", "modify file",
    "delete file", "remove file", "rename", "move file",
)

# Query keywords that signal the user wants to create / write / save / run a
# file or code. Used as a routing fallback so file/code-generation intent does
# not depend on the LLM returning an exact skill name — claw's native run_python
# meta-tool handles these directly once the agent decides to call it.
_ROUTE_FILE_INTENT_KEYWORDS = (
    "生成文件", "创建文件", "写文件", "保存文件", "新建文件", "写入文件",
    "生成文档", "写文档", "创建文档", "新建文档", "保存为文件", "保存为",
    "导出文件", "导出为", "生成excel", "生成表格", "生成csv", "导出csv",
    "运行代码", "执行代码", "运行python", "执行python", "跑代码", "生成代码",
    "写个文件", "写个文档", "脚本文件",
    "write file", "create file", "generate file", "save file", "run code",
    "execute code", "export file", "new file",
)


# ── Bilingual Agent-Graph prompts ──
# Prompt text now lives in app/services/i18n (mirrors frontend/src/i18n):
# zh_cn.py / en_us.py hold the templates; t() resolves by id + locale.
# These wrappers keep the original function names/signatures so call sites
# (build_intent_router_prompt / build_tool_system_prompt
# / build_selfheal_prompt) are unchanged. lang='zh' (config_manager.prompt_language
# default) reproduces the original Chinese behavior; lang='en' selects the English A/B variants.
from app.services.i18n import t as _t


def build_intent_router_prompt(query: str, skill_list: str, lang: str = "zh") -> str:
    """Layer-1 intent-router prompt. lang='zh' reproduces the original behavior."""
    return _t("intent_router", lang, query=query, skill_list=skill_list)


def build_tool_system_prompt(tool_desc: str, lang: str = "zh") -> str:
    """Forced tool-call JSON system prompt. lang='zh' reproduces the original behavior."""
    return _t("tool_system", lang, tool_desc=tool_desc)


def _try_parse_tool_call(content: str, available_tools: list[dict]) -> list[dict] | None:
    import re
    _clog = logging.getLogger("ragclaw.agent")

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
    _clog = _hlog.getLogger("ragclaw.agent")

    patterns = [
        # --code "VALUE" / code "VALUE" — LLMs sometimes emit a --code flag
        # with unescaped inner quotes. VALUE is the last quoted field before the
        # closing braces, so capture greedily up to the final quote.
        r'(?:--code|code)\s*[:=]?>?\s*"([\s\S]+)"\s*\}?\s*\}?\s*$',
        r"(?:--code|code)\s*[:=]?>?\s*'([\s\S]+)'\s*\}?\s*\}?\s*$",
        # code => "VALUE" / code => 'VALUE' (arrow syntax)
        r'code\s*[:=]>\s*"([\s\S]+?)"\s*\}?\s*\}?\s*$',
        r"""code\s*[:=]>\s*'([\s\S]+?)'\s*\}?\s*\}?\s*$""",
        # "code": "VALUE"
        r'"code"\s*:\s*"([\s\S]+?)"\s*\}?\s*\}?\s*$',
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
    _clog = logging.getLogger("ragclaw.agent")
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


# ── Tool-call robustness helpers (Layer 1 + Layer 2) ──

def _tool_names(available_tools: list[dict]) -> list[str]:
    """Return the list of callable tool names from an OpenAI-format tool list."""
    names = []
    for t in available_tools:
        n = t.get("function", {}).get("name")
        if n:
            names.append(n)
    return names


def _infer_forced_tool(query: str, available_tools: list[dict],
                       active_skill: dict | None) -> str | None:
    """Layer 1: decide whether to force a specific tool via tool_choice dict.

    Returns a tool name to force, or None to keep tool_choice="auto".

    Priority:
      1. Skill-declared force_tool (active_skill["force_tool"]) if that tool is available.
      2. Keyword inference: file-generation / code / compute intent -> run_python.

    Forcing is intentionally conservative: it only fires when the tool is
    actually available AND the intent is unambiguous, so pure chit-chat still
    goes through tool_choice="auto".
    """
    names = set(_tool_names(available_tools))
    if not names:
        return None
    # 1. Skill-declared preference
    if active_skill:
        ft = active_skill.get("force_tool")
        if ft and ft in names:
            return ft
    # 2. Keyword inference -> run_python
    if "run_python" in names:
        q = (query or "").lower()
        if any(kw.lower() in q for kw in _FORCE_PY_KEYWORDS):
            return "run_python"
    return None

def _extract_intended_tool_name(content: str, available_tools: list[dict]) -> str | None:
    """Layer 2: from malformed output, guess which tool the LLM intended to call.

    Even when the JSON/format is broken, the tool name is usually present as a
    substring (e.g. `{tool => "run_python", ...}`). Return the first available
    tool name found in the content, or None.
    """
    if not content:
        return None
    for name in _tool_names(available_tools):
        if name and name in content:
            return name
    return None


def _strip_tool_call_noise(content: str) -> str:
    """Strip raw [TOOL_CALL] wrapper text and stray --code / => fragments from an
    assistant message's content.

    When the model emits native tool_calls it sometimes ALSO dumps a raw
    `[TOOL_CALL] ... [/TOOL_CALL]` block (or a `--code "..."` shell-style fragment)
    into the content field. That text must never leak into the visible chat or the
    tool-execution history, so we remove it here. Any clean preamble the model
    added (e.g. an acknowledgment like "sure, generating the file") is preserved.
    """
    if not content:
        return content
    import re
    cleaned = re.sub(r'\[TOOL_CALL\][\s\S]*?\[/TOOL_CALL\]', '', content, flags=re.IGNORECASE)
    # Remove a leftover bare tool-call object like {tool => "run_python", args => {...}}
    cleaned = re.sub(r'\{\s*tool\s*=>(?:\s*args)?\s*=>?\s*\{[\s\S]*?\}\s*\}', '', cleaned,
                     flags=re.IGNORECASE)
    # Remove stray --code / --flag "..." or '...' fragments (with or without a
    # space/dash between the flag and its quoted value)
    cleaned = re.sub(r'--?[A-Za-z_]+(?:\s*=\s*|\s+)?(?:"[^"]*"|\'[^\']*\')', '', cleaned)
    return cleaned.strip()


def build_selfheal_prompt(tool_name: str, bad_output: str, lang: str = "zh") -> str:
    """Layer 2: instruction that asks the LLM to rewrite a malformed tool call
    as strictly valid JSON. The prior bad output is fed back as context.
    lang='zh' reproduces the original behavior; lang='en' selects the English
    A/B variant. Template lives in app/services/i18n (key: 'selfheal').
    """
    snippet = (bad_output or "")[:1500]
    return _t("selfheal", lang, tool_name=tool_name, snippet=snippet)


def _build_working_dir_prompt(state: dict) -> str:
    """Return an English note describing the user's selected working directory.

    Injected into the LLM system prompt so file/code operations land in the
    correct place. ``state["subdir"]`` is the user-selected sub-directory
    (relative to their sandbox root; "" = root) — it never contains the per-user
    Linux uid, which the REPL sandbox resolves server-side, so it is safe to
    surface to the model. Written in English per explicit request.
    """
    ws = (state.get("subdir") or "").strip()
    if ws:
        return (
            "\n\n## Working Directory\n"
            f"The sandbox root is your working directory. The user has selected the sub-directory '{ws}'. "
            f"The runtime does NOT auto-change into it, so address files with the '{ws}/' path prefix "
            f"(e.g. open('{ws}/report.pdf')) or call os.chdir('{ws}') at the start of your code. "
            "All read/write/run operations resolve relative to the sandbox root."
        )
    return (
        "\n\n## Working Directory\n"
        "The sandbox root is your working directory (no sub-directory selected). "
        "Perform all file read, write, and run operations relative to this root directory."
    )


async def _chat_with_tools_resilient(
    messages: list[dict], tools: list[dict], tool_choice,
    temperature: float, max_tokens: int,
) -> dict:
    """Call chat_with_tools with exponential backoff on transient upstream
    errors (HTTP 502 / 502001). Non-transient errors (e.g. 400) are re-raised
    immediately so the caller can fall back (e.g. drop a forced tool_choice).
    """
    import httpx
    last_err = None
    for attempt in range(TRANSIENT_RETRY_MAX):
        try:
            return await llm_client.chat_with_tools(
                messages=messages, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
                tool_choice=tool_choice,
            )
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            last_err = e
            if code == 502 and attempt < TRANSIENT_RETRY_MAX - 1:
                delay = TRANSIENT_BACKOFF_BASE * (2 ** attempt)
                logger.warning("chat_with_tools 502 (attempt %d/%d), backing off %.1fs",
                               attempt + 1, TRANSIENT_RETRY_MAX, delay)
                await asyncio.sleep(delay)
                continue
            raise
        except Exception as e:
            last_err = e
            raise
    if last_err:
        raise last_err
    raise RuntimeError("chat_with_tools_resilient: exhausted retries")


# ── Router (Layer 1: name + description only) ──

async def entry_node(state: dict) -> dict:
    """Cache gate — entry point of the graph.

    Runs first (cheap: KB prompt fetch + cache lookup). On a cache hit it
    returns early so the graph can END without any LLM call or retrieval.
    On a miss it records kb_prompt and lets the graph fan out into the skill
    router (LLM) and retrieval (I/O) which now run in PARALLEL — retrieval no
    longer waits for the router's LLM call to finish.
    """
    query, kb_id = state["query"], state["kb_id"]
    skill_id = state.get("skill_id")
    kb_prompt = await get_kb_prompt(kb_id)
    if not state.get("skip_cache"):
        cached = answer_cache.get(query, kb_id, skill_id=skill_id or "", kb_prompt=kb_prompt)
        if cached:
            return {"cache_hit": True, "final_answer": cached.answer,
                    "citations": cached.citations or [], "tool_results": [],
                    "tool_messages": [], "download_entries": [], "kb_prompt": kb_prompt}
    return {"cache_hit": False, "kb_prompt": kb_prompt}


async def fanout_node(state: dict) -> dict:
    """No-op pass-through that splits the graph into parallel branches.

    A single conditional edge from `entry` (cache miss) lands here, then this
    node fans out to both `router` (LLM) and `retrieval` (I/O) at once. Exists
    only because a LangGraph conditional edge maps one key to one target.
    """
    return {}


async def join_node(state: dict) -> dict:
    """No-op pass-through that merges the parallel `router` + `retrieval`
    branches back into a single stream before skill_loader / tool_decision.
    """
    return {}


async def skill_router_node(state: dict) -> dict:
    """Layer 1 routing — only queries DB index (name + description).

    Does NOT load SKILL.md full text or tools. That happens in skill_loader_node.
    Runs in PARALLEL with parallel_retrieval_node (see _build_graph): the router
    makes its LLM call while retrieval runs concurrently.
    """
    if state.get("cache_hit"):
        return {}
    _emit(state, "routing", "Analyzing intent and selecting a skill…")
    query, kb_id = state["query"], state["kb_id"]
    skill_id, tenant_id, user_id = state.get("skill_id"), state.get("tenant_id"), state.get("user_id")
    active_skill = None
    if skill_id:
        # User explicitly selected a skill — just fetch the DB index
        active_skill = await _get_skill_index(skill_id)
        logger.info("Router: explicit skill_id=%s name=%s", skill_id, active_skill.get('name') if active_skill else 'NONE')
    if not active_skill and not skill_id:
        # Auto-route using name + description only
        async with async_session() as db:
            skills = (await db.execute(
                select(Skill).where((Skill.tenant_id == tenant_id) & (Skill.is_active == True))  # noqa: E712
            )).scalars().all()
        # Routing gate: drop candidates whose shared enable-symlink is missing
        # (disabled since the last DB sync) so the LLM never routes to them.
        skills = [s for s in skills if is_skill_effectively_enabled(s.folder_name)]
        active_skill = await _route_to_best_skill(query, tenant_id, user_id, skills=skills)
        logger.info("Router: auto-routed to skill=%s", active_skill.get('name') if active_skill else 'NONE')
        # Fallback: keyword-based routing for file/code generation intent. This
        # avoids depending on the LLM returning the exact skill name.
        if not active_skill:
            active_skill = _route_by_keywords(query, skills)
            logger.info("Router: keyword-routed to skill=%s", active_skill.get('name') if active_skill else 'NONE')

    # Routing gate (defense in depth): the shared enable-symlink is the source of
    # truth. Drop a skill the router selected if its symlink is gone (disabled
    # after the last DB sync), even when its is_active cache is still True.
    if active_skill and not is_skill_effectively_enabled(active_skill.get("folder_name", "")):
        logger.info("Router: dropping skill=%s — FS enable-symlink absent (disabled)",
                    active_skill.get("name"))
        active_skill = None

    # Layer 1 output: only id/name/description/folder_name — no system_prompt, no tools
    return {"active_skill": active_skill, "available_tools": [],
            "cache_hit": False, "tool_round": 0, "tool_results": [], "tool_messages": [],
            "download_entries": []}


async def _get_skill_index(skill_id: str) -> dict | None:
    """Fetch skill DB index row by ID. Returns {id, name, description, folder_name}."""
    async with async_session() as db:
        skill = await get_skill_by_id(db, skill_id)
        if not skill or not skill.is_active:
            return None
        return {"id": skill.id, "name": skill.name, "description": skill.description, "folder_name": skill.folder_name}


async def _route_to_best_skill(query, tenant_id, user_id, skills=None) -> dict | None:
    """Auto-route using LLM to match query against skill name + description (Layer 1).

    Returns {id, name, description, folder_name} or None.
    """
    if skills is None:
        async with async_session() as db:
            skills = (await db.execute(
                select(Skill).where((Skill.tenant_id == tenant_id) & (Skill.is_active == True))  # noqa: E712
            )).scalars().all()
    # Routing gate: never route to a skill whose enable-symlink is missing.
    skills = [s for s in skills if is_skill_effectively_enabled(s.folder_name)]
    if not skills:
        return None
    skill_list = "\n".join(f"{i+1}. {s.name}: {s.description or '(no description)'}" for i, s in enumerate(skills))
    prompt = build_intent_router_prompt(query, skill_list, lang=config_manager.prompt_language)
    try:
        raw = (await llm_client.chat(messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=50)).strip()
        # The router now returns a 1-based skill NUMBER (or 0 for none). Parse the
        # first integer so trailing text like "1." / "no. 1" still matches.
        m = _re.search(r"\d+", raw)
        if m:
            idx = int(m.group(0))
            if 1 <= idx <= len(skills):
                s = skills[idx - 1]
                return {"id": s.id, "name": s.name, "description": s.description, "folder_name": s.folder_name}
            # idx == 0 or out of range -> explicit "no skill" / invalid -> fall through
        # Safety net: if the model ignored the numbered format and echoed a name,
        # still try a case/space-insensitive exact-name match.
        chosen = _re.sub(r"\s+", "", raw.strip('"').strip("'").strip("。!").strip()).lower()
        for s in skills:
            if _re.sub(r"\s+", "", s.name.lower()) == chosen:
                return {"id": s.id, "name": s.name, "description": s.description, "folder_name": s.folder_name}
    except Exception as e:
        logger.warning("Skill routing failed: %s", e)
    return None


def _route_by_keywords(query: str, skills: list) -> dict | None:
    """Keyword fallback for routing file / code-generation intent to the skill
    that manages workspace files.

    Used when the LLM name-match router (``_route_to_best_skill``) returns
    nothing. Picks the candidate skill whose name/description best signals
    file/document handling, so "generate mydoc.txt" maps to e.g. Document
    Manager without relying on exact-name matching.
    """
    if not skills or not query:
        return None
    q = query.lower()
    if not any(kw.lower() in q for kw in _ROUTE_FILE_INTENT_KEYWORDS):
        return None
    doc_kw = ("文件", "文档", "file", "document", "doc", "写", "生成",
              "workspace", "工作区", "excel", "csv", "表格")
    best, best_score = None, 0
    for s in skills:
        blob = f"{s.name} {s.description or ''}".lower()
        score = sum(1 for k in doc_kw if k.lower() in blob)
        if score > best_score:
            best, best_score = s, score
    if best and best_score > 0:
        return {"id": best.id, "name": best.name,
                "description": best.description, "folder_name": best.folder_name}
    return None


# ── Skill Loader (Layer 2: SKILL.md full text + tools) ──

def _build_meta_skill_tools(include_kb: bool = False) -> list[dict]:
    """Always-available meta tools that let the LLM orchestrate skills (Route D).

    These are injected into available_tools whenever a skill is loaded, so the
    LLM can list skills and chain into another skill mid-conversation. When
    ``include_kb`` is True (i.e. the session has a selected knowledge base), the
    ``hybrid_search`` meta-tool is added so the LLM can retrieve on demand.
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "list_skills",
                "description": "List all currently available skills (name and description). Call this when you need to decide which skill to use, or to confirm whether a particular skill exists.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            "_source": "meta",
        },
        {
            "type": "function",
            "function": {
                "name": "use_skill",
                "description": (
                    "Load and activate another skill. Once loaded, that skill's rules and tools take effect immediately and can be called in the current conversation. "
                    "Use this for subtasks the current skill cannot handle directly. Note: creating, reading, updating, and deleting files and running code are your "
                    "NATIVE capabilities (just call run_python) — only use use_skill when you need a separate, specialized skill."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "The name of the skill to use. Call list_skills first if you need to see the available skills.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why you are invoking this skill (e.g. 'need to generate a PPT document first, then return to polish it'). Shown to the user as a progress step.",
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
                    "End the current temporary skill and return to the previous skill layer. Do not call this if no return is needed."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            "_source": "meta",
        },
        {
            "type": "function",
            "function": {
                "name": "create_cron",
                "description": (
                    "Create a scheduled cron job that runs a task on a recurring basis. "
                    "Use when the user wants to schedule a task to run at specific times (e.g. daily, weekly, hourly). "
                    "The job will be persisted in the database and executed by the scheduler at the specified times."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short name of the scheduled task (e.g. 'Daily report generation')",
                        },
                        "cron_expr": {
                            "type": "string",
                            "description": "Linux crontab 5-field expression (e.g. '0 9 * * *' for daily at 9:00 AM). "
                                           "The expression is interpreted in the user's local timezone.",
                        },
                        "task_content": {
                            "type": "string",
                            "description": "The exact task to execute when the cron job triggers. "
                                           "Describe what the agent should do in natural language.",
                        },
                        "max_runs": {
                            "type": "integer",
                            "description": "Optional: maximum number of executions. Omit for unlimited.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional longer description of what this job does.",
                        },
                    },
                    "required": ["name", "cron_expr", "task_content"],
                },
            },
            "_source": "meta",
        },
        {
            "type": "function",
            "function": {
                "name": "update_memory",
                "description": (
                    "Read or write the user's persistent profile memory (the 'Memory & Preferences' "
                    "field). Call this whenever the user explicitly asks to change what you remember "
                    "about them — to remember something, note a preference, or save a fact (e.g. "
                    "'remember that I prefer replies in Chinese'), OR to forget / edit a previously "
                    "saved fact or preference (e.g. 'forget my project codename'). The intended "
                    "workflow is: first call with action='read' to fetch the current memory text, edit "
                    "it in your reasoning (add, delete, or modify lines), then call again with "
                    "action='write' and the full, updated text to persist it. Do NOT call this for "
                    "trivial small talk — only when the user clearly wants the profile memory changed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["read", "write"],
                            "description": "Use 'read' to fetch the current memory text (no write), or 'write' to persist the memory text provided in 'content' (an empty string clears the memory).",
                        },
                        "content": {
                            "type": "string",
                            "description": "Used only for action='write' — the complete, updated memory text to save (replacing the previous content). An empty or whitespace-only value CLEARS the memory. Ignored for action='read'.",
                        },
                    },
                    "required": ["action"],
                },
            },
            "_source": "meta",
        },
    ]
    if include_kb:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "hybrid_search",
                    "description": (
                        "Search the user's currently selected knowledge base on demand. "
                        "Use this when the entry retrieval did not surface enough, OR when the "
                        "user's question contains references (e.g. 'these meetings', 'them', "
                        "'the ones above') that can only be resolved from conversation context. "
                        "BEFORE calling, resolve any reference against the conversation history "
                        "and REWRITE the query to be self-contained (e.g. turn 'who hosted "
                        "these meetings' into 'who hosted Meeting A, Meeting B, Meeting C'). "
                        "Do NOT call when you already have enough information to answer."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "A SELF-CONTAINED search query with all references resolved. Never pass the user's raw follow-up verbatim if it contains references.",
                            },
                            "top_k": {
                                "type": "integer",
                                "description": "Optional max chunks to return. Defaults to system retrieval_final_top_k.",
                            },
                            "doc_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional restrict to specific doc_ids (e.g. only search within Meeting A and Meeting B).",
                            },
                        },
                        "required": ["query"],
                    },
                },
                "_source": "meta",
            }
        )
    return tools


# Module-level cache of Python Executor meta tools (always-available native tools).
# Populated at startup (lifespan) via _refresh_meta_python_tools and used as a
# fallback here when startup missed it. These are the "meta tools" the Python
# Executor (e.g. run_python) exposes — native file/code execution for claw.
_META_PYTHON_TOOLS: list[dict] = []


async def _refresh_meta_python_tools() -> None:
    """Fetch tools from the Python Executor MCP server and cache them as meta tools.

    The cached tools keep their MCP source metadata (``_source`` / ``_mcp_server_id``)
    so they execute through the normal MCP tool path, not the meta control path —
    this is what makes run_python etc. available natively to every conversation.
    """
    global _META_PYTHON_TOOLS
    try:
        tools = await tool_registry.get_mcp_tools(["Python Executor"])
    except Exception as e:
        logger.warning("meta python tools: fetch failed: %s", e)
        return
    _META_PYTHON_TOOLS = [t for t in tools if t.get("function", {}).get("name")]
    logger.info("meta python tools: loaded %d tools from Python Executor",
                len(_META_PYTHON_TOOLS))


def _meta_control_names() -> set[str]:
    """Names of the meta *control* tools (orchestration only, never executed)."""
    return {"list_skills", "use_skill", "done_skill"}


async def _build_all_meta_tools(include_kb: bool = False) -> list[dict]:
    """Always-available meta tools: control tools + Python Executor native tools.

    The Python Executor tools (e.g. run_python) are fetched from the server at
    startup; _refresh_meta_python_tools populates the cache, with a lazy fetch
    here as a fallback if startup missed it (e.g. MCP not reachable yet). When
    ``include_kb`` is True the ``hybrid_search`` meta-tool is included too (the
    session has a selected knowledge base).
    """
    python_tools = _META_PYTHON_TOOLS
    if not python_tools:
        await _refresh_meta_python_tools()
        python_tools = _META_PYTHON_TOOLS
    return _build_meta_skill_tools(include_kb=include_kb) + python_tools


async def _load_skill_body_and_tools(folder_name: str, user_id: str | None = None) -> tuple[str, list[dict]]:
    """Load a skill's SKILL.md body + tools.

    Shared by the initial skill_loader_node and Route D chaining (skill_switcher_node).
    Returns (system_prompt, tools) where tools already include the
    read_skill_resource tool when the skill has reference/data files.

    Skills are exposed to the sandbox exclusively through the ``REPL_SKILLS_DIR``
    container env var (set in docker-compose for mcp-repl, default
    ``/ragclaw_skills/enable``), which points at the shared, backend-managed
    ``enable/`` set. No per-user symlink is materialised anymore; the skill body
    is always loaded from the shared store regardless.
    """

    skill_md_content = read_skill_md(folder_name)
    if not skill_md_content:
        logger.warning("_load_skill_body_and_tools: SKILL.md not found for folder=%s", folder_name)
        return config_manager.system_prompt_capabilities, []

    parsed = parse_skill_md(skill_md_content)
    # No source-side path-template expansion here: skill authors use arbitrary names for the
    # skill root (e.g. {baseDir}, <skill_dir>, plain scripts/...). The LLM maps any of them to
    # $REPL_SKILLS_DIR/<folder_name> via the principle stated in skill_sandbox_note.
    system_prompt = parsed["body"] or config_manager.system_prompt_capabilities
    # Teach the LLM where this skill's folder lives inside the sandbox (REPL_SKILLS_DIR)
    # and that it is read-only. Appended to every active skill's system prompt so the
    # model can open skill files via run_python and knows writes are not persisted.
    system_prompt = system_prompt + "\n\n" + _t("skill_sandbox_note", config_manager.prompt_language)
    # Append the ragclaw-owned adapter doc (Resolved command + output rules) WITHOUT
    # mutating the third-party SKILL.md. This file is generated by the skill's init.sh
    # and lives at <skill>/.ragclaw/SKILL.ragclaw.md.
    ragclaw_doc = read_ragclaw_skill_doc(folder_name)
    if ragclaw_doc:
        system_prompt = system_prompt + "\n\n---\n\n" + ragclaw_doc.strip()
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
                "description": f"Read a resource file from the skill folder. Available files:\n{path_list}{more}\n\n"
                              + _t("skill_resource_tool_note", config_manager.prompt_language),
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
    "run_python": "Run Python script",
    "read_skill_resource": "Read skill resource",
}


def _emit(state: dict, stage: str, message: str, **extra) -> None:
    """Push an agent_step progress event to the SSE stream (if a callback is
    wired) and accumulate it into ``state["agent_steps"]`` for durable persistence.

    Must never raise — a broken emit must not interrupt the agent graph. The
    accumulated steps live in a SEPARATE channel: they are never injected into
    the LLM message list and never fed to MEM0 memory extraction.
    """
    # Accumulate for persistence (guarded; must never break the graph).
    try:
        steps = state.setdefault("agent_steps", [])
        steps.append({
            "stage": stage,
            "message": message,
            "extra": extra or None,
            "ts": datetime.utcnow().isoformat() + "Z",
        })
    except Exception:
        pass
    fn = state.get("emit")
    if not fn:
        return
    try:
        fn(stage, message, **extra)
    except Exception:
        pass


def _emit_context_usage(state: dict, summary_text, history, messages: list[dict]) -> None:
    """Report the token footprint of the payload about to be sent to the LLM.

    Fires on EVERY submission (each tool round, then the final generation), so
    the frontend meter always reflects the most recent one -- later reports
    simply overwrite earlier ones.

    Pure transient telemetry: it goes straight to the SSE stream and is NOT
    accumulated into ``state["agent_steps"]`` (it would be meaningless on
    replay and would bloat the persisted trace).
    """
    fn = state.get("emit_usage")
    if not fn:
        return
    try:
        fn(context_breakdown(summary_text, history, count_messages_tokens(messages)))
    except Exception:
        pass


async def skill_loader_node(state: dict) -> dict:
    """Layer 2 — load SKILL.md full text, discover script tools, load MCP tools.

    Always injects the always-available meta tools (control tools + Python
    Executor native tools such as run_python) so claw's file/code capabilities
    are present even when no skill is selected. When a skill IS selected, its
    SKILL.md body and tools are also loaded and the Route D skill stack is
    initialised.
    """
    if state.get("cache_hit"):
        return {}

    # Build the always-available meta tools (control + Python Executor native).
    # This runs before any skill check so run_python etc. are natively available
    # to every conversation, matching the ragclaw "native file/code execution" role.
    meta_tools = await _build_all_meta_tools(include_kb=bool(state.get("kb_id")))

    active_skill = state.get("active_skill")
    if not active_skill:
        # No skill selected — still expose the native meta tools so the agent can
        # operate on files / run code without routing through a skill.
        return {"available_tools": meta_tools}

    # Routing gate (defense in depth): never inject a disabled skill's body/tools
    # even if it slipped past the router. is_skill_effectively_enabled falls back
    # to is_active when the shared volume is unmounted, so this only drops skills
    # that are genuinely disabled in the mounted state.
    if not is_skill_effectively_enabled(active_skill.get("folder_name", "")):
        logger.warning("skill_loader_node: active skill '%s' is FS-disabled; skipping skill load",
                       active_skill.get("name"))
        return {"available_tools": meta_tools}

    folder_name = active_skill.get("folder_name")
    if not folder_name:
        return {"available_tools": meta_tools}

    system_prompt, all_tools = await _load_skill_body_and_tools(folder_name, state.get("user_id"))
    updated_skill = {**active_skill, "system_prompt": system_prompt, "source": "primary"}

    # Always-available meta tools for orchestration + native execution (Route D)
    all_tools = all_tools + meta_tools

    _emit(state, "skill_load", f"Loaded skill: {active_skill.get('name', '?')}", skill=active_skill.get("name"))

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


def _fuzzy_match_skill(name: str, skills: list) -> "Skill | None":
    """Pick the single best-matching active skill for a (possibly misspelled) name.

    Purely registry-driven — never special-cases any specific skill, so the
    matching stays correct as skills are added/removed/renamed. Tiers (first hit
    wins): 1) case-insensitive exact on name/folder_name; 2) unique substring
    containment; 3) difflib edit-distance ratio >= 0.6, rejecting near-ties.
    Returns None when no confident match exists.
    """
    if not skills:
        return None
    lowered = name.lower()
    for s in skills:
        if s.name.lower() == lowered or s.folder_name.lower() == lowered:
            return s
    contained = [s for s in skills if lowered in s.name.lower() or lowered in s.folder_name.lower()]
    if len(contained) == 1:
        return contained[0]
    from difflib import SequenceMatcher

    def _ratio(s: "Skill") -> float:
        best = 0.0
        for cand in (s.name, s.folder_name):
            best = max(best, SequenceMatcher(None, lowered, cand.lower()).ratio())
        return best
    scored = sorted(((_ratio(s), s) for s in skills), key=lambda x: x[0], reverse=True)
    best_ratio, best = scored[0]
    if best_ratio < 0.6:
        return None
    if len(scored) > 1 and scored[1][0] >= best_ratio - 0.05:
        return None  # ambiguous — refuse to guess
    return best


async def _build_skill_catalogue_prompt(tenant_id: str | None) -> str:
    """Build a generic 'Available Skills' block from the live registry.

    Surfaces each active skill's name + description (authored in its SKILL.md
    frontmatter) so the decision LLM can route to a skill without first calling
    list_skills. Capability-driven, NOT a per-skill keyword enumeration.
    """
    async with async_session() as db:
        if tenant_id:
            rows = (await db.execute(
                select(Skill).where((Skill.tenant_id == tenant_id) & (Skill.is_active == True))  # noqa: E712
            )).scalars().all()
        else:
            rows = (await db.execute(
                select(Skill).where(Skill.is_active == True)  # noqa: E712
            )).scalars().all()
    rows = [s for s in rows if is_skill_effectively_enabled(s.folder_name)]
    if not rows:
        return ""
    lines = "\n".join(f"- {s.name}: {s.description or '(no description)'}" for s in rows)
    return (
        "## Available Skills\n"
        "Load a skill with use_skill(\"<name>\") when the user's request matches its "
        "capability. Available now:\n" + lines
    )


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
            # Routing gate: only surface skills whose shared enable-symlink is
            # present, so a freshly-disabled skill leaves the catalogue immediately.
            skills = [s for s in skills if is_skill_effectively_enabled(s.folder_name)]
            skill_list = "\n".join(f"- {s.name}: {s.description or '(no description)'}" for s in skills) or "(no skills available)"
            result = f"Available skills:\n{skill_list}"
            return _skill_control_return(tc, result, stack, state)

        # ── done_skill ──
        if fname == "done_skill":
            if len(stack) <= 1:
                result = "done_skill: already at the top-level skill; there is no previous layer to return to."
                # C-consistent: a control no-op (already at top level) must not consume
                # a tool-round quota — only real work should. Keep the inline return so we
                # don't inherit _skill_control_return's +1 (shared by list_skills / failed switches).
                return {
                    "active_skill": stack[-1] if stack else state.get("active_skill"),
                    "skill_stack": stack,
                    "tool_results": [result],
                    "tool_messages": [{
                        "role": "tool",
                        "tool_call_id": tc.get("id", "call_meta"),
                        "name": fname,
                        "content": result,
                    }],
                    "tool_round": state.get("tool_round", 0),
                }
            stack = stack[:-1]
            prev = stack[-1]
            result = f"Returned to the previous skill layer: '{prev.get('name')}'. Its rules and tools are now active."
            _emit(state, "skill_return", f"Returned to previous skill layer: '{prev.get('name')}'", skill=prev.get("name"))
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
                "tool_round": state.get("tool_round", 0),
            }

        # ── use_skill ──
        if fname == "use_skill":
            name = (args.get("skill_name") or "").strip()
            if not name:
                _emit(state, "skill_switch_fail", "use_skill: no skill_name was provided.")
                return _skill_control_return(tc, "use_skill: no skill_name was provided.", stack, state)
            quota = state.get("skill_switch_quota", config_manager.skill_switch_quota)
            # quota == 0 means unlimited switches
            if quota != 0 and switch_count >= quota:
               # Suspend: wait for user confirmation ("continue" = replay after adding quota) instead of silently rejecting
                # Emit a stable, language-neutral code; the localized reminder text lives in the
                # frontend chat namespace (chat.skillSwitchLimitHint), mirroring tool_round_limit.
                msg = "skill_switch_limit"
                _emit(state, "skill_switch_fail", msg, skill=name)
                return {
                    "pending_limit": {
                        "kind": "skill_switch",
                        "message": msg,
                        "deferred_tool_call": tc,
                    },
                }
            skill = await get_skill_by_name(db, name, tenant_id)
            corrected_from = None
            if not skill or not skill.is_active or not is_skill_effectively_enabled(skill.folder_name):
                # B: generic fuzzy fallback over the live registry (no per-skill logic).
                # Lets "search" -> "anysearch" etc. without burning a list_skills round.
                stmt = (
                    select(Skill).where((Skill.tenant_id == tenant_id) & (Skill.is_active == True))  # noqa: E712
                    if tenant_id else
                    select(Skill).where(Skill.is_active == True)  # noqa: E712
                )
                all_skills = (await db.execute(stmt)).scalars().all()
                all_skills = [s for s in all_skills if is_skill_effectively_enabled(s.folder_name)]
                fuzzy = _fuzzy_match_skill(name, all_skills)
                if fuzzy is not None:
                    corrected_from = name
                    skill = fuzzy
                else:
                    result = f"use_skill: no available skill named '{name}' (call list_skills to see what's available)."
                    _emit(state, "skill_switch_fail", result, skill=name)
                    return _skill_control_return(tc, result, stack, state)
            if skill.id in loaded:
                result = f"use_skill: skill '{skill.name}' is already active in the stack; no need to load it again."
                _emit(state, "skill_switch_fail", result, skill=skill.name)
                return _skill_control_return(tc, result, stack, state)

            system_prompt, new_tools = await _load_skill_body_and_tools(skill.folder_name, state.get("user_id"))
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
            correction_note = (
                f" (note: '{corrected_from}' not found — loaded closest match '{skill.name}')"
                if corrected_from else ""
            )
            result = (
                f"Loaded skill '{skill.name}'{correction_note}. New tools added: {added_names or '(none)'}."
                "Its rules are now active — you can call its tools directly. When finished with this skill, call done_skill to return to the previous layer."
            )
            logger.info("Skill switcher: use_skill '%s' → stack depth=%d, added_tools=%d",
                        skill.name, len(stack), len(added))
            reason = (args.get("reason") or "").strip()
            switch_msg = f"Switched to and loaded '{skill.name}'"
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
                # C: a successful skill load must NOT consume a tool-round quota,
                # otherwise loading alone can trip 'max rounds reached' and force a
                # user 'continue' re-entry. Real tools still increment the counter.
                "tool_round": state.get("tool_round", 0),
            }

    # Unknown control tool — should not happen, but fail safe.
    result = f"Unknown meta-tool: {fname}"
    _emit(state, "skill_switch_fail", result)
    return _skill_control_return(tc, result, stack, state)


async def limit_suspend_node(state: dict) -> dict:
    """Suspension exit: pending_limit is set; chat.py catches it, stores the snapshot, pushes need_user_input, and the graph ends normally here."""
    return {}


async def resume_replay_node(state: dict) -> dict:
    """Resume entry: no LLM decision involved. tool_calls has already been reset by chat.py to the rejected call (cause A),
    or left empty (cause B, handed to tool_decision for re-decision).

    Note: the assistant tool_call message corresponding to the rejected use_skill was already written to
    tool_messages by tool_decision_node in the original round (and stored in the snapshot), so no need to add it again here;
    after skill_switcher succeeds it appends the corresponding tool result, keeping the tool pair naturally complete.
    """
    return {}


# ── Retrieval ──

async def parallel_retrieval_node(state: dict) -> dict:
    if state.get("cache_hit"):
        return {}
    query = state["query"]
    kb_id = state.get("kb_id")
    user_id = state.get("user_id", "")
    # When the user has not selected any knowledge base there is nothing to
    # retrieve over, so skip the (vector + BM25) hybrid search entirely.
    if not kb_id:
        _emit(state, "retrieval_done", "No knowledge base selected - skipping retrieval", detail="skip")
        return {"rag_context": "", "citations": [], "retrieval_ms": 0}
    _emit(state, "retrieval", "Retrieving from knowledge base…")
    t_start = time.time()
    loop = asyncio.get_running_loop()

    # Document hybrid retrieval: concurrent vector + BM25, RRF fuse, render.
    # Delegates to the shared HybridSearchService._run_hybrid_retrieval — the
    # single source for the doc-path logic, also used by the hybrid_search meta
    # tool (Step 3). Replaces the inline copy that previously lived here.
    rag_context, citations = await hybrid_search._run_hybrid_retrieval(kb_id, query)
    chunk_count = len(citations)
    _emit(state, "retrieval_done", f"Retrieved {chunk_count} chunk(s)", detail=f"{chunk_count} chunk(s)")

    # ── Conversation memory recall (independent B namespace: mem_{conv_id}) ──
    # Mirrors the document hybrid path: run vector + BM25 concurrently, fuse, and
    # degrade to BM25-only if the vector path errors (e.g. no embedding model).
    # Kept separate from rag_context so archived memory is never surfaced as a
    # user-document citation.
    memory_context = ""
    conv_id = state.get("conversation_id")
    mem_kb = f"mem_{conv_id}" if conv_id else ""
    if mem_kb and memory_archive.has_memory(conv_id):
        mv_task = loop.run_in_executor(None, vector_store.search, mem_kb, query, settings.retrieval_vector_top_k)
        mb_task = loop.run_in_executor(None, bm25_index.search, mem_kb, query, settings.retrieval_bm25_top_k)
        mv, mb = await asyncio.gather(mv_task, mb_task, return_exceptions=True)
        if isinstance(mv, Exception):
            logger.warning("Memory vector search error: %s", mv)
            mv = []
        if isinstance(mb, Exception):
            logger.warning("Memory BM25 search error: %s", mb)
            mb = []
        mem_retrieved = hybrid_search.fuse(mv, mb)
        if mem_retrieved:
            memory_context = _format_memory(mem_retrieved)

    return {"rag_context": rag_context, "citations": citations, "memory_context": memory_context,
            "retrieval_ms": round((time.time() - t_start) * 1000)}


def _format_memory(retrieved: list[dict]) -> str:
    """Render recalled memory chunks as a plain text block (no citations).

    Joined with MEM_CHUNK_DELIM so the assembly-point fit guard can drop the
    least-relevant (tail) chunk without mis-splitting a recalled passage that
    itself contains blank lines (a bare "\\n\\n" join would have cut inside a chunk).
    """
    if not retrieved:
        return ""
    return MEM_CHUNK_DELIM.join(f"[Conversation Memory] {r['content']}" for r in retrieved)


# ── Tool Decision ──

def _tool_call_signature(tc: dict) -> tuple:
    """Normalized (name, args_json) signature for repeat detection across rounds."""
    f = (tc or {}).get("function", {}) or {}
    name = f.get("name")
    try:
        args = json.loads(f.get("arguments", "{}"))
    except (json.JSONDecodeError, TypeError):
        args = {}
    return (name, json.dumps(args, sort_keys=True, ensure_ascii=False))


def _same_tool_calls(a: list, b: list) -> bool:
    """True if two tool-call lists are identical by normalized signature."""
    if not a or not b or len(a) != len(b):
        return False
    return all(_tool_call_signature(x) == _tool_call_signature(y) for x, y in zip(a, b))


def _looks_like_error(result: str) -> bool:
    if not result:
        return False
    r = result.lower()
    return "错误" in result or "异常" in result or "error" in r or "traceback" in r


def _recent_assistant_tool_calls(tool_messages: list, window: int = 3) -> list:
    """Most-recent-first list of assistant tool_calls (up to `window` rounds)."""
    out = []
    for m in reversed(tool_messages or []):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            out.append(m["tool_calls"])
            if len(out) >= window:
                break
    return out


def _tool_result_signature(text: str) -> str:
    """Normalized signature of a tool result for repeat detection.

    Strips the leading ``[tool_name] `` prefix (which differs per tool) and
    surrounding whitespace so that two runs of e.g. ``run_python`` that return
    the same payload but were invoked with slightly different code still count
    as a repeat. This catches the degenerate "run → same answer → run again"
    loop that the argument-based guard misses.
    """
    if not text:
        return ""
    s = text.strip()
    # Drop a leading "[name] " (or "[name]" with no trailing space) produced by
    # the executor's result wrappers, so two runs that return the same payload
    # but were invoked with slightly different code still count as a repeat.
    s = re.sub(r"^\[[^\]]*\]\s*", "", s)
    return s.strip()


def _recent_tool_result_signatures(tool_messages: list, window: int = 3) -> list:
    """Most-recent-first normalized signatures of tool results (up to `window` rounds).

    Each "round" normally contributes exactly one tool result message; we take
    the last `window` such messages regardless of how many tools ran per round.
    """
    out = []
    for m in reversed(tool_messages or []):
        if m.get("role") == "tool":
            out.append(_tool_result_signature(m.get("content", "")))
            if len(out) >= window:
                break
    return out


async def tool_decision_node(state: dict) -> dict:
    if state.get("cache_hit"):
        return {}
    available_tools = state.get("available_tools", [])
    tool_round = state.get("tool_round", 0)
    prev_results = state.get("tool_results", [])
    # Logs the decision trace at INFO (dev) so tool-call loops are debuggable
    # via `docker logs`. Production suppresses INFO (see logging_config).
    logger.info("🔍 tool_decision ENTER: round=%d tools=%d prev_results=%d cache=%s",
                tool_round, len(available_tools), len(prev_results),
                state.get("cache_hit"))
    if not available_tools:
        logger.info("Tool decision: no available tools — skipping tool phase")
        return {"tool_calls": None}
    quota = state.get("tool_round_quota", config_manager.agent_round_quota)
    # quota == 0 means unlimited rounds
    if quota != 0 and tool_round >= quota:
        logger.info("Tool decision: max rounds reached (round=%d, quota=%d)", tool_round, quota)
        # Suspend: rounds exhausted, wait for user confirmation (after resume the LLM re-decides, because the LLM was not called yet when the limit was hit).
        # Emit a stable, language-neutral code as the hint; the actual localized reminder
        # text is owned by the frontend i18n (chat.toolRoundLimitHint) keyed on kind="tool_round".
        msg = "tool_round_limit"
        _emit(state, "tool_round_limit", msg)
        return {
            "tool_calls": None,
            "pending_limit": {"kind": "tool_round", "message": msg, "deferred_tool_call": None},
        }

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
    # Part 1 (identity/security) is the ALWAYS-ON base; Part 2 (native capabilities /
    # skill body) is the active skill's system_prompt when present, else the constant
    # base. They are kept as SEPARATE components: identity stays in the stable
    # task-background block while the variable skill_body is emitted as its OWN
    # trailing system message, so a skill switch only busts that final cache unit.
    identity_prompt = config_manager.system_prompt_identity
    skill_body = active.get("system_prompt", config_manager.system_prompt_capabilities)
    kb_prompt = state.get("kb_prompt") or ""
    if not kb_prompt:
        kb_prompt = await get_kb_prompt(state["kb_id"])
    kb_context = f"\n\n## Knowledge Base Background & Preferences\n{kb_prompt}" if kb_prompt else ""
    # Tell the LLM the user's selected working directory (English) so file/code
    # operations target the right place. Appended to the task-background context.
    # NOTE: available_tools is guaranteed non-empty here — tool_decision_node
    # returns early at the top when it is empty (so the LLM is never called in the
    # no-tools case, and cwd is irrelevant there anyway).
    ws_context = _build_working_dir_prompt(state)
    # A: always surface the live skill catalogue in the decision context so the
    # LLM can route via use_skill without first spending a list_skills round.
    # Capability-driven (name+description from each SKILL.md), not keyword enumeration.
    skill_catalogue = await _build_skill_catalogue_prompt(state.get("tenant_id"))
    tool_desc = "\n".join(
        f"- {t['function']['name']}: {t['function']['description']}"
        for t in available_tools
    )
    # Tencent tokenhub does not support tool_choice="required" (400/502).
    # Use tool_choice="auto" and steer via prompt. Even when the LLM ignores
    # the JSON instruction, its alternate output (Python code blocks) is caught
    # by _try_extract_code_as_tool — plain text hallucination would be the worst case.
    # D4 fix: native function calling passes the tools as function schemas to
    # chat_with_tools, so we do NOT re-list them in the system prompt here — the
    # double description nudges the model toward emitting JSON-in-text instead of a
    # native tool call. The tool list is only injected when we fall back to plain
    # text mode (see _with_tool_desc below), where there is no function schema.
    tool_system = build_tool_system_prompt("", lang=config_manager.prompt_language)
    tool_heading = _t("tool_desc_heading", config_manager.prompt_language)
    tool_desc_block = f"{tool_heading}\n{tool_desc}" if tool_desc else ""

    def _with_tool_desc(msgs: list) -> list:
        if not tool_desc_block:
            return msgs
        out = []
        injected = False
        for m in msgs:
            if m.get("role") == "system" and not injected:
                out.append({**m, "content": m["content"] + "\n\n" + tool_desc_block})
                injected = True
            else:
                out.append(m)
        return out
    # ── Assembly-point budget guard (the only hard ceiling) ──
    # build_context_with_summary applied SEMANTIC compression at turn start but
    # deliberately performs no mechanical trimming -- it cannot see the RAG /
    # memory / tool payload. This guard runs with the complete payload and trims
    # (rag -> memory -> summary -> history -> tool_messages -> query) on TRANSIENT
    # copies, so the request always fits without writing anything back. `q` is
    # threaded in as a parameter (never read from `state`) so phase 3 can take
    # effect. Note: the tool-decision prompt does NOT render memory recall
    # (memory_context is passed as None), so only the final-generation path trims it.
    def _assemble(s, h, rag, payload, q, mem):
        # User-authored memory & preferences (from the profile page), appended to
        # the task background so the LLM can personalize. Kept separate from the
        # auto-extracted MEM0 memory graph. Empty string when nothing is set.
        user_memory = (state.get("user_memory") or "").strip()
        pin = state.get("pinned_instruction") or ""
        # Stable base (cache-friendly order): identity + KB + user memory + pinned
        # instructions + working dir. The variable skill_body is NOT merged here — it
        # becomes its own trailing system message below so skill switches invalidate
        # only that final unit, not this stable prefix.
        task_background = identity_prompt + kb_context
        if user_memory:
            task_background += f"\n\n## User Memory & Preferences\n{user_memory}"
        if pin:
            task_background += f"\n\n## Pinned Instructions\n{pin}"
        if ws_context:
            task_background += "\n\n" + ws_context
        # Anchor relative-time requests ('today'/'this year') to the real current
        # date in the user's timezone, so the LLM doesn't hardcode a training-year.
        task_background += "\n\n" + _current_date_note(state.get("timezone"))
        msgs = [
            {"role": "system", "content": tool_system},
            {"role": "system", "content": "## Task Background (reference only)\n" + task_background},
        ]
        if skill_body.strip():
            msgs.append({"role": "system", "content": skill_body})
        if skill_catalogue:
            msgs.append({"role": "system", "content": skill_catalogue})
        if s:
            msgs.append({"role": "system", "content": "## Earlier conversation summary (compressed)\n" + s})
        if h:
            msgs.extend(h)
        user_parts = []
        if rag:
            user_parts.append(f"## Reference Documents\n{rag}")
        user_parts.append(f"## Question\n{q}")
        msgs.append({"role": "user", "content": "\n\n".join(user_parts)})
        if payload:
            # ── Sanitize tool_messages before sending to LLM ──
            # TokenHub validates that assistant.tool_calls[].function.arguments is valid JSON.
            # If the LLM's output was truncated by max_tokens, the arguments string may be
            # incomplete JSON → TokenHub returns 400 on the next round.
            # Fix: validate each tool_call's arguments; if invalid, replace with error stub.
            sanitized_msgs = []
            for msg in payload:
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
            msgs.extend(sanitized_msgs)
        return msgs

    trimmed_s, trimmed_h, trimmed_rag, trimmed_mem, trimmed_p, trimmed_q, dropped = fit_assembly_context(
        summary_text=state.get("conversation_summary"),
        history=state.get("conversation_history", []),
        rag_context=state.get("rag_context"),
        memory_context=None,  # tool decision does not render memory recall
        tool_payload=state.get("tool_messages", []),
        query=state.get("query") or "",
        payload_kind="messages",
        build_messages=_assemble,
        tools=state.get("available_tools"),
    )
    messages = _assemble(trimmed_s, trimmed_h, trimmed_rag, trimmed_p, trimmed_q, trimmed_mem)
    _emit_context_usage(state, trimmed_s, trimmed_h, messages)
    if dropped:
        _emit(
            state,
            "context_compress",
            _t("assembly_trim_warning", config_manager.prompt_language),
        )
    try:
        # ── Dual-mode strategy: try native function calling first, fall back to text mode ──
        # TokenHub officially supports tools + tool_choice="auto", but some models may
        # have compatibility issues. We try chat_with_tools first; on 400/error,
        # we fall back to chat() + prompt-based JSON parsing.
        tool_calls = None
        content = ""

        # ── Layer 1: force a named tool when intent is unambiguous ──
        # Only on the first round (no prior results) so we never lock the LLM
        # into a tool during multi-round reasoning. TokenHub does not support
        # tool_choice="required", but the named-tool dict form IS supported.
        forced_tool = None
        if tool_round == 0 and not prev_results:
            forced_tool = _infer_forced_tool(state.get("query", ""), available_tools, active)
        tool_choice = (
            {"type": "function", "function": {"name": forced_tool}}
            if forced_tool else "auto"
        )

        try:
            logger.info("Tool decision: trying chat_with_tools (native), %d tools, round=%d, forced=%s",
                       len(available_tools), tool_round, forced_tool)
            response = await _chat_with_tools_resilient(
                messages, available_tools, tool_choice,
                temperature=0.0, max_tokens=_compute_agent_max_tokens(messages),
            )
            tool_calls = response.get("tool_calls")
            content = response.get("content") or ""
            logger.info("Tool decision: native_tool_calls=%s content_preview=%.200s",
                       bool(tool_calls), content[:200])
        except Exception as native_err:
            # A forced tool_choice dict may 400 on an incompatible model — retry
            # once with "auto" before falling all the way back to plain text mode.
            logger.warning("Tool decision: chat_with_tools failed (%s)", str(native_err)[:200])
            if forced_tool:
                try:
                    logger.info("Tool decision: retrying with tool_choice=auto (forced tool rejected)")
                    response = await _chat_with_tools_resilient(
                        messages, available_tools, "auto",
                        temperature=0.0, max_tokens=_compute_agent_max_tokens(messages),
                    )
                    tool_calls = response.get("tool_calls")
                    content = response.get("content") or ""
                except Exception as retry_err:
                    logger.warning("Tool decision: auto retry failed (%s), text mode", str(retry_err)[:200])
                    content = await llm_client.chat(messages=_with_tool_desc(messages), max_tokens=_compute_agent_max_tokens(messages))
            else:
                logger.info("Tool decision: falling back to text mode")
                content = await llm_client.chat(messages=_with_tool_desc(messages), temperature=0.1, max_tokens=_compute_agent_max_tokens(messages))
            logger.info("Tool decision: fallback content_preview=%.200s", content[:200])

        # Surface the model's raw reasoning ("thinking"/planning, e.g. "I will…")
        # as a separate agent_step so it appears in the processing timeline and is
        # persisted to the agent_steps table — never mixed into the final answer.
        # Emitted verbatim (NOT stripped) per requirement.
        if content and content.strip():
            _emit(state, "thinking", content.strip())

        if not content and not tool_calls:
            logger.warning("Tool decision: LLM returned empty content and no tool_calls")
            return {"tool_calls": None}

        # Try structured JSON parsing from content (handles [TOOL_CALL], =>, etc.)
        if not tool_calls and content:
            parsed = _try_parse_tool_call(content, available_tools)
            if parsed:
                logger.info("Tool decision: parsed tool_calls from JSON in content (round %d)", tool_round)
                tool_calls = parsed
                content = ""

        # Fallback: try extracting Python code blocks from LLM response
        if not tool_calls and content:
            logger.info("Tool decision: no JSON tool call (round %d), trying code extraction", tool_round)
            code_tool = _try_extract_code_as_tool(content, available_tools)
            if code_tool:
                logger.info("Tool decision: extracted code from LLM response, built run_python call")
                tool_calls = code_tool
                content = ""
            else:
                logger.info("Tool decision: code extraction yielded nothing (round %d)", tool_round)

        # ── Layer 2: self-heal retry ──
        # Parsing failed but the LLM emitted text that names an available tool —
        # it clearly INTENDED to call a tool, just botched the format. Ask it to
        # rewrite as valid JSON (forcing that tool), feeding back the bad output.
        if not tool_calls and content:
            intended = _extract_intended_tool_name(content, available_tools)
            if intended:
                logger.info("Tool decision: entering self-heal for tool '%s' (round %d)", intended, tool_round)
                heal_choice = {"type": "function", "function": {"name": intended}}
                bad_output = content
                for attempt in range(SELF_HEAL_MAX_RETRIES):
                    heal_messages = messages + [
                        {"role": "assistant", "content": bad_output},
                        {"role": "user", "content": build_selfheal_prompt(
                            intended, bad_output, lang=config_manager.prompt_language)},
                    ]
                    try:
                        heal_resp = await _chat_with_tools_resilient(
                            heal_messages, available_tools, heal_choice,
                            temperature=0.0, max_tokens=_compute_agent_max_tokens(heal_messages),
                        )
                    except Exception as heal_err:
                        # Forced dict may be rejected on retry — try once with auto.
                        logger.warning("Tool decision: self-heal attempt %d failed (%s), trying auto",
                                       attempt + 1, str(heal_err)[:150])
                        try:
                            heal_resp = await _chat_with_tools_resilient(
                                heal_messages, available_tools, "auto",
                                temperature=0.0, max_tokens=_compute_agent_max_tokens(heal_messages),
                            )
                        except Exception as heal_err2:
                            logger.warning("Tool decision: self-heal auto also failed (%s)", str(heal_err2)[:150])
                            break
                    heal_tc = heal_resp.get("tool_calls")
                    heal_content = heal_resp.get("content") or ""
                    if not heal_tc and heal_content:
                        heal_tc = (_try_parse_tool_call(heal_content, available_tools)
                                   or _try_extract_code_as_tool(heal_content, available_tools))
                    if heal_tc:
                        logger.info("Tool decision: self-heal SUCCESS on attempt %d (tool '%s')",
                                       attempt + 1, intended)
                        tool_calls = heal_tc
                        content = ""
                        break
                    # Feed forward the latest (still-bad) output for the next attempt.
                    bad_output = heal_content or bad_output
                    if not tool_calls:
                        logger.warning("Tool decision: self-heal exhausted %d attempts for '%s'",
                                       SELF_HEAL_MAX_RETRIES, intended)

        # ── Round-0 no-tool nudge (Plan A, generic / intent-agnostic) ──
        # The LLM emitted non-empty text but NO tool call on the very first tool round
        # (round 0, no prior results) AND a skill is active. This is the classic "it
        # planned but never invoked a tool" failure (e.g. "I will query the sub-domains
        # first" with no get_sub_domains call — incident 2026-08-16). Give it ONE
        # corrective chance with tool_choice="auto" so it can re-read the function
        # schemas and emit a real call. We do NOT guess the user's intent via keywords
        # (that would violate the capability-driven routing contract); we only re-prompt.
        # Gated on an active skill so pure chit-chat pays no extra LLM call.
        if not tool_calls and content and tool_round == 0 and not prev_results and active:
            logger.info("Tool decision: round-0 no-tool nudge (skill active) — retrying with auto")
            nudge_messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": build_no_tool_nudge_prompt(config_manager.prompt_language)},
            ]
            try:
                nudge_resp = await _chat_with_tools_resilient(
                    nudge_messages, available_tools, "auto",
                    temperature=0.0, max_tokens=_compute_agent_max_tokens(nudge_messages),
                )
            except Exception as nudge_err:
                logger.warning("Tool decision: round-0 nudge failed (%s)", str(nudge_err)[:150])
                nudge_resp = {}
            nudge_tc = nudge_resp.get("tool_calls")
            nudge_content = nudge_resp.get("content") or ""
            if not nudge_tc and nudge_content:
                nudge_tc = (_try_parse_tool_call(nudge_content, available_tools)
                            or _try_extract_code_as_tool(nudge_content, available_tools))
            if nudge_tc:
                logger.info("Tool decision: round-0 nudge SUCCESS — recovered a tool call")
                tool_calls = nudge_tc
                content = ""
            else:
                # No tool call even after the nudge. Treat the new content (if any) as
                # the real answer; Plan B's final-generation notice will keep the model
                # from leaking tool-call code if a skill was active but no tool ran.
                if nudge_content:
                    content = nudge_content
                logger.info("Tool decision: round-0 nudge produced no tool call; proceeding without tools")

        if tool_calls:
            # ── Deterministic loop guard ──
            # The model is unreliable at self-terminating: after a successful
            # append/write it often re-issues the SAME tool call, producing an
            # infinite loop that only pauses at the round quota (the "continue"
            # button then recharges it). We enforce termination deterministically:
            # if the chosen call is identical to a recent round AND that round
            # succeeded, stop and let the graph emit the final answer.
            prev_calls = _recent_assistant_tool_calls(state.get("tool_messages", []), window=3)
            if prev_calls:
                last_call = prev_calls[0]
                exact_repeat = _same_tool_calls(tool_calls, last_call)
                # Degenerate-loop case: the last up-to-3 rounds collapsed into a
                # single repeated signature (a pure repeat loop, NOT a legit
                # A/B/C cycle which would show >=2 distinct signatures).
                recent_sigs = []
                for calls in prev_calls:
                    recent_sigs.extend(_tool_call_signature(tc) for tc in calls)
                new_sigs = {_tool_call_signature(tc) for tc in tool_calls}
                degenerate_loop = (
                    len(recent_sigs) >= 2
                    and len(set(recent_sigs)) == 1
                    and new_sigs.issubset(set(recent_sigs))
                )
                # Result-level repeat: the model re-invokes a tool with slightly
                # different arguments but keeps getting back the *same answer*
                # (e.g. "what is the current working directory" → runs
                # os.getcwd() three times, each returning the identical path).
                # The argument-based guard above misses this because the code
                # text differs; comparing normalized results catches it.
                recent_results = _recent_tool_result_signatures(
                    state.get("tool_messages", []), window=3)
                result_repeat = (
                    len(recent_results) >= 3
                    and all(r == recent_results[0] for r in recent_results)
                    and recent_results[0] != ""
                )
                if exact_repeat or degenerate_loop or result_repeat:
                    prev_results = state.get("tool_results", [])
                    last_result = prev_results[-1] if prev_results else ""
                    if not _looks_like_error(last_result):
                        logger.warning(
                            "Tool decision: loop guard tripped (exact=%s degenerate=%s result=%s, round=%d) — forcing stop",
                            exact_repeat, degenerate_loop, result_repeat, tool_round,
                        )
                        _emit(state, "tool_loop_guard",
                              "Detected repeated calls to the same tool; auto-stopped to avoid an infinite loop and produced the final reply.")
                        return {"tool_calls": None}

            # Strip any raw [TOOL_CALL] wrapper / --code fragments the model may
            # have dumped into content alongside native tool_calls, so they
            # never surface in the chat or pollute the tool-execution history.
            clean_content = _strip_tool_call_noise(content) if content else ""
            tool_msg = {"role": "assistant", "content": clean_content, "tool_calls": tool_calls}
            _emit(state, "round", f"Tool-call round {tool_round + 1}")
            return {"tool_calls": tool_calls, "tool_messages": [tool_msg]}

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

def _build_download_entries(files: list[dict]) -> list[dict]:
    """Convert MCP ``structuredContent.files`` items into download entries.

    Each ``files`` item is ``{name, path, mimeType}``, where ``path`` is the
    sandbox-relative path (uid prefix already stripped by the MCP server).

    Produces ``{"url", "filename", "path"}`` entries pointing at RAGClaw's own
    ``/api/workspace/download`` endpoint — so the user only needs access to
    RAGClaw and the per-user Linux uid is NEVER exposed in a URL.
    """
    from app.config import settings
    public_base = settings.public_url.rstrip("/") if settings.public_url else ""
    base = f"{public_base}/api/workspace/download?path="
    entries: list[dict] = []
    seen: set = set()
    for f in files:
        if not isinstance(f, dict):
            continue
        rel = f.get("path") or f.get("name") or ""
        if not rel:
            continue
        url = f"{base}{quote(rel)}"
        filename = f.get("name") or rel.rsplit("/", 1)[-1] or "file"
        if url not in seen:
            seen.add(url)
            entries.append({"url": url, "filename": filename, "path": rel})
    return entries


def _normalize_download_url(url: str) -> str:
    """Rewrite the legacy public download proxy (/api/download/<uid>/...) to the
    workspace endpoint so the per-user Linux uid is never exposed in a URL.

    Legacy links can survive in model output (copied from earlier turns that used
    the old route) or in persisted history. Normalizing keeps every visible link
    uid-free and lets the idempotent injection drop duplicates instead of
    appending a second, differently-shaped link.
    """
    m = _re.match(r'^/api/download/user_u\d+/(.*)$', url)
    if m:
        return f"/api/workspace/download?path={m.group(1)}"
    return url


# NOTE: the markdown-format _extract_download_links_from_state helper was removed;
# download entries now flow through state["download_entries"] (see
# _extract_download_entries_from_state below) instead of being regex-parsed
# from tool text.


def _extract_download_entries_from_state(state: dict) -> list[dict]:
    """Return structured download entries emitted by the MCP REPL server.

    Files produced by tool calls travel through an LLM-independent channel:
    the MCP server returns them as ``structuredContent.files`` and they are
    accumulated in ``state.download_entries``. No regex parsing of tool text is
    performed — the old ``[File]``-tag convention has been removed.
    """
    return state.get("download_entries", []) or []


async def _execute_create_cron(state: dict, args: dict) -> dict:
    """Execute create_cron tool directly — write CronJob to DB without MCP round-trip.

    Session-injected identity (user_id, tenant_id, kb_id, skill_id, timezone,
    subdir) ensures the LLM cannot spoof ownership. The cron_expr is
    validated via compute_next_run before persisting.
    """
    from app.services.cron_graph import _make_create_tool

    # ── Validate required fields ──
    name = (args.get("name") or "").strip()
    cron_expr = (args.get("cron_expr") or "").strip()
    task_content = (args.get("task_content") or "").strip()

    if not name:
        return {"result": "[create_cron] error: 'name' is required", "endpoint": None}
    if not cron_expr:
        return {"result": "[create_cron] error: 'cron_expr' is required", "endpoint": None}
    if not task_content:
        return {"result": "[create_cron] error: 'task_content' is required", "endpoint": None}

    # ── Validate cron expression ──
    from croniter import croniter
    if not croniter.is_valid(cron_expr):
        return {"result": f"[create_cron] error: invalid cron expression: {cron_expr}", "endpoint": None}

    # ── Build tool with session-injected identity ──
    user_id = state.get("user_id")
    tenant_id = state.get("tenant_id")
    kb_id = state.get("kb_id")
    skill_id = (state.get("active_skill") or {}).get("id")
    subdir = state.get("subdir") or None

    # ── Determine timezone: prefer session-level, fall back to UTC ──
    timezone_str = state.get("timezone") or "UTC"

    tool = _make_create_tool(
        user_id=user_id,
        tenant_id=tenant_id,
        kb_id=kb_id,
        skill_id=skill_id,
        timezone=timezone_str,
        workspace_dir=subdir,
    )

    tool_args = {
        "name": name,
        "cron_expr": cron_expr,
        "task_content": task_content,
        "max_runs": args.get("max_runs"),
        "description": (args.get("description") or "").strip(),
    }

    try:
        result_json = await tool.ainvoke(tool_args)
        return {"result": f"[create_cron] {result_json}", "endpoint": None}
    except Exception as e:
        logger.exception("create_cron tool execution failed")
        return {"result": f"[create_cron] error: {e}", "endpoint": None}


async def _execute_update_memory(state: dict, args: dict) -> dict:
    """Execute update_memory tool directly — read or write profile memory.

    Session-injected ``user_id`` / ``tenant_id`` ensure the LLM cannot spoof
    ownership. ``action`` selects the operation:
      - 'read': return the current memory text verbatim (no mutation).
      - 'write': persist ``content`` as the full, updated memory text, replacing
        the previous content. Capped at 2000 characters (matching the frontend
        maxlength). An empty/whitespace-only ``content`` is VALID and clears the
        memory. When the supplied text exceeds the cap, the write is REJECTED
        (no silent truncation) and the model is told to surface this to the user
        and point them to the Profile page. The LLM is expected to have read first
        and supplied the complete edited text (or an empty string to clear).
    """
    from app.models.user import User

    action = (args.get("action") or "read").strip().lower()
    if action not in ("read", "write"):
        return {"result": "[update_memory] error: 'action' must be 'read' or 'write'", "endpoint": None}

    user_id = state.get("user_id")
    tenant_id = state.get("tenant_id")
    if not user_id:
        return {"result": "[update_memory] error: no authenticated user in session", "endpoint": None}

    MAX_LEN = 2000

    async with async_session() as db:
        query = select(User).where(User.id == user_id)
        if tenant_id is not None:
            query = query.where(User.tenant_id == tenant_id)
        user = (await db.execute(query)).scalar_one_or_none()
        if not user:
            return {"result": "[update_memory] error: user not found", "endpoint": None}

        if action == "read":
            current = user.memory or ""
            return {
                "result": f"[update_memory] read: current memory ({len(current)} chars):\n"
                          f"<<<MEMORY_START>>>\n{current}\n<<<MEMORY_END>>>",
                "endpoint": None,
            }

        # action == 'write'
        # Empty content is allowed and means "clear the memory". Only strip
        # surrounding whitespace; a blank/whitespace-only value clears memory.
        content = (args.get("content") or "").strip()
        if len(content) > MAX_LEN:
            # Reject instead of silently truncating: the model must tell the user
            # and let them edit on the Profile page. No DB write happens.
            logger.warning(
                "update_memory: write rejected for user=%s (len=%d exceeds cap=%d)",
                user_id, len(content), MAX_LEN,
            )
            return {
                "result": f"[update_memory] write rejected: {_t('memory_too_long', config_manager.prompt_language)}",
                "endpoint": None,
            }

        user.memory = content
        await db.commit()

    logger.info("update_memory: wrote memory for user=%s (len=%d)", user_id, len(content))
    if len(content) == 0:
        return {
            "result": "[update_memory] write: memory cleared (empty content). Profile memory updated.",
            "endpoint": None,
        }
    return {
        "result": f"[update_memory] write: saved {len(content)} chars. Profile memory updated.",
        "endpoint": None,
    }


async def _execute_hybrid_search(state: dict, args: dict) -> dict:
    """Execute hybrid_search tool directly — on-demand KB retrieval mid-graph.

    Unlike the entry retrieval node (parallel_retrieval_node), this runs when the
    LLM decides it must re-retrieve with a rewritten, self-contained query (e.g.
    to resolve anaphora like "those meetings" against conversation history). The
    session-injected ``kb_id`` ensures the LLM cannot target an arbitrary KB.
    Results are returned as plain text only and are NOT merged into
    state["citations"], so no frontend citation rendering occurs.

    Returns:
        {"result": str, "endpoint": None} — same shape as the other meta tools.
    """
    kb_id = state.get("kb_id")
    if not kb_id:
        return {"result": "[hybrid_search] error: no knowledge base selected in this session", "endpoint": None}

    query = (args.get("query") or "").strip()
    if not query:
        return {"result": "[hybrid_search] error: 'query' is required (self-contained, anaphora resolved)", "endpoint": None}

    # Optional passthrough params — coerced defensively since args come from LLM JSON.
    top_k = args.get("top_k")
    if top_k is not None:
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = None
        else:
            # Clamp the LLM-supplied top_k: non-positive values fall back to the
            # system default (a negative would otherwise reach fuse() and slice
            # results[:-1]), and anything above retrieval_final_top_k is capped to
            # avoid blowing up the context beyond the system's retrieval limit.
            if top_k < 1:
                top_k = None
            else:
                top_k = min(top_k, settings.retrieval_final_top_k)
    doc_ids = args.get("doc_ids")
    if not isinstance(doc_ids, list):
        doc_ids = None

    try:
        rag_context, citations = await hybrid_search._run_hybrid_retrieval(kb_id, query, doc_ids=doc_ids, top_k=top_k)
    except Exception as e:
        logger.exception("hybrid_search meta tool execution failed")
        return {"result": f"[hybrid_search] error: {e}", "endpoint": None}

    # Assemble result text. Citations are surfaced as a deduped doc list (for
    # follow-up doc_ids narrowing) but NEVER written to state["citations"].
    result = f"[hybrid_search] {rag_context}"
    if citations:
        seen: set[str] = set()
        doc_lines = []
        for c in citations:
            did = c.get("doc_id") or "unknown"
            if did in seen:
                continue
            seen.add(did)
            dname = c.get("doc_name") or "?"
            doc_lines.append(f"- {dname} (doc_id={did})")
        if doc_lines:
            result += "\n\nMatched documents (pass doc_ids to narrow a follow-up search):\n" + "\n".join(doc_lines)
    return {"result": result, "endpoint": None}


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
        _emit(state, "tool", f"Running tool: {label}", tool=tname)

        # ── Command-level logging (REPL tools) ──
        # The RESULT line below only logs the truncated *output*. This logs the
        # exact command/code the REPL tool executed, so we can see e.g. the real
        # search query anysearch's shim was invoked with (previously hidden).
        if tname in ("run_shell", "run_python", "run_javascript"):
            logger.warning(">>> tool_executor CMD: tool=%s args=%.1000s <<<",
                           tname, json.dumps(args, ensure_ascii=False))

        tool_def = tool_lookup.get(tname, {})
        tool_source = tool_def.get("_source", "mcp")

        # ── Meta control tools should never reach the executor ──
        if tname in _meta_control_names():
            return {"result": f"[{tname}] meta-tools must not be called during the tool-execution stage", "endpoint": None}

        # ── Script tool path ──
        if tool_source == "script" and folder_name:
            script_path = tool_def.get("_script_path", "")
            func_name = tool_def.get("_func_name", tname)
            # Find the python_repl MCP server config
            repl_config = await _get_repl_server_config()
            if not repl_config:
                return {"result": f"[{tname}] error: Python Executor MCP Server not configured", "endpoint": None}
            result = await execute_script_tool(
                folder_name, script_path, func_name, args, repl_config,
                subdir=state.get("subdir"),
                user_id=state.get("user_id"),
            )
            if result.ok:
                return {"result": f"[{tname}] {result.result}", "endpoint": repl_config.get("endpoint"),
                        "files": getattr(result, "files", None) or []}
            return {"result": f"[{tname}] error: {result.error}", "endpoint": repl_config.get("endpoint"),
                    "files": []}

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

        # ── create_cron: intercepted tool, write to DB directly (no MCP call) ──
        if tname == "create_cron":
            return await _execute_create_cron(state, args)

        # ── update_memory: intercepted tool, append to user profile memory ──
        if tname == "update_memory":
            return await _execute_update_memory(state, args)

        # ── hybrid_search: intercepted tool, retrieve from KB on demand (no MCP call) ──
        if tname == "hybrid_search":
            return await _execute_hybrid_search(state, args)

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
                    return {"result": f"[{tname}] error: MCP server not found", "endpoint": None}
        else:
            # Fallback: try to find run_python on the default python_repl server
            if tname == "run_python":
                repl_config = await _get_repl_server_config()
                if not repl_config:
                    return {"result": f"[{tname}] error: Python Executor MCP Server not configured", "endpoint": None}
                endpoint = repl_config.get("endpoint")
                cfg = repl_config
            else:
                return {"result": f"[{tname}] error: no MCP server binding for tool", "endpoint": None}
        try:
            # Share the conversation workspace so chained skills can read
            # files produced by an earlier skill's tool call.
            call_args = dict(args)
            ws_id = state.get("subdir")
            if ws_id:
                call_args["subdir"] = ws_id
            # Propagate the user's local timezone to the REPL sandbox so that
            # code using datetime.now()/time.strftime stamps files with the
            # user's local time instead of the container's default (UTC).
            # Falls back to UTC when the client did not send one.
            call_args["timezone"] = state.get("timezone") or "UTC"
            res = await _mc.call_tool(cfg, tname, call_args, auth_user=state.get("user_id"))
            logger.warning(">>> tool_executor RESULT: tool=%s ok=%s result=%.200s <<<",
                          tname, res.ok, (res.result or res.error)[:200])
            if res.ok:
                return {"result": f"[{tname}] {res.result}", "endpoint": endpoint,
                        "files": getattr(res, "files", None) or []}
            return {"result": f"[{tname}] error: {res.error}", "endpoint": endpoint, "files": []}
        except Exception as e:
            return {"result": f"[{tname}] execution error: {str(e)}", "endpoint": endpoint}

    tasks = [execute_one(tc) for tc in tool_calls]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    # Unwrap: each item is a dict {"result": str, "endpoint": str|None,
    # "files": list[dict]} or Exception. ``files`` comes from the MCP
    # structuredContent.files channel and feeds state.download_entries.
    tr = []
    download_acc = []
    for i, item in enumerate(raw):
        if isinstance(item, Exception):
            tr.append(str(item))
        elif isinstance(item, dict):
            tr.append(item.get("result", ""))
            files = item.get("files") or []
            if files:
                entries = _build_download_entries(files)
                download_acc.extend(entries)
                tcname = tool_calls[i].get("function", {}).get("name", "unknown") if i < len(tool_calls) else "unknown"
                for fe in entries:
                    _emit(state, "tool_done", f"File generated: {fe['filename']}", tool=tcname, detail=fe["filename"])
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
    return {"tool_results": tr, "tool_messages": result_msgs, "tool_round": state.get("tool_round", 0) + 1,
            "download_entries": download_acc}


async def _get_repl_server_config() -> dict | None:
    """Get the python_repl MCP server config by name 'Python executor'."""
    async with async_session() as db:
        srv = (await db.execute(
            select(MCPServer).where(MCPServer.name == "Python Executor").limit(1)
        )).scalar_one_or_none()
        if not srv:
            return None
        return {"id": srv.id, "transport_type": srv.transport_type, "endpoint": srv.endpoint,
                "command": srv.command, "args_json": srv.args_json, "env_json": srv.env_json,
                "timeout_seconds": srv.timeout_seconds}


# ── Build Context ──

async def build_context_node(state: dict) -> dict:
    return {}

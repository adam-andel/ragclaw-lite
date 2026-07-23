"""Agent graph nodes for the RAGClaw LangGraph state machine."""
import asyncio, json, logging, time
from datetime import datetime
from sqlalchemy import select
from app.config import settings
from app.database import async_session
from app.models.skill import Skill, MCPServer
from app.services.hybrid_search import hybrid_search
from app.services.vector_store import vector_store
from app.services.bm25_index import bm25_index
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

logger = logging.getLogger("ragclaw.agent")
logger.setLevel(logging.INFO)

MAX_TOOL_ROUNDS = 5
MAX_SKILL_SWITCHES = 4  # Route D: cap on use_skill pushes to prevent runaway chaining

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


# ── Bilingual Agent-Graph prompts (A/B test: config_manager.prompt_language = "zh" | "en") ──
# zh = original Chinese prompts (unchanged behavior). en = English A/B variants that aim to
# improve instruction-following on English-dominant base models (GPT/Claude/DeepSeek etc.).

def build_intent_router_prompt(query: str, skill_list: str, lang: str = "zh") -> str:
    """Layer-1 intent-router prompt. lang='zh' reproduces the original behavior."""
    if lang == "en":
        return (
            "You are an intent router. Based on the user's question, select the most "
            "appropriate skill from the following NUMBERED list.\n\n"
            "Available skills:\n" + skill_list + "\n\n"
            "Rules:\n"
            "- If the user's question closely matches a skill, return that skill's NUMBER.\n"
            '- If the user\'s question does not match any skill, return 0.\n'
            "- Return ONLY a single integer (the skill number, or 0). No other output.\n\n"
            "User question: " + query + "\n\n"
            "Number:"
        )
    return (
        "你是一个意图路由器。根据用户的问题，从以下编号技能中选择最合适的一个。\n\n"
        f"可用技能：\n{skill_list}\n\n"
        "规则：\n"
        "- 如果用户的问题与某个技能高度匹配，返回该技能的**编号**\n"
        "- 如果用户的问题与所有技能都不匹配，返回 0\n"
        "- 只返回一个整数（技能编号，或 0），不要有任何其他输出\n\n"
        f"用户问题：{query}\n\n"
        "编号："
    )


def build_tool_system_prompt(tool_desc: str, lang: str = "zh") -> str:
    """Forced tool-call JSON system prompt. lang='zh' reproduces the original behavior."""
    if lang == "en":
        return (
            "# ⚠️ CRITICAL INSTRUCTION: Decide whether to call a tool (or explicitly stop)\n\n"
            "Your job is to decide whether another tool call is still needed.\n\n"
            "## When to STOP calling tools (most important)\n"
            "- If the prior tool results ALREADY fully satisfy the user's request "
            "(e.g. the file was successfully created/modified, the computation is done), "
            "do NOT call any tool again.\n"
            "- Output ONLY an EMPTY object `{}` and the system will generate the final reply.\n"
            "- NEVER call the same tool again just to 'confirm' a result, and NEVER repeat a "
            "write/append that already achieved its goal.\n"
            "- Only keep calling tools when further action is genuinely required.\n\n"
            "## When you MUST use a tool\n"
            "- User asks to generate / create / write / save / append / modify a file -> MUST call run_python\n"
            "- User asks to run code, do data processing, or compute -> MUST call run_python\n"
            "- Any file read/write operation that is NOT yet done -> MUST call run_python\n\n"
            "## Available tools\n" + tool_desc + "\n\n"
            "## Output format\n"
            'When a tool is still needed: {"tool": "tool_name", "arguments": {"arg_name": "arg_value"}}\n'
            'When the task is done (no tool needed): {}\n\n'
            "## Rules\n"
            "- Output ONLY the JSON object above, with no extra text.\n"
            '- You MUST use double quotes (") and MUST NOT use single quotes (\').\n'
            "- You MUST NOT use => arrow syntax; use the standard JSON colon (:).\n"
            "- Do NOT wrap the JSON in ``` code fences.\n"
            "- Do NOT output [TOOL_CALL] or <tool_call> tags.\n"
            '- Escape double quotes inside code arguments as \\", and use \\n for newlines.\n'
            "- Do NOT output the final reply; if the task is done, output {} instead.\n"
            "- NEVER fabricate File, file paths, or UUIDs."
        )
    return (
        "# ⚠️ 关键指令：判断是否需要调用工具（或显式停止）\n\n"
        "你的职责是判断「现在是否还需要继续调用工具」。\n\n"
        "## 何时停止调用工具（最重要）\n"
        "- 如果**之前的工具结果已经完整满足用户的需求**（例如：文件已成功创建/修改、计算已完成、数据已处理），**不要再调用任何工具**。\n"
        "- 此时请只输出一个**空对象** `{}`，系统会据此生成最终回复给用户。\n"
        "- **绝对不要**为了「确认结果」「再检查一次」而重复调用同一个工具；也不要重复执行一个已经达成目标的写入/追加操作（例如文件里已经有用户要的内容了，就不要再写一遍）。\n"
        "- 只有当确实需要进一步操作（如「先读后写」、真正的多步流程）时，才继续调用工具。\n\n"
        "## 何时必须使用工具\n"
        "- 用户要求「生成」「创建」「写入」「保存」「追加」「修改」文件 → **必须**调用 run_python\n"
        "- 用户要求执行代码、数据处理、计算 → **必须**调用 run_python\n"
        "- 任何**尚未完成**的读写文件操作 → **必须**调用 run_python\n\n"
        "## 可用工具\n" + tool_desc + "\n\n"
        "## 输出格式\n"
        '还需要调用工具时：{"tool": "工具名", "arguments": {"参数名": "参数值"}}\n'
        '任务已完成、无需再调用工具时：{}\n\n'
        "## 规则\n"
        "- 只输出上述 JSON 对象，不要附加任何多余文字\n"
        "- **必须**使用双引号（\"），**绝对不能**使用单引号（'）\n"
        "- **绝对不能**使用 => 箭头语法，必须是 JSON 标准的 : 冒号\n"
        "- 不要用 ``` 包裹 JSON\n"
        "- 不要输出 [TOOL_CALL] 或 <tool_call> 标签\n"
        "- 代码参数中的双引号需用 \\\" 转义，换行用 \\n\n"
        "- **绝对不要**编造File、文件路径或 uuid"
    )


def build_skill_switch_limit_message(name: str, switch_count: int, quota: int, lang: str = "zh") -> str:
    """Suspension message when skill-switch quota is exhausted. lang='zh' = original."""
    if lang == "en":
        return (
            f"use_skill: skill-switch limit reached ({switch_count}/{quota}); "
            f'cannot load "{name}". Reply "continue" to add quota and auto-retry.'
        )
    return (
        f"use_skill：已达技能切换上限（{switch_count}/{quota}），"
        f"无法加载「{name}」。请回复「继续」以追加额度后自动重试。"
    )


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
    added (e.g. "好的，我来生成文件") is preserved.
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
    """
    snippet = (bad_output or "")[:1500]
    if lang == "en":
        return (
            "Your previous tool call was NOT valid JSON and could not be parsed. "
            f"You MUST call the tool `{tool_name}` now.\n\n"
            "## Your previous (invalid) output\n" + snippet + "\n\n"
            "## Requirements\n"
            f'- Output ONLY a single pure JSON object: {{"tool": "{tool_name}", "arguments": {{...}}}}\n'
            "- Use double quotes (\") only. NEVER use single quotes, `=>`, `--code`, "
            "or [TOOL_CALL] tags.\n"
            '- Escape any double quotes inside string values as \\", and newlines as \\n.\n'
            "- Do NOT add any explanation before or after the JSON."
        )
    return (
        "你上一次的工具调用不是合法 JSON，无法被解析。"
        f"你现在必须调用工具 `{tool_name}`。\n\n"
        "## 你上一次的（非法）输出\n" + snippet + "\n\n"
        "## 要求\n"
        f'- 只输出一个纯 JSON 对象：{{"tool": "{tool_name}", "arguments": {{...}}}}\n'
        "- 只能使用双引号（\"）；绝对不要使用单引号、`=>`、`--code` 或 [TOOL_CALL] 标签\n"
        "- 字符串值内部的双引号用 \\\" 转义，换行用 \\n\n"
        "- JSON 前后不要附加任何解释文字"
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
                    "tool_messages": [], "kb_prompt": kb_prompt}
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
    _emit(state, "routing", "分析意图并选择技能…")
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
        active_skill = await _route_to_best_skill(query, tenant_id, user_id, skills=skills)
        logger.info("Router: auto-routed to skill=%s", active_skill.get('name') if active_skill else 'NONE')
        # Fallback: keyword-based routing for file/code generation intent. This
        # avoids depending on the LLM returning the exact skill name.
        if not active_skill:
            active_skill = _route_by_keywords(query, skills)
            logger.info("Router: keyword-routed to skill=%s", active_skill.get('name') if active_skill else 'NONE')

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


async def _route_to_best_skill(query, tenant_id, user_id, skills=None) -> dict | None:
    """Auto-route using LLM to match query against skill name + description (Layer 1).

    Returns {id, name, description, folder_name} or None.
    """
    if skills is None:
        async with async_session() as db:
            skills = (await db.execute(
                select(Skill).where((Skill.tenant_id == tenant_id) & (Skill.is_active == True))  # noqa: E712
            )).scalars().all()
    if not skills:
        return None
    skill_list = "\n".join(f"{i+1}. {s.name}: {s.description or '(无描述)'}" for i, s in enumerate(skills))
    prompt = build_intent_router_prompt(query, skill_list, lang=config_manager.prompt_language)
    try:
        raw = (await llm_client.chat(messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=50)).strip()
        # The router now returns a 1-based skill NUMBER (or 0 for none). Parse the
        # first integer so trailing text like "1." / "编号 1" still matches.
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
    file/document handling, so "生成文件mydoc.txt" maps to e.g. Document
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
                    "加载并使用另一个技能。加载后该技能的规则与工具立即生效，当前对话即可调用其能力。"
                    "适用于当前技能无法直接完成的子任务。注意：文件创建/读取/修改/删除与代码运行是你"
                    "的原生能力（直接调用 run_python 即可），只有当需要某个额外专用技能时才用 use_skill。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "要使用的技能名称。可先调用 list_skills 查看可用技能。",
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
                    "结束当前临时技能，返回到上一层技能。无需返回时不必调用。"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            "_source": "meta",
        },
    ]


# Module-level cache of Python Executor meta tools (always-available native tools).
# Populated at startup (lifespan) via _refresh_meta_python_tools and used as a
# fallback here when startup missed it. These are the "元工具" the Python
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


async def _build_all_meta_tools() -> list[dict]:
    """Always-available meta tools: control tools + Python Executor native tools.

    The Python Executor tools (e.g. run_python) are fetched from the server at
    startup; _refresh_meta_python_tools populates the cache, with a lazy fetch
    here as a fallback if startup missed it (e.g. MCP not reachable yet).
    """
    python_tools = _META_PYTHON_TOOLS
    if not python_tools:
        await _refresh_meta_python_tools()
        python_tools = _META_PYTHON_TOOLS
    return _build_meta_skill_tools() + python_tools


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
    meta_tools = await _build_all_meta_tools()

    active_skill = state.get("active_skill")
    if not active_skill:
        # No skill selected — still expose the native meta tools so the agent can
        # operate on files / run code without routing through a skill.
        return {"available_tools": meta_tools}

    folder_name = active_skill.get("folder_name")
    if not folder_name:
        return {"available_tools": meta_tools}

    system_prompt, all_tools = await _load_skill_body_and_tools(folder_name)
    updated_skill = {**active_skill, "system_prompt": system_prompt, "source": "primary"}

    # Always-available meta tools for orchestration + native execution (Route D)
    all_tools = all_tools + meta_tools

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
            quota = state.get("skill_switch_quota", MAX_SKILL_SWITCHES)
            if switch_count >= quota:
               # Suspend: wait for user confirmation ("continue" = replay after adding quota) instead of silently rejecting
                msg = build_skill_switch_limit_message(
                    name, switch_count, quota, lang=config_manager.prompt_language
                )
                _emit(state, "skill_switch_fail", msg, skill=name)
                return {
                    "pending_limit": {
                        "kind": "skill_switch",
                        "message": msg,
                        "deferred_tool_call": tc,
                    },
                }
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
    # retrieve over, so skip the (vector + BM25) hybrid search entirely. Only
    # the user-scoped memory search still runs.
    if not kb_id:
        _emit(state, "retrieval_done", "未选择知识库，跳过检索", detail="skip")
        memory_context = ""
        if user_id:
            mem_raw = await _search_memories_safe(query, user_id)
            memory_context = _build_memory_context(mem_raw) if mem_raw else ""
        return {"rag_context": "", "citations": [], "memory_context": memory_context,
                "retrieval_ms": 0}
    _emit(state, "retrieval", "检索知识库…")
    t_start = time.time()
    loop = asyncio.get_running_loop()

    # Run vector + BM25 concurrently. BM25 is an in-memory jieba+rank_bm25 call
    # (sub-10ms); vector is the slow path (embedding + Chroma query). Overlapping
    # them via separate executor threads drops total latency from
    # T_vec + T_bm25 to max(T_vec, T_bm25). fuse() then merges both result sets.
    v_task = loop.run_in_executor(None, vector_store.search, kb_id, query, settings.retrieval_vector_top_k)
    b_task = loop.run_in_executor(None, bm25_index.search, kb_id, query, settings.retrieval_bm25_top_k)
    mem_coro = _search_memories_safe(
        query, user_id,
        agent_id=state.get("kb_id"),
        run_id=state.get("conversation_id"),
    ) if user_id else None

    if mem_coro:
        v_res, b_res, mem_raw = await asyncio.gather(v_task, b_task, mem_coro, return_exceptions=True)
    else:
        v_res, b_res = await asyncio.gather(v_task, b_task, return_exceptions=True)
        mem_raw = []

    if isinstance(v_res, Exception):
        logger.warning("Vector search error: %s", v_res)
        v_res = []
    if isinstance(b_res, Exception):
        logger.warning("BM25 search error: %s", b_res)
        b_res = []
    if isinstance(mem_raw, Exception):
        logger.warning("Mem0 search error: %s", mem_raw)
        mem_raw = []

    retrieved = hybrid_search.fuse(v_res, b_res)
    rag_context, citations = _build_context(retrieved)
    memory_context = _build_memory_context(mem_raw) if mem_raw else ""
    chunk_count = len(retrieved) if isinstance(retrieved, list) else 0
    _emit(state, "retrieval_done", f"检索完成，命中 {chunk_count} 段", detail=f"{chunk_count} 段")
    return {"rag_context": rag_context, "citations": citations, "memory_context": memory_context,
            "retrieval_ms": round((time.time() - t_start) * 1000)}


async def _search_memories_safe(
    query: str, user_id: str, limit: int = 5,
    agent_id: str | None = None, run_id: str | None = None,
) -> list[dict]:
    try:
        from app.services.memory import search_memories
        return await search_memories(
            query, user_id=user_id, limit=limit,
            agent_id=agent_id, run_id=run_id,
        ) or []
    except ImportError:
        return []
    except Exception as e:
        logger.warning("Mem0 search failed: %s", e)
        return []


def _build_context(retrieved: list[dict]) -> tuple[str, list[dict]]:
    if not retrieved:
        return "未找到相关文档", []
    parts, citations = [], []
    # Defense-in-depth: collapse display-identical sources so the UI never
    # shows what looks like the same chunk twice (e.g. same doc_id + chunk_index
    # + heading). Distinct sections survive because their headings differ.
    seen_keys: set[tuple] = set()
    for i, r in enumerate(retrieved):
        doc_name = r.get("doc_name") or r.get("doc_id", "?")[:8]
        heading = r.get("heading", "") or ""
        page = r.get("page")
        if page == 0:
            page = None
        key = (r.get("doc_id", ""), r.get("chunk_index", 0), heading)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        parts.append(f"[{i + 1}] {doc_name} {heading}\n{r['content']}")
        citations.append({"doc_id": r.get("doc_id", ""), "doc_name": doc_name,
                          "chunk_index": r.get("chunk_index", 0), "heading": heading,
                          "page": page, "score": round(r.get("fusion_score", 0), 4)})
    return "\n\n---\n\n".join(parts), citations


def _build_memory_context(memories: list[dict]) -> str:
    return "\n".join(f"- {m.get('memory', m.get('text', str(m)))}" for m in memories if m.get('memory') or m.get('text'))


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


async def tool_decision_node(state: dict) -> dict:
    if state.get("cache_hit"):
        return {}
    available_tools = state.get("available_tools", [])
    tool_round = state.get("tool_round", 0)
    prev_results = state.get("tool_results", [])
    # Intentionally logs every decision entry at WARNING level to aid debugging
    # of tool-call loops (see the deterministic loop guard below). Keep this.
    logger.warning("🔍 tool_decision ENTER: round=%d tools=%d prev_results=%d cache=%s",
                   tool_round, len(available_tools), len(prev_results),
                   state.get("cache_hit"))
    if not available_tools:
        logger.warning("Tool decision: no available tools — skipping tool phase")
        return {"tool_calls": None}
    if tool_round >= state.get("tool_round_quota", MAX_TOOL_ROUNDS):
        quota = state.get("tool_round_quota", MAX_TOOL_ROUNDS)
        logger.warning("Tool decision: max rounds reached (round=%d, quota=%d)", tool_round, quota)
       # Suspend: rounds exhausted, wait for user confirmation (after resume the LLM re-decides, because the LLM was not called yet when the limit was hit)）
        msg = (f"工具调用轮次已达上限（{tool_round}/{quota}），"
               f"请回复「继续」以追加轮次后继续。")
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
        tool_system = build_tool_system_prompt(tool_desc, lang=config_manager.prompt_language)
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
            logger.warning("Tool decision: trying chat_with_tools (native), %d tools, round=%d, forced=%s",
                       len(available_tools), tool_round, forced_tool)
            response = await _chat_with_tools_resilient(
                messages, available_tools, tool_choice,
                temperature=0.1, max_tokens=config_manager.agent_max_tokens,
            )
            tool_calls = response.get("tool_calls")
            content = response.get("content") or ""
            logger.warning("Tool decision: native_tool_calls=%s content_preview=%.200s",
                       bool(tool_calls), content[:200])
        except Exception as native_err:
            # A forced tool_choice dict may 400 on an incompatible model — retry
            # once with "auto" before falling all the way back to plain text mode.
            logger.warning("Tool decision: chat_with_tools failed (%s)", str(native_err)[:200])
            if forced_tool:
                try:
                    logger.warning("Tool decision: retrying with tool_choice=auto (forced tool rejected)")
                    response = await _chat_with_tools_resilient(
                        messages, available_tools, "auto",
                        temperature=0.1, max_tokens=config_manager.agent_max_tokens,
                    )
                    tool_calls = response.get("tool_calls")
                    content = response.get("content") or ""
                except Exception as retry_err:
                    logger.warning("Tool decision: auto retry failed (%s), text mode", str(retry_err)[:200])
                    content = await llm_client.chat(messages=messages, temperature=0.1, max_tokens=config_manager.agent_max_tokens)
            else:
                logger.warning("Tool decision: falling back to text mode")
                content = await llm_client.chat(messages=messages, temperature=0.1, max_tokens=config_manager.agent_max_tokens)
            logger.warning("Tool decision: fallback content_preview=%.200s", content[:200])

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

        # ── Layer 2: self-heal retry ──
        # Parsing failed but the LLM emitted text that names an available tool —
        # it clearly INTENDED to call a tool, just botched the format. Ask it to
        # rewrite as valid JSON (forcing that tool), feeding back the bad output.
        if not tool_calls and content:
            intended = _extract_intended_tool_name(content, available_tools)
            if intended:
                logger.warning("Tool decision: entering self-heal for tool '%s' (round %d)", intended, tool_round)
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
                            temperature=0.0, max_tokens=config_manager.agent_max_tokens,
                        )
                    except Exception as heal_err:
                        # Forced dict may be rejected on retry — try once with auto.
                        logger.warning("Tool decision: self-heal attempt %d failed (%s), trying auto",
                                       attempt + 1, str(heal_err)[:150])
                        try:
                            heal_resp = await _chat_with_tools_resilient(
                                heal_messages, available_tools, "auto",
                                temperature=0.0, max_tokens=config_manager.agent_max_tokens,
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
                        logger.warning("Tool decision: self-heal SUCCESS on attempt %d (tool '%s')",
                                       attempt + 1, intended)
                        tool_calls = heal_tc
                        content = ""
                        break
                    # Feed forward the latest (still-bad) output for the next attempt.
                    bad_output = heal_content or bad_output
                if not tool_calls:
                    logger.warning("Tool decision: self-heal exhausted %d attempts for '%s'",
                                   SELF_HEAL_MAX_RETRIES, intended)

        if tool_calls:
            # ── Deterministic loop guard ──
            # The model is unreliable at self-terminating: after a successful
            # append/write it often re-issues the SAME tool call, producing an
            # infinite loop that only pauses at the round quota (the "继续"
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
                if exact_repeat or degenerate_loop:
                    prev_results = state.get("tool_results", [])
                    last_result = prev_results[-1] if prev_results else ""
                    if not _looks_like_error(last_result):
                        logger.warning(
                            "Tool decision: loop guard tripped (exact=%s degenerate=%s, round=%d) — forcing stop",
                            exact_repeat, degenerate_loop, tool_round,
                        )
                        _emit(state, "tool_loop_guard",
                              "已检测到重复调用相同工具，自动停止以避免死循环，并生成最终回复。")
                        return {"tool_calls": None}

            # Strip any raw [TOOL_CALL] wrapper / --code fragments the model may
            # have dumped into content alongside native tool_calls, so they
            # never surface in the chat or pollute the tool-execution history.
            clean_content = _strip_tool_call_noise(content) if content else ""
            tool_msg = {"role": "assistant", "content": clean_content, "tool_calls": tool_calls}
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

    Generates download URLs pointing to RAGClaw's own ``/api/workspace/download``
    endpoint, so the user only needs access to RAGClaw — the MCP server stays
    fully internal with no host port exposure. The uid is resolved server-side
    from the caller's session, so the per-user Linux uid (``user_uNNNN``) is
    NEVER included in the URL.

    ``uuid_dir`` is the full relative path under the sandbox's allow-dir, which
    may be nested (e.g. ``user_u2001/<ws>``) when per-user isolation is active.
    Only the sandbox-relative part (uid prefix stripped) goes into the link.
    """
    from urllib.parse import quote

    m = _re.search(r'\[workspace:\s*([\w/-]+)/\]', result)
    if not m:
        return result
    uuid_dir = m.group(1).strip("/")
    if not uuid_dir:
        return result

    # Do NOT leak the per-user Linux uid in the URL. The workspace proxy
    # resolves the uid server-side from the caller's session, so only the
    # sandbox-relative path (uid prefix stripped) belongs in the link.
    # The uid may be a bare root (user_u10000) or nested (user_u10000/<ws>),
    # so strip the trailing slash optionally.
    rel = _re.sub(r'^user_u\d+/?', '', uuid_dir)
    from app.config import settings
    public_base = settings.public_url.rstrip("/") if settings.public_url else ""
    # Build the query path WITHOUT a leading slash (bare root -> "" so the file
    # segment is appended directly). The workspace endpoint lstrip()s a leading
    # slash anyway, but keeping it consistent avoids two shapes of the same link.
    query_path = quote(rel) + ("/" if rel else "")
    proxy_prefix = f"{public_base}/api/workspace/download?path={query_path}"

    if "[File]" in result:
        # MCP server included [File] tags with its own URL — rewrite to the
        # RAGClaw workspace download endpoint. uuid_dir may contain "/" (nested
        # per-user path), so escape it. The captured group is the file path
        # under uuid_dir; encode it so spaces/Unicode stay valid in the URL.
        result = _re.sub(
            r'(?<=\[File\] )\S+/files/' + _re.escape(uuid_dir) + r'/(\S+)',
            lambda mo: f"{proxy_prefix}/{quote(mo.group(1))}",
            result
        )
    # The [workspace: <uuid>/] tag carries the per-user Linux uid and is only
    # needed above to rewrite [File] links — strip it so the uid never persists
    # in tool results / conversation history.
    result = _re.sub(r'\[workspace:\s*[\w/-]+/\]', '', result)

    # If MCP didn't generate [File] links (missing REPL_PUBLIC_URL),
    # we don't fabricate broken links — let the result speak for itself.

    return result


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


def _extract_download_links_from_state(state: dict) -> str:
    """Scan tool results for download links and format them for final display.

    This runs OUTSIDE the LLM — links are system-generated, never hallucinated.
    Formats [File] tags as clickable Markdown links the frontend can render.
    Supports both absolute (https://...) and relative (/api/workspace/download?path=...) URLs.
    """
    tool_results = state.get("tool_results", [])
    links = []
    seen = set()
    for r in tool_results:
        # Match both absolute and relative [File] URLs
        for url_match in _re.finditer(
            r'\[File\]\s*((?:https?://\S+|/api/download/\S+|/api/workspace/download\S+))', r
        ):
            url = _normalize_download_url(url_match.group(1))
            filename = url.rstrip("/").rsplit("/", 1)[-1]
            # Dedupe by the RENDERED line, not just the raw URL: a proxy URL
            # and a raw REPL URL (or a trailing-slash variant) may render
            # identically yet differ as strings, which a raw-URL dedup would
            # miss and would surface as two duplicate download links.
            line = f"- [📥 {filename}]({url})"
            if line not in seen:
                seen.add(line)
                links.append(line)
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
                return {"result": f"[{tname}] 错误: Python Executor MCP Server 未配置", "endpoint": None}
            result = await execute_script_tool(
                folder_name, script_path, func_name, args, repl_config,
                workspace_id=state.get("workspace_id"),
                user_id=state.get("user_id"),
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
                    return {"result": f"[{tname}] 错误: Python Executor MCP Server 未配置", "endpoint": None}
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
            res = await _mc.call_tool(cfg, tname, call_args, auth_user=state.get("user_id"))
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
        fm = _re.search(r'\[File\]\s*((?:https?://\S+|/api/workspace/download\S+))', r)
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

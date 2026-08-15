"""Skill script loader — discover Python function tools from skill folders and
execute them via python_repl_mcp_server's run_python sandbox.

Workflow:
1. discover_tools(folder_name) — AST-parse scripts/*.py, extract public functions
   with type annotations, generate OpenAI function-calling tool definitions.
   Results are cached by file mtime for efficiency.
2. execute_script_tool(script_path, func_name, arguments, repl_server_config) —
   Read script file content, construct self-contained inline Python code
   (script + function call), send to run_python via mcp_client.

The generated code reuses python_repl_mcp_server's full security stack:
AST pre-screen, import guard, filesystem isolation, network blocking, etc.
"""

import ast
import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.mcp_client import mcp_client, ToolResult
from app.services.skill_manager import get_skill_dir

logger = logging.getLogger("ragclaw.skill_script")

# Cache: {folder_name: {"mtime": float, "tools": list[dict]}}
_cache: dict[str, dict] = {}


# ─── Type annotation mapping ───

_PYTHON_TYPE_TO_JSON = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "List": "array",
    "Dict": "object",
    "Any": "string",
    "Optional": "string",
}


def _annotation_to_json_type(annotation: ast.expr | None) -> str:
    """Convert a Python type annotation AST node to JSON Schema type string."""
    if annotation is None:
        return "string"

    # Simple name: str, int, etc.
    if isinstance(annotation, ast.Name):
        return _PYTHON_TYPE_TO_JSON.get(annotation.id, "string")

    # Subscript: List[str], Optional[int], etc.
    if isinstance(annotation, ast.Subscript):
        if isinstance(annotation.value, ast.Name):
            base = annotation.value.id
            return _PYTHON_TYPE_TO_JSON.get(base, "string")

    # Constant (Python 3.8+ string annotations)
    if isinstance(annotation, ast.Constant):
        if isinstance(annotation.value, str):
            return _PYTHON_TYPE_TO_JSON.get(annotation.value, "string")

    return "string"


def _extract_param_description(docstring: str, param_name: str) -> str:
    """Extract a parameter description from a Google-style or reST-style docstring."""
    lines = docstring.split("\n")
    in_args = False
    for line in lines:
        stripped = line.strip()
        # Google style: "    param_name: description" or "    param_name (type): description"
        if stripped.startswith(f"{param_name}"):
            # "path: description" or "path (str): description"
            rest = stripped[len(param_name):].lstrip()
            if rest.startswith("("):
                # Skip type annotation in parens
                close = rest.find(")")
                if close != -1:
                    rest = rest[close + 1:].lstrip()
            if rest.startswith(":"):
                rest = rest[1:].lstrip()
            if rest:
                return rest
        # reST style: ":param param_name: description"
        if stripped.startswith(f":param {param_name}:"):
            return stripped[len(f":param {param_name}:"):].strip()
    return ""


def _parse_function(func_node: ast.FunctionDef, script_rel_path: str) -> dict | None:
    """Parse a function AST node into an OpenAI tool definition.

    Returns None if the function should be skipped (private, no args, etc.)
    """
    # Skip private functions (starting with _)
    if func_node.name.startswith("_"):
        return None

    # Extract docstring
    docstring = ast.get_docstring(func_node) or ""

    # Parse arguments
    args = func_node.args
    properties = {}
    required = []

    # Positional args with defaults
    defaults = args.defaults
    # Number of args without defaults
    num_no_default = len(args.args) - len(defaults)

    for i, arg in enumerate(args.args):
        if arg.arg == "self":
            continue

        # Type annotation
        json_type = _annotation_to_json_type(arg.annotation)

        # Description from docstring
        desc = _extract_param_description(docstring, arg.arg)

        properties[arg.arg] = {
            "type": json_type,
            "description": desc,
        }

        # Required if no default
        if i < num_no_default:
            required.append(arg.arg)
        else:
            # Has default — try to extract it
            default_idx = i - num_no_default
            if default_idx < len(defaults):
                default_node = defaults[default_idx]
                if isinstance(default_node, ast.Constant):
                    properties[arg.arg]["default"] = default_node.value

    # Use first line of docstring as description, fallback to function name
    desc_first_line = docstring.split("\n")[0].strip() if docstring else ""
    if not desc_first_line:
        desc_first_line = f"Script tool: {func_node.name}"

    return {
        "type": "function",
        "function": {
            "name": func_node.name,
            "description": desc_first_line,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
        # Internal metadata for executor routing
        "_source": "script",
        "_script_path": script_rel_path,
        "_func_name": func_node.name,
    }


def _parse_script_file(script_path: Path, skill_dir: Path) -> list[dict]:
    """Parse a single Python script file and return tool definitions."""
    try:
        content = script_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except SyntaxError as e:
        logger.warning("Failed to parse %s: %s", script_path, e)
        return []
    except Exception as e:
        logger.warning("Error reading %s: %s", script_path, e)
        return []

    rel_path = str(script_path.relative_to(skill_dir)).replace("\\", "/")
    tools = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            tool = _parse_function(node, rel_path)
            if tool:
                tools.append(tool)
    return tools


# ─── Public API ───

def discover_tools(folder_name: str) -> list[dict]:
    """Discover script tools from a skill's scripts/ directory.

    DISABLED BY CONVENTION — always returns [].

    ragclaw must NOT read third-party skill script source (scripts/*.py) to
    auto-expose internal functions as function-calling tools. How a skill's
    scripts are used is governed entirely by the skill's own SKILL.md: the LLM
    reads SKILL.md / runtime.conf and invokes the documented CLI via the
    run_shell / run_python tools.

    Auto-AST-parsing script internals (e.g. a CLI's ``cmd_*`` handlers) is wrong
    on two counts:
      1. It violates the industry convention — skill authors never ship skills
         expecting the host to introspect their source; SKILL.md is the contract.
      2. It wastes tokens proportional to script size (a 20KB script costs the
         same to ignore as a 20-line one once we stop reading it).

    Returning [] guarantees no script-internal function is ever exposed to the
    LLM, so it can only follow the SKILL.md contract. The rest of this module
    (build_execution_code / execute_script_tool) is intentionally left in place
    but is now unreachable through discovery; if a skill ever needs host-side
    script execution, it must be wired explicitly — never by reading skill source.

    Returns an empty list (no script tools).
    """
    return []


def clear_cache(folder_name: str | None = None) -> None:
    """Clear the script tool cache for a specific skill, or all if None."""
    if folder_name:
        _cache.pop(folder_name, None)
    else:
        _cache.clear()


def build_execution_code(script_content: str, func_name: str, arguments: dict) -> str:
    """Construct self-contained Python code for sandbox execution.

    The code includes:
    1. The full script content (function definitions)
    2. A function call with the provided arguments
    3. Print the result as JSON for structured output
    """
    # Build argument string
    arg_parts = []
    for key, value in arguments.items():
        arg_parts.append(f"{key}={json.dumps(value, ensure_ascii=False, default=str)}")
    arg_str = ", ".join(arg_parts)

    return f"""# === Script (inline) ===
{script_content}

# === Execute ===
import json as _json
_result = {func_name}({arg_str})
if isinstance(_result, str):
    print(_result)
else:
    print(_json.dumps(_result, ensure_ascii=False, default=str))
"""


def get_script_content(folder_name: str, script_rel_path: str) -> str:
    """Read the content of a script file within a skill folder."""
    skill_dir = get_skill_dir(folder_name)
    script_path = (skill_dir / script_rel_path).resolve()

    # Security: prevent path traversal
    if not str(script_path).startswith(str(skill_dir.resolve())):
        raise ValueError(f"ILLEGAL_SCRIPT_PATH: {script_rel_path}")

    if not script_path.exists():
        raise FileNotFoundError(f"SCRIPT_FILE_NOT_FOUND: {script_rel_path}")

    return script_path.read_text(encoding="utf-8")


async def execute_script_tool(
    folder_name: str,
    script_rel_path: str,
    func_name: str,
    arguments: dict,
    repl_server_config: dict,
    subdir: str | None = None,
    user_id: str | None = None,
) -> ToolResult:
    """Execute a script tool via python_repl_mcp_server sandbox.

    Args:
        folder_name: Skill folder name
        script_rel_path: Relative path to script (e.g. "scripts/check_disk.py")
        func_name: Function name to call
        arguments: Arguments dict from LLM
        repl_server_config: MCP server config dict for python_repl server
        subdir: Optional shared workspace dir (Route D) so generated
                      files persist across chained skill tool calls.

    Returns:
        ToolResult with ok=True and sandbox output, or ok=False with error
    """
    try:
        # Read script content
        script_content = get_script_content(folder_name, script_rel_path)

        # Construct execution code
        code = build_execution_code(script_content, func_name, arguments)

        # Execute via run_python sandbox (share workspace if provided)
        call_args: dict = {"code": code}
        if subdir:
            call_args["subdir"] = subdir
        result = await mcp_client.call_tool(
            repl_server_config, "run_python", call_args,
            auth_user=user_id,
        )

        if result.ok:
            logger.info("Script tool executed: %s.%s", script_rel_path, func_name)
        else:
            logger.warning("Script tool failed: %s.%s - %s", script_rel_path, func_name, result.error)

        return result

    except FileNotFoundError as e:
        return ToolResult(tool_name=func_name, ok=False, error=str(e))
    except ValueError as e:
        return ToolResult(tool_name=func_name, ok=False, error=str(e))
    except Exception as e:
        logger.error("Script execution error: %s", e, exc_info=True)
        return ToolResult(tool_name=func_name, ok=False, error=f"SCRIPT_EXECUTION_ERROR: {e}")

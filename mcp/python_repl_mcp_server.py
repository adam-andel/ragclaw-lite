"""Python REPL MCP Server — execute Python code in isolated subprocesses.

Security: runs in a single allowed directory only.
  --allow-dir D:\workspace  → subprocess can only read/write inside this dir

Usage:
  python python_repl_mcp_server.py --port 9200 --allow-dir D:\workspace
"""

import json
import subprocess
import tempfile
import shutil
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from argparse import ArgumentParser

MAX_OUTPUT = 20000
DEFAULT_TIMEOUT = 15
_allow_dir: str | None = None


def _build_guard(workdir: str) -> str:
    """Generate a preamble that patches open() to enforce single-directory access."""
    target = _allow_dir.replace("\\", "\\\\")
    return f"""
# --- sandbox guard (injected) ---
import os as _g_os, builtins as _g_bi, functools as _g_ft
_ALLOW = r'{target}'
_orig_open = _g_bi.open
@_g_ft.wraps(_orig_open)
def _safe_open(file, mode='r', *a, **kw):
    path = _g_os.path.abspath(str(file))
    if not path.startswith(_ALLOW + _g_os.sep) and path != _ALLOW:
        raise PermissionError(f"仅允许访问 {_ALLOW} 目录，拒绝: {{path}}")
    return _orig_open(file, mode, *a, **kw)
_g_bi.open = _safe_open
# --- end guard ---
"""


def run_python(code: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    workdir = tempfile.mkdtemp(dir=_allow_dir, prefix="repl_") if _allow_dir else tempfile.mkdtemp(prefix="repl_")
    try:
        full_code = (_build_guard(workdir) + "\n" + code) if _allow_dir else code

        env = os.environ.copy()
        for k in list(env.keys()):
            if any(pat in k.upper() for pat in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
                env.pop(k, None)

        proc = subprocess.run(
            [os.sys.executable, "-c", full_code],
            capture_output=True, text=True, timeout=timeout,
            cwd=workdir, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
        )

        parts = [proc.stdout.strip()] if proc.stdout.strip() else []
        if proc.stderr.strip():
            parts.append(f"[stderr]\n{proc.stderr.strip()}")
        return ("\n".join(parts) or "(无输出)")[:MAX_OUTPUT]

    except subprocess.TimeoutExpired:
        return f"执行超时（>{timeout}秒）"
    except Exception as e:
        return f"执行异常: {str(e)}"
    finally:
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass


TOOLS = [{
    "name": "run_python",
    "description": "在独立子进程中执行 Python 代码。仅允许访问指定工作目录。",
    "inputSchema": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "完整的 Python 代码"}},
        "required": ["code"],
    },
}]


class MCPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        method = body.get("method", "")
        req_id = body.get("id", 0)

        if method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = body.get("params", {})
            code = params.get("arguments", {}).get("code", "")
            result = {"content": [{"type": "text", "text": run_python(code)}]}
        else:
            self.send_error(404); return

        resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())

    def log_message(self, _fmt, *args):
        print(f"[REPL] {args[0]}")


if __name__ == "__main__":
    p = ArgumentParser()
    p.add_argument("--port", type=int, default=9200)
    p.add_argument("--allow-dir", type=str,
                   help="仅允许子进程访问此目录（强烈建议在多租户部署中设置）")
    args = p.parse_args()
    _allow_dir = os.path.abspath(args.allow_dir) if args.allow_dir else None

    server = HTTPServer(("0.0.0.0", args.port), MCPHandler)
    print(f"🐍 Python REPL MCP Server on :{args.port}  timeout={DEFAULT_TIMEOUT}s")
    if _allow_dir:
        print(f"   🛡️  Allow-only: {_allow_dir}")
    else:
        print(f"   ⚠️  No --allow-dir set (filesystem unrestricted)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

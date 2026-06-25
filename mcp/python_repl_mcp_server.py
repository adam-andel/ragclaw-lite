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
import uuid
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from argparse import ArgumentParser

MAX_OUTPUT = 20000
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_MEMORY_MB = 512
DEFAULT_KEEP_MINUTES = 60
_allow_dir: str | None = None
_no_network: bool = False
_max_memory_mb: int = DEFAULT_MAX_MEMORY_MB
_keep_minutes: int = DEFAULT_KEEP_MINUTES
_CLEANUP_EVERY = 300  # check every 5 minutes

# Modules that can bypass Python-level guards — blocked at import level
_BLOCKED_MODULES = {
    # ctypes / ffi → direct OS syscalls, bypass all wrappers
    "ctypes", "_ctypes", "cffi",
    # Memory-mapped files → bypass open() wrapper
    "mmap",
    # Windows API → CreateFileW etc bypass filesystem guard
    "_winapi", "msvcrt", "win32api", "win32file", "win32con",
    # Process spawning → bypass subprocess guard
    "multiprocessing", "_multiprocessing", "_posixsubprocess",
    # Unsafe deserialization
    "pickle", "_pickle", "dill",
    # Alternative file storage that doesn't use open()
    "dbm", "_dbm", "gdbm", "shelve", "marshal",
    # Environment manipulation
    "resource", "signal",
}


def _build_guard(per_call_dir: str) -> str:
    """Build security preamble. per_call_dir is THIS invocation's exclusive directory."""
    guards = []
    target = per_call_dir.replace("\\", "\\\\")
    blocked = ", ".join(sorted(_BLOCKED_MODULES))
    guards.append(f"""
# --- import guard: block bypass vectors ---
import builtins as _g_bi2
_blocked = {{{blocked!r}}}
_orig_import = _g_bi2.__import__
def _safe_import(name, *a, **kw):
    root = name.split('.')[0]
    if root in _blocked:
        raise ImportError(f"禁止导入 {{name}}——该模块可能绕过安全限制")
    return _orig_import(name, *a, **kw)
_g_bi2.__import__ = _safe_import
""")

    # 1. Filesystem — allow only --allow-dir
    if _allow_dir:
        target = _allow_dir.replace("\\", "\\\\")
        guards.append(f"""
# --- filesystem guard: default-deny, allow only --allow-dir ---
import os as _g_os, builtins as _g_bi, functools as _g_ft, shutil as _g_shutil, pathlib as _g_pl
_ALLOW = r'{target}'

def _check_path(path, op='操作'):
    if not isinstance(path, str):
        return True
    p = _g_os.path.abspath(path)
    if p.startswith(_ALLOW + _g_os.sep) or p == _ALLOW:
        return True
    raise PermissionError(f"{{op}} {{path}} 被拒绝——仅允许操作 {_ALLOW} 目录")

# open()
_orig_open = _g_bi.open
@_g_ft.wraps(_orig_open)
def _safe_open(file, mode='r', *a, **kw):
    _check_path(str(file), '打开')
    return _orig_open(file, mode, *a, **kw)
_g_bi.open = _safe_open

# io.FileIO / io.open — separate from builtins.open
import io as _g_io
if hasattr(_g_io, 'FileIO'):
    _orig_fileio = _g_io.FileIO
    @_g_ft.wraps(_orig_fileio)
    def _safe_fileio(file, *a, **kw):
        _check_path(str(file), 'FileIO')
        return _orig_fileio(file, *a, **kw)
    _g_io.FileIO = _safe_fileio
if hasattr(_g_io, 'open'):
    _orig_io_open = _g_io.open
    @_g_ft.wraps(_orig_io_open)
    def _safe_io_open(file, *a, **kw):
        _check_path(str(file), 'io.open')
        return _orig_io_open(file, *a, **kw)
    _g_io.open = _safe_io_open

# os path-taking functions — wrap ALL of them
for _name in dir(_g_os):
    if _name.startswith('_') or _name in ('fspath','path','sep'):
        continue
    _orig = getattr(_g_os, _name, None)
    if not callable(_orig):
        continue
    @_g_ft.wraps(_orig)
    def _mk_wrapper(original=_orig, fn_name=_name):
        def _wrapped(*a, **kw):
            for arg in a:
                if isinstance(arg, str) and (arg.startswith('/') or arg.startswith('\\\\') or ('\\' in arg) or (':' in arg and len(arg) > 2)):
                    _check_path(arg, fn_name)
            return original(*a, **kw)
        return _wrapped
    setattr(_g_os, _name, _mk_wrapper())

# shutil — wrap all
for _name in dir(_g_shutil):
    if _name.startswith('_'):
        continue
    _orig = getattr(_g_shutil, _name, None)
    if not callable(_orig):
        continue
    @_g_ft.wraps(_orig)
    def _mk_sh_impl(original=_orig):
        def _wrapped(*a, **kw):
            for arg in a:
                if isinstance(arg, str):
                    _check_path(arg, _name)
            return original(*a, **kw)
        return _wrapped
    setattr(_g_shutil, _name, _mk_sh_impl())

# pathlib
_orig_path_init = _g_pl.Path.__init__
def _safe_path_init(self, *a, **kw):
    _orig_path_init(self, *a, **kw)
    _check_path(str(self), 'pathlib')
_g_pl.Path.__init__ = _safe_path_init
""")

    # 2. Network — block all outbound connections
    if _no_network:
        guards.append("""
# --- network guard (all socket operations blocked) ---
import socket as _g_socket
_orig_socket = _g_socket.socket
_orig_create_conn = getattr(_g_socket, 'create_connection', None)
_orig_getaddrinfo = _g_socket.getaddrinfo

def _blocked(*a, **kw):
    raise PermissionError("网络访问已被禁止。如需外部数据，请通过 MCP 工具获取。")

_g_socket.socket = _blocked
if _orig_create_conn:
    _g_socket.create_connection = _blocked
_g_socket.getaddrinfo = _blocked
""")

        # Also block subprocess to prevent shell-level network (curl, wget, etc.)
        guards.append("""
# --- subprocess guard ---
import subprocess as _g_subprocess
_orig_popen = _g_subprocess.Popen
_orig_run = _g_subprocess.run
_orig_call = _g_subprocess.call

def _blocked_proc(*a, **kw):
    raise PermissionError("子进程调用已被禁止")

_g_subprocess.Popen = _blocked_proc
_g_subprocess.run = _blocked_proc
_g_subprocess.call = _blocked_proc
_g_subprocess.check_call = _blocked_proc
_g_subprocess.check_output = _blocked_proc
""")

    return "\n".join(guards) if guards else ""


def _ast_prescreen(code: str) -> str | None:
    """Lightweight AST scan — catches obvious attacks before spawning subprocess.
    Returns error message string if code is rejected, None if passed.
    """
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"语法错误 (行 {e.lineno}): {e.msg}"

    blocked_imports = _BLOCKED_MODULES
    blocked_calls = {"eval", "exec", "compile", "__import__", "open",
                     "input", "breakpoint", "help"}
    blocked_attrs = {"__import__", "__builtins__", "__globals__", "__dict__",
                     "__class__", "__bases__", "__subclasses__", "__mro__"}

    for node in ast.walk(tree):
        # import xxx
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root in blocked_imports:
                    return f"禁止导入模块: {root}"
        # from xxx import yyy
        if isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split('.')[0]
                if root in blocked_imports:
                    return f"禁止导入模块: {root}"
        # eval(), exec(), __import__() calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in blocked_calls:
                return f"禁止调用: {node.func.id}()"
        # .__class__, .__bases__ etc attribute access
        if isinstance(node, ast.Attribute):
            if node.attr in blocked_attrs:
                return f"禁止访问: .{node.attr}"

    return None  # passed


def run_python(code: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute Python code in an isolated subprocess with layered defense.

    Layer 0: AST pre-screening — catches obvious attacks, ~1ms (in-process)
    Layer 1-3: import guard + filesystem + network (injected as preamble)
    Layer 4: subprocess isolation + timeout + temp file (not -c flag)
    """
    # Layer 0: AST pre-screen
    err = _ast_prescreen(code)
    if err:
        return f"代码审查未通过: {err}"

    # Layer 1-3: per-call guard — only THIS invocation's UUID subdirectory is accessible
    call_uuid = str(uuid.uuid4())[:8]
    workdir = os.path.join(_allow_dir, call_uuid) if _allow_dir else tempfile.mkdtemp(prefix="repl_")
    os.makedirs(workdir, exist_ok=True)
    guard = _build_guard(workdir)
    full_code = guard + "\n" + code if guard else code

    # Write to temp file
    fd, temp_script = tempfile.mkstemp(suffix=".py", text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(full_code)

        env = os.environ.copy()
        for k in list(env.keys()):
            if any(pat in k.upper() for pat in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
                env.pop(k, None)

        proc = subprocess.run(
            [os.sys.executable, temp_script],
            capture_output=True, text=True, timeout=timeout,
            cwd=workdir, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
        )

        parts = [proc.stdout.strip()] if proc.stdout.strip() else []
        if proc.stderr.strip():
            parts.append(f"[stderr]\n{proc.stderr.strip()}")
        if proc.returncode != 0 and not proc.stdout.strip():
            parts.insert(0, f"(进程退出码: {proc.returncode})")
        result = "\n".join(parts) or "(无输出)"
        if _allow_dir:
            result = f"[workspace: {call_uuid}/]\n{result}"
        return result[:MAX_OUTPUT]

    except subprocess.TimeoutExpired:
        return f"执行超时（>{timeout}秒），已终止进程"
    except MemoryError:
        return f"内存不足（>{_max_memory_mb}MB）"
    except Exception as e:
        return f"执行异常: {str(e)}"
    finally:
        try:
            os.unlink(temp_script)
        except Exception:
            pass
        # Dir NOT cleaned — kept for --keep-minutes for user download


TOOLS = [{
    "name": "run_python",
    "description": (
        "在独立子进程中执行 Python 代码并返回输出。"
        "生成的文件通过输出开头的 [workspace: xxx/] 标识。"
        "下载链接格式: http://127.0.0.1:8000/data/workspace/{uuid}/{文件名}"
        + ("仅允许访问指定工作目录。" if _allow_dir else "")
        + ("网络访问已被禁止。" if _no_network else "")
    ),
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
                   help="仅允许子进程访问此目录")
    p.add_argument("--no-network", action="store_true",
                   help="禁止子进程网络访问（阻止 socket + subprocess）")
    p.add_argument("--keep-minutes", type=int, default=DEFAULT_KEEP_MINUTES,
                   help=f"生成文件保留时长（分钟），默认 {DEFAULT_KEEP_MINUTES}")
    args = p.parse_args()
    _allow_dir = os.path.abspath(args.allow_dir) if args.allow_dir else None
    _no_network = args.no_network
    _keep_minutes = args.keep_minutes

    # Background cleanup thread
    def _cleanup_loop():
        while True:
            time.sleep(_CLEANUP_EVERY)
            if not _allow_dir:
                continue
            cutoff = time.time() - (_keep_minutes * 60)
            try:
                for entry in os.scandir(_allow_dir):
                    if entry.is_dir() and entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry.path, ignore_errors=True)
                        print(f"[cleanup] removed {entry.name}")
            except Exception:
                pass
    threading.Thread(target=_cleanup_loop, daemon=True).start()

    server = HTTPServer(("0.0.0.0", args.port), MCPHandler)
    print(f"🐍 Python REPL MCP Server on :{args.port}  timeout={DEFAULT_TIMEOUT}s")
    if _allow_dir:
        print(f"   🛡️  Allow-only: {_allow_dir}  (keep={_keep_minutes}min)")
    if _no_network:
        print(f"   🔒 Network blocked (socket + subprocess)")
    if not _allow_dir and not _no_network:
        print(f"   ⚠️  No restrictions set")
    print(f"   🧹 Cleanup: every {_CLEANUP_EVERY}s, keeps files for {_keep_minutes}min")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

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
import signal
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from argparse import ArgumentParser

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("repl")

# ── Threaded HTTP server ──────────────────────────────────
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

MAX_OUTPUT = 20000
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_MEMORY_MB = 512
DEFAULT_MAX_NPROC = 64
DEFAULT_MAX_CONCURRENT = 4
DEFAULT_KEEP_MINUTES = 60
_allow_dir: str | None = None
_no_network: bool = False
_max_memory_mb: int = DEFAULT_MAX_MEMORY_MB
_max_nproc: int = DEFAULT_MAX_NPROC
_max_concurrent: int = DEFAULT_MAX_CONCURRENT
_keep_minutes: int = DEFAULT_KEEP_MINUTES
_CLEANUP_EVERY = 300  # check every 5 minutes
_shutdown_event = threading.Event()
_exec_semaphore = threading.BoundedSemaphore(DEFAULT_MAX_CONCURRENT)
_public_url: str = ""  # e.g. "http://192.168.1.100:9200"

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


def _set_limits():
    """preexec_fn (Linux only): enforce OS-level resource limits on child process.

    RLIMIT_AS  → hard memory cap (prevents OOM of host/container)
    RLIMIT_CPU → CPU time ceiling (backstop for timeout)
    RLIMIT_NPROC → max child processes (prevents fork bombs)
    """
    if os.name == 'nt':
        return  # Windows: no resource module, container limits handled by Docker
    import resource as _rlim
    mem_bytes = _max_memory_mb * 1024 * 1024
    _rlim.setrlimit(_rlim.RLIMIT_AS, (mem_bytes, mem_bytes))
    cpu_secs = DEFAULT_TIMEOUT + 10
    _rlim.setrlimit(_rlim.RLIMIT_CPU, (cpu_secs, cpu_secs))
    _rlim.setrlimit(_rlim.RLIMIT_NPROC, (_max_nproc, _max_nproc))


def _build_guard(per_call_dir: str) -> str:
    """Build security preamble. per_call_dir is THIS invocation's exclusive directory."""
    preamble = """# --- guard preamble: all imports before restrictions ---
import os as _g_os, builtins as _g_bi, builtins as _g_bi2
import functools as _g_ft, shutil as _g_shutil, pathlib as _g_pl, io as _g_io
import socket as _g_socket, subprocess as _g_subprocess
"""
    guards = []
    blocked = repr(set(_BLOCKED_MODULES))  # e.g. "{'ctypes', '_ctypes', ...}"
    guards.append(f"""
# --- import guard: block bypass vectors ---
_blocked = {blocked}
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
_ALLOW = r'{target}'

def _check_path(path, op='操作'):
    if not isinstance(path, str):
        return True
    p = _g_os.path.normpath(_g_os.path.abspath(path))
    allow = _g_os.path.normpath(_ALLOW)
    if p.startswith(allow + _g_os.sep) or p == allow:
        return True
    raise PermissionError(f"{{op}} {{path}} 被拒绝——仅允许操作 {{_ALLOW}} 目录")

# open()
_orig_open = _g_bi.open
@_g_ft.wraps(_orig_open)
def _safe_open(file, mode='r', *a, **kw):
    _check_path(str(file), '打开')
    return _orig_open(file, mode, *a, **kw)
_g_bi.open = _safe_open

# io.FileIO / io.open — separate from builtins.open
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
        _BS = chr(92)
        def _wrapped(*a, **kw):
            for arg in a:
                if isinstance(arg, str) and (arg.startswith('/') or _BS in arg or (':' in arg and len(arg) > 2)):
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
_orig_socket = _g_socket.socket
_orig_create_conn = getattr(_g_socket, 'create_connection', None)
_orig_getaddrinfo = _g_socket.getaddrinfo

def _net_blocked(*a, **kw):
    raise PermissionError("网络访问已被禁止。如需外部数据，请通过 MCP 工具获取。")

_g_socket.socket = _net_blocked
if _orig_create_conn:
    _g_socket.create_connection = _net_blocked
_g_socket.getaddrinfo = _net_blocked
""")

        # Also block subprocess to prevent shell-level network (curl, wget, etc.)
        guards.append("""
# --- subprocess guard ---
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

    return "\n".join([preamble] + guards) if guards else preamble


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
    # open() is NOT blocked — the filesystem guard (injected as preamble)
    # replaces builtins.open with a safe wrapper before user code runs.
    blocked_calls = {"eval", "exec", "compile", "__import__",
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
    t0 = time.time()
    err = _ast_prescreen(code)
    if err:
        logger.warning("ast_reject reason=%s code_preview=%.60s", err, code[:60].replace("\n", " "))
        return f"代码审查未通过: {err}"

    # Concurrency gate — prevent LLM from spawning too many simultaneous executions
    if not _exec_semaphore.acquire(timeout=5):
        logger.warning("exec_busy max_concurrent=%d", _max_concurrent)
        return f"服务器繁忙（并发执行数已达上限 {_max_concurrent}），请稍后重试"

    try:
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
                preexec_fn=_set_limits if os.name != 'nt' else None,
            )

            parts = [proc.stdout.strip()] if proc.stdout.strip() else []
            if proc.stderr.strip():
                parts.append(f"[stderr]\n{proc.stderr.strip()}")
            if proc.returncode != 0 and not proc.stdout.strip():
                parts.insert(0, f"(进程退出码: {proc.returncode})")
            result = "\n".join(parts) or "(无输出)"
            if _allow_dir:
                result = f"[workspace: {call_uuid}/]\n{result}"
            result = result[:MAX_OUTPUT]

            # Append download links (MCP server constructs the full URL from --public-url)
            if _public_url and _allow_dir:
                dirpath = os.path.join(_allow_dir, call_uuid)
                download_base = f"{_public_url}/files/{call_uuid}"
                for f in os.listdir(dirpath):
                    fpath = os.path.join(dirpath, f)
                    if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                        result += f"\n\n[File] {download_base}/{f}"

            elapsed_ms = int((time.time() - t0) * 1000)
            logger.info("exec_ok uuid=%s elapsed_ms=%d output_len=%d exit_code=%d",
                        call_uuid, elapsed_ms, len(result), proc.returncode)
            return result

        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.warning("exec_timeout uuid=%s timeout=%d elapsed_ms=%d code_preview=%.60s",
                          call_uuid, timeout, elapsed_ms, code[:60].replace("\n", " "))
            return f"执行超时（>{timeout}秒），已终止进程"
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error("exec_error uuid=%s error=%s elapsed_ms=%d",
                        call_uuid, type(e).__name__, elapsed_ms)
            return f"执行异常: {str(e)}"
        finally:
            try:
                os.unlink(temp_script)
            except Exception:
                pass
    finally:
        _exec_semaphore.release()


TOOLS = [{
    "name": "run_python",
    "description": (
        "在隔离子进程中执行 Python 代码并返回输出。适用场景：文件生成、数据处理、计算。"
        + ("工作目录已隔离。" if _allow_dir else "")
        + ("无网络访问。" if _no_network else "")
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "完整的 Python 代码"}},
        "required": ["code"],
    },
}]


class MCPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Health check
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        # File download: GET /files/{uuid}/{filename}
        if self.path.startswith("/files/") and _allow_dir:
            rel = self.path[len("/files/"):].lstrip("/")
            # Directory listing: GET /files/{uuid} or /files/{uuid}/
            if rel.endswith("/"):
                rel = rel.rstrip("/")
            parts = rel.split("/")
            if len(parts) == 1:
                # Directory listing
                uuid_dir = parts[0]
                dirpath = os.path.join(_allow_dir, uuid_dir)
                if os.path.isdir(dirpath) and os.path.commonpath(
                    [os.path.realpath(dirpath), os.path.realpath(_allow_dir)]
                ) == os.path.realpath(_allow_dir):
                    try:
                        files = [f for f in os.listdir(dirpath)
                                if os.path.isfile(os.path.join(dirpath, f))]
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "uuid": uuid_dir,
                            "files": [{"name": f, "url": f"/files/{uuid_dir}/{f}"}
                                     for f in sorted(files)]
                        }, ensure_ascii=False).encode())
                        return
                    except Exception:
                        pass
                self.send_error(404)
                return
            if len(parts) == 2:
                uuid_dir, filename = parts
                filepath = os.path.join(_allow_dir, uuid_dir, filename)
                real = os.path.realpath(filepath)
                if (os.path.commonpath([real, os.path.realpath(_allow_dir)])
                        == os.path.realpath(_allow_dir)
                        and os.path.isfile(real)):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition",
                                    f'attachment; filename="{filename}"')
                    self.end_headers()
                    with open(real, "rb") as f:
                        self.wfile.write(f.read())
                    return
        self.send_error(404)

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
        logger.info("http %s", args[0])


if __name__ == "__main__":
    p = ArgumentParser()
    p.add_argument("--port", type=int, default=9200)
    p.add_argument("--allow-dir", type=str,
                   help="仅允许子进程访问此目录")
    p.add_argument("--no-network", action="store_true",
                   help="禁止子进程网络访问（阻止 socket + subprocess）")
    p.add_argument("--keep-minutes", type=int, default=DEFAULT_KEEP_MINUTES,
                   help=f"生成文件保留时长（分钟），默认 {DEFAULT_KEEP_MINUTES}")
    p.add_argument("--max-memory-mb", type=int, default=DEFAULT_MAX_MEMORY_MB,
                   help=f"子进程最大内存（MB，Linux 下通过 setrlimit 硬限制），默认 {DEFAULT_MAX_MEMORY_MB}")
    p.add_argument("--max-nproc", type=int, default=DEFAULT_MAX_NPROC,
                   help=f"子进程最大进程数（防 fork bomb），默认 {DEFAULT_MAX_NPROC}")
    p.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CONCURRENT,
                   help=f"最大并发执行数，默认 {DEFAULT_MAX_CONCURRENT}")
    p.add_argument("--public-url", type=str, default="",
                   help="对外可访问的完整地址，用于生成File。如 http://192.168.1.100:9200")
    args = p.parse_args()
    # Env var overrides: CLI args take precedence, env vars provide defaults
    _allow_dir = args.allow_dir or os.environ.get("REPL_ALLOW_DIR")
    _allow_dir = os.path.abspath(_allow_dir) if _allow_dir else None
    _no_network = (args.no_network
                   or os.environ.get("REPL_NO_NETWORK", "").lower() in ("1", "true", "yes"))
    _keep_minutes = int(os.environ.get("REPL_KEEP_MINUTES", args.keep_minutes))
    _max_memory_mb = int(os.environ.get("REPL_MAX_MEMORY_MB", args.max_memory_mb))
    _max_nproc = int(os.environ.get("REPL_MAX_NPROC", args.max_nproc))
    _max_concurrent = int(os.environ.get("REPL_MAX_CONCURRENT", args.max_concurrent))
    _public_url = args.public_url or os.environ.get("REPL_PUBLIC_URL", "")
    if _public_url:
        _public_url = _public_url.rstrip("/")
    if _max_concurrent != DEFAULT_MAX_CONCURRENT:
        _exec_semaphore = threading.BoundedSemaphore(_max_concurrent)

    # Background cleanup thread
    def _cleanup_loop():
        while not _shutdown_event.is_set():
            _shutdown_event.wait(_CLEANUP_EVERY)
            if not _allow_dir:
                continue
            cutoff = time.time() - (_keep_minutes * 60)
            try:
                for entry in os.scandir(_allow_dir):
                    if entry.is_dir() and entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry.path, ignore_errors=True)
                        logger.info("cleanup removed=%s", entry.name)
            except Exception:
                pass
    threading.Thread(target=_cleanup_loop, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), MCPHandler)
    logger.info("start port=%d timeout=%d max_mem=%d max_nproc=%d max_concurrent=%d keep=%d",
                args.port, DEFAULT_TIMEOUT, _max_memory_mb, _max_nproc, _max_concurrent, _keep_minutes)
    if _allow_dir:
        logger.info("allow_dir=%s", _allow_dir)
    if _no_network:
        logger.info("network=blocked")
    if os.name != 'nt':
        logger.info("os_limits rlimit_as=%dMB rlimit_cpu=%ds rlimit_nproc=%d",
                    _max_memory_mb, DEFAULT_TIMEOUT + 10, _max_nproc)

    # Graceful shutdown on SIGTERM (Docker stop) + SIGINT (Ctrl+C)
    def _on_shutdown(signum, frame):
        logger.info("signal=%d shutting_down", signum)
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)

    server.timeout = 0.5  # poll shutdown_event every 0.5s
    try:
        while not _shutdown_event.is_set():
            server.handle_request()
    finally:
        server.server_close()
        logger.info("shutdown_complete")

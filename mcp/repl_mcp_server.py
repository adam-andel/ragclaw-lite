"""REPL MCP Server — multi-language sandbox execution for LLM-generated code.

Supports: Python, Shell (/bin/sh), JavaScript (Node.js)
Security: each language isolated via subprocess + OS limits + Docker seccomp.

Usage:
  python repl_mcp_server.py --port 9200 --allow-dir /app/workspace --no-network
  python repl_mcp_server.py --port 9200 --enable-shell --enable-javascript
"""

from __future__ import annotations

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
import re
import ast as _ast_module
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from argparse import ArgumentParser

# ═══════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("repl")

# ═══════════════════════════════════════════════════════════
# Threaded HTTP server
# ═══════════════════════════════════════════════════════════
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

# ═══════════════════════════════════════════════════════════
# Configuration / globals
# ═══════════════════════════════════════════════════════════
MAX_OUTPUT = 20000
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_MEMORY_MB = 512
DEFAULT_MAX_NPROC = 64
DEFAULT_MAX_CONCURRENT = 4
DEFAULT_KEEP_MINUTES = 60
_CLEANUP_EVERY = 300

_allow_dir: str | None = None
_no_network: bool = False
_max_memory_mb: int = DEFAULT_MAX_MEMORY_MB
_max_nproc: int = DEFAULT_MAX_NPROC
_max_concurrent: int = DEFAULT_MAX_CONCURRENT
_keep_minutes: int = DEFAULT_KEEP_MINUTES
_public_url: str = ""
_enable_shell: bool = False
_enable_shell_local: bool = False
_enable_javascript: bool = False
_shutdown_event = threading.Event()
_exec_semaphore = threading.BoundedSemaphore(DEFAULT_MAX_CONCURRENT)

# ═══════════════════════════════════════════════════════════
# OS resource limits (shared across languages)
# ═══════════════════════════════════════════════════════════
def _set_limits():
    """preexec_fn (Linux): RLIMIT_AS / RLIMIT_CPU / RLIMIT_NPROC on child process."""
    if os.name == 'nt':
        return
    import resource as _rlim
    mem_bytes = _max_memory_mb * 1024 * 1024
    _rlim.setrlimit(_rlim.RLIMIT_AS, (mem_bytes, mem_bytes))
    cpu_secs = DEFAULT_TIMEOUT + 10
    _rlim.setrlimit(_rlim.RLIMIT_CPU, (cpu_secs, cpu_secs))
    _rlim.setrlimit(_rlim.RLIMIT_NPROC, (_max_nproc, _max_nproc))


# ═══════════════════════════════════════════════════════════
# Shared execution helpers
# ═══════════════════════════════════════════════════════════
def _sanitize_env() -> dict:
    """Copy environ, strip credential-like vars."""
    env = os.environ.copy()
    for k in list(env.keys()):
        if any(pat in k.upper() for pat in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
            env.pop(k, None)
    return env


def _acquire_slot(timeout: int = 5) -> bool:
    """Try to acquire a concurrency slot. Returns False if busy."""
    return _exec_semaphore.acquire(timeout=timeout)


def _make_workdir() -> str:
    """Create per-call UUID workdir inside --allow-dir (or tempdir if not set)."""
    call_uuid = str(uuid.uuid4())[:8]
    if _allow_dir:
        workdir = os.path.join(_allow_dir, call_uuid)
        os.makedirs(workdir, exist_ok=True)
        return workdir
    return tempfile.mkdtemp(prefix="repl_")


def _subprocess_flags() -> int:
    """Cross-platform process-creation flags."""
    return subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0


# ═══════════════════════════════════════════════════════════
# Python executor — Docker-simplified guard
# ═══════════════════════════════════════════════════════════
# Docker provides hardware-level isolation:
#   read_only rootfs, tmpfs workspace, cap_drop ALL, seccomp,
#   no-new-privileges, non-root user, resource limits.
# Python-level guards only handle network blocking (--no-network).
# All import/filesystem/subprocess guards removed — they caused
# compatibility issues with pptx, PIL, ssl, lxml, etc.

def _py_build_guard(per_call_dir: str) -> str:
    """Build security preamble. Only network guard if --no-network is set."""
    preamble = """# --- guard preamble (Docker-simplified) ---
import socket as _g_socket
"""
    guards = []

    # Network guard — only block outbound connection functions, NOT the socket
    # class itself. This allows ssl.py, urllib, etc. to import correctly.
    if _no_network:
        guards.append("""
# --- network guard: block outbound connections only ---
_orig_create_conn = getattr(_g_socket, 'create_connection', None)
_orig_getaddrinfo = _g_socket.getaddrinfo

def _net_blocked(*a, **kw):
    raise PermissionError("网络访问已被禁止。如需外部数据，请通过 MCP 工具获取。")

if _orig_create_conn:
    _g_socket.create_connection = _net_blocked
_g_socket.getaddrinfo = _net_blocked
""")

    return "\n".join([preamble] + guards) if guards else preamble


def _py_ast_prescreen(code: str) -> str | None:
    """Lightweight AST scan — syntax check only. Docker handles security."""
    try:
        _ast_module.parse(code)
    except SyntaxError as e:
        return f"语法错误 (行 {e.lineno}): {e.msg}"
    return None


def _run_python(code: str, workdir: str, timeout: int) -> str:
    """Execute Python code in isolated subprocess (preserved from original)."""
    guard = _py_build_guard(workdir)
    full_code = guard + "\n" + code if guard else code

    fd, temp_script = tempfile.mkstemp(suffix=".py", text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(full_code)

        env = _sanitize_env()
        proc = subprocess.run(
            [os.sys.executable, temp_script],
            capture_output=True, text=True, timeout=timeout,
            cwd=workdir, env=env,
            creationflags=_subprocess_flags(),
            preexec_fn=_set_limits if os.name != 'nt' else None,
        )

        parts = [proc.stdout.strip()] if proc.stdout.strip() else []
        if proc.stderr.strip():
            parts.append(f"[stderr]\n{proc.stderr.strip()}")
        if proc.returncode != 0 and not proc.stdout.strip():
            parts.insert(0, f"(进程退出码: {proc.returncode})")
        return "\n".join(parts) or "(无输出)"
    finally:
        try:
            os.unlink(temp_script)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# Shell executor
# ═══════════════════════════════════════════════════════════

# Patterns that trigger rejection (regex applied to shell command string)
_SHELL_DANGEROUS: list[tuple[str, str]] = [
    # ── File system destruction ──
    (r'rm\s.*-r\S*f?\s*/',               'rm -rf on root'),
    (r'>\s*/dev/(sd|hd|nvme|xvd|vd|mmc)', 'redirect to block device'),
    (r'dd\s+.*if=.*of=/dev/',             'dd to block device'),
    (r'\bmkfs\.',                          'mkfs (format)'),
    (r'\bmount\s',                         'mount'),
    (r'\bumount\s',                        'umount'),
    (r'\bchmod\s+.*[67]77\s+/',           'chmod 777 on root'),
    (r'\bchown\s+\S+\s+/',                'chown on root'),
    (r'\blosetup\b',                       'losetup (loop device)'),

    # ── Network tools (always dangerous in sandbox) ──
    (r'\bcurl\b',                          'curl'),
    (r'\bwget\b',                          'wget'),
    (r'\bnc\b',                            'netcat'),
    (r'\bncat\b',                          'ncat'),
    (r'\bssh\b',                           'ssh'),
    (r'\bscp\b',                           'scp'),
    (r'\bftp\b',                           'ftp'),
    (r'\btelnet\b',                        'telnet'),
    (r'\bnmap\b',                          'nmap'),
    (r'\bsocat\b',                         'socat'),

    # ── Process / kernel manipulation ──
    (r'kill\s+-9\s+1\b',                  'kill PID 1'),
    (r'\breboot\b',                        'reboot'),
    (r'\bshutdown\b',                      'shutdown'),
    (r'\bmodprobe\b',                      'modprobe'),
    (r'\binsmod\b',                        'insmod'),
    (r'\brmmod\b',                         'rmmod'),
    (r'\bsysctl\b',                        'sysctl'),

    # ── System config tampering ──
    (r'>\s*/etc/',                         'redirect to /etc'),
    (r'\bpasswd\b',                        'password utility'),
    (r'\bsu\s',                            'su command'),
    (r'\bsudo\b',                          'sudo'),
    (r'\biptables\b',                      'iptables'),
    (r'\bcrontab\b',                       'crontab'),
    (r'\bsystemctl\b',                     'systemctl'),
    (r'\bservice\s',                       'service manager'),
    (r'\bdocker\b',                        'docker'),
]


def _sh_prescreen(code: str) -> str | None:
    """Scan shell command for dangerous patterns. Returns error or None."""
    for pattern, reason in _SHELL_DANGEROUS:
        if re.search(pattern, code, re.IGNORECASE):
            return f"禁止执行 Shell 命令（匹配危险模式: {reason}）"
    return None


def _run_shell(code: str, workdir: str, timeout: int) -> str:
    """Execute shell command in isolated subprocess."""
    # Force working directory first
    if os.name == 'nt':
        shell_cmd = ["cmd.exe", "/c", code]
    else:
        # cd to workdir, then execute; use set -e for early failure
        shell_cmd = ["/bin/sh", "-c", f"cd '{workdir}' && {code}"]

    env = _sanitize_env()
    try:
        proc = subprocess.run(
            shell_cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=workdir, env=env,
            creationflags=_subprocess_flags(),
            preexec_fn=_set_limits if os.name != 'nt' else None,
        )

        parts = [proc.stdout.strip()] if proc.stdout.strip() else []
        if proc.stderr.strip():
            parts.append(f"[stderr]\n{proc.stderr.strip()}")
        if proc.returncode != 0:
            parts.insert(0, f"(进程退出码: {proc.returncode})")
        return "\n".join(parts) or "(无输出)"
    except subprocess.TimeoutExpired:
        return f"执行超时（>{timeout}秒），已终止进程"


# ═══════════════════════════════════════════════════════════
# JavaScript executor
# ═══════════════════════════════════════════════════════════

_JS_GUARD_PREAMBLE = r"""
// ── REPL JS Guard Preamble ──
// Monkey-patches dangerous Node.js APIs before user code runs.
(function() {
  var _allowDir = process.env.REPL_ALLOW_DIR;
  var _allowDirNorm = _allowDir ? require('path').resolve(_allowDir) : null;

  // ── Block dangerous modules at require level ──
  var blockedModules = [
    'child_process', 'cluster', 'worker_threads',
    'net', 'dgram', 'http', 'https', 'http2', 'tls', 'dns',
    'vm', 'inspector', 'repl', 'v8'
  ];
  var Module = require('module');
  var origLoad = Module._load;
  Module._load = function(request, parent, isMain) {
    var root = request.split('/')[0];
    if (blockedModules.indexOf(root) !== -1) {
      throw new Error("require('" + request + "') is blocked for security");
    }
    // fs, path, os are allowed — filesystem is restricted by Docker layer
    return origLoad.apply(this, arguments);
  };

  // ── Block eval / new Function ──
  var origEval = global.eval;
  global.eval = function(code) {
    throw new Error('eval() is blocked for security');
  };
  // Wrap Function constructor
  var OrigFunction = Function;
  var F = function() {
    throw new Error('new Function() is blocked for security');
  };
  F.prototype = OrigFunction.prototype;
  try { Object.defineProperty(global, 'Function', { value: F, writable: false, configurable: false }); } catch(e) {}
  try { Object.defineProperty(globalThis, 'Function', { value: F, writable: false, configurable: false }); } catch(e) {}

  // ── Block process.exit / process.kill / process.abort ──
  var origExit = process.exit;
  process.exit = function(code) {
    console.error('[REPL] process.exit(' + code + ') blocked');
    // Allow exit(0) to be treated as normal completion
    if (code === 0) return;
    throw new Error('process.exit(' + code + ') blocked');
  };
  process.kill = function(pid, signal) {
    if (pid === process.pid && signal === 0) return true;
    throw new Error('process.kill blocked');
  };
  process.abort = function() {
    throw new Error('process.abort blocked');
  };

  // ── Block global setTimeout/setInterval from spawning infinite loops ──
  // (allow them, but warn if extremely large values)
})();

// ── USER CODE BELOW ──
""".lstrip()


def _js_prescreen(code: str) -> str | None:
    """Lightweight JS pre-screen for obvious attacks. Returns error or None."""
    lowered = code.lower()

    # Block top-level require of dangerous modules (belt-and-suspenders)
    blocked_requires_patterns = [
        r'require\s*\(\s*[\'"]child_process[\'"]\s*\)',
        r'require\s*\(\s*[\'"]cluster[\'"]\s*\)',
        r'require\s*\(\s*[\'"]worker_threads[\'"]\s*\)',
        r'require\s*\(\s*[\'"]net[\'"]\s*\)',
        r'require\s*\(\s*[\'"]dgram[\'"]\s*\)',
        r'require\s*\(\s*[\'"]http[s2]*[\'"]\s*\)',
        r'require\s*\(\s*[\'"]tls[\'"]\s*\)',
        r'require\s*\(\s*[\'"]dns[\'"]\s*\)',
        r'require\s*\(\s*[\'"]vm[\'"]\s*\)',
        r'require\s*\(\s*[\'"]inspector[\'"]\s*\)',
        r'require\s*\(\s*[\'"]repl[\'"]\s*\)',
    ]
    for pattern in blocked_requires_patterns:
        if re.search(pattern, lowered):
            return f"禁止 require 该模块: {re.search(pattern, lowered).group(0)}"

    # Block obvious OS command construction
    if 'process.mainModule' in lowered:
        return "禁止访问 process.mainModule"

    return None


def _run_javascript(code: str, workdir: str, timeout: int) -> str:
    """Execute JavaScript code via Node.js in isolated subprocess."""
    full_code = _JS_GUARD_PREAMBLE + "\n" + code

    fd, temp_script = tempfile.mkstemp(suffix=".js", text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(full_code)

        env = _sanitize_env()
        env["REPL_ALLOW_DIR"] = workdir
        env["NODE_OPTIONS"] = "--no-warnings"

        # Find node executable
        node_exe = shutil.which("node") or "node"

        proc = subprocess.run(
            [node_exe, temp_script],
            capture_output=True, text=True, timeout=timeout,
            cwd=workdir, env=env,
            creationflags=_subprocess_flags(),
            preexec_fn=_set_limits if os.name != 'nt' else None,
        )

        parts = [proc.stdout.strip()] if proc.stdout.strip() else []
        if proc.stderr.strip():
            parts.append(f"[stderr]\n{proc.stderr.strip()}")
        if proc.returncode != 0 and not proc.stdout.strip():
            parts.insert(0, f"(进程退出码: {proc.returncode})")
        return "\n".join(parts) or "(无输出)"
    finally:
        try:
            os.unlink(temp_script)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# Executor registry — each language registers its own tool
# ═══════════════════════════════════════════════════════════

def _make_workspace_note(workdir: str) -> str:
    """Generate workspace identifier for output prefix."""
    if not _allow_dir:
        return ""
    call_uuid = os.path.basename(workdir)
    return f"[workspace: {call_uuid}/]\n"


def _append_download_links(result: str, workdir: str) -> str:
    """Append File download URLs to result if public_url is configured."""
    if not _public_url or not _allow_dir:
        return result
    call_uuid = os.path.basename(workdir)
    download_base = f"{_public_url}/files/{call_uuid}"
    for f in sorted(os.listdir(workdir)):
        fpath = os.path.join(workdir, f)
        if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
            result += f"\n\n[File] {download_base}/{f}"
    return result


def _executor_template(lang: str, prescreen_fn, run_fn):
    """Factory: produce a unified execute function for one language.

    Returns a callable (code: str, timeout: int) -> str that handles:
    1. prescreening  2. concurrency gate  3. workspace creation
    4. execution  5. post-processing (workspace note + download links)
    """
    def execute(code: str, timeout: int = DEFAULT_TIMEOUT) -> str:
        t0 = time.time()

        # Pre-screen
        err = prescreen_fn(code)
        if err:
            logger.warning("%s_prescreen_reject reason=%s code_preview=%.60s",
                          lang, err, code[:60].replace("\n", " "))
            return f"{lang} 代码审查未通过: {err}"

        # Concurrency gate
        if not _acquire_slot():
            logger.warning("exec_busy max_concurrent=%d lang=%s", _max_concurrent, lang)
            return f"服务器繁忙（并发执行数已达上限 {_max_concurrent}），请稍后重试"

        try:
            workdir = _make_workdir()
            result = run_fn(code, workdir, timeout)
            ws_note = _make_workspace_note(workdir) if lang != "Python" else _make_workspace_note(workdir)
            result = ws_note + result
            result = result[:MAX_OUTPUT]
            result = _append_download_links(result, workdir)

            elapsed_ms = int((time.time() - t0) * 1000)
            logger.info("exec_ok lang=%s uuid=%s elapsed_ms=%d output_len=%d",
                        lang, os.path.basename(workdir) if _allow_dir else "-",
                        elapsed_ms, len(result))
            return result
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.warning("exec_timeout lang=%s timeout=%d elapsed_ms=%d",
                          lang, timeout, elapsed_ms)
            return f"执行超时（>{timeout}秒），已终止进程"
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error("exec_error lang=%s error=%s elapsed_ms=%d",
                        lang, type(e).__name__, elapsed_ms)
            return f"执行异常: {str(e)}"
        finally:
            _exec_semaphore.release()

    return execute


def _build_tools() -> list[dict]:
    """Build MCP tool definitions based on enabled languages."""
    tools = []

    # Python — always enabled
    tools.append({
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
    })

    # Shell
    if _enable_shell:
        tools.append({
            "name": "run_shell",
            "description": (
                "在隔离子进程中执行 Shell 命令并返回输出。适用场景：文件操作、文本处理、简单脚本。"
                + ("工作目录已隔离。" if _allow_dir else "")
                + ("网络工具已被拦截。" if _no_network else "")
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "完整的 Shell 命令"}},
                "required": ["code"],
            },
        })

    # JavaScript
    if _enable_javascript:
        tools.append({
            "name": "run_javascript",
            "description": (
                "在隔离子进程中通过 Node.js 执行 JavaScript 代码并返回输出。适用场景：数据处理、算法验证、JSON 转换。"
                + ("工作目录已隔离。" if _allow_dir else "")
                + ("无网络访问。" if _no_network else "")
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "完整的 JavaScript 代码"}},
                "required": ["code"],
            },
        })

    return tools


# ═══════════════════════════════════════════════════════════
# MCP HTTP handler
# ═══════════════════════════════════════════════════════════

class MCPHandler(BaseHTTPRequestHandler):
    """JSON-RPC 2.0 handler that dispatches to registered language executors."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        # File download: GET /files/{uuid}/{filename}
        if self.path.startswith("/files/") and _allow_dir:
            rel = self.path[len("/files/"):].lstrip("/")
            if rel.endswith("/"):
                rel = rel.rstrip("/")
            parts = rel.split("/")
            if len(parts) == 1:
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
            result = {"tools": _build_tools()}
        elif method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name", "")
            code = params.get("arguments", {}).get("code", "")

            # Route to correct executor by tool name
            executor = _EXECUTORS.get(tool_name)
            if executor and code:
                result = {"content": [{"type": "text", "text": executor(code)}]}
            else:
                result = {"content": [{"type": "text", "text": f"未知工具: {tool_name}"}]}
        else:
            self.send_error(404)
            return

        resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())

    def log_message(self, _fmt, *args):
        logger.info("http %s", args[0])


# ═══════════════════════════════════════════════════════════
# Main — CLI parsing, executor registration, server startup
# ═══════════════════════════════════════════════════════════

# Built at startup after CLI parsing
_EXECUTORS: dict[str, callable] = {}


if __name__ == "__main__":
    p = ArgumentParser(description="REPL MCP Server — multi-language sandbox execution")
    p.add_argument("--port", type=int, default=9200)
    p.add_argument("--allow-dir", type=str,
                   help="仅允许子进程访问此目录")
    p.add_argument("--no-network", action="store_true",
                   help="禁止子进程网络访问")
    p.add_argument("--keep-minutes", type=int, default=DEFAULT_KEEP_MINUTES,
                   help=f"生成文件保留时长（分钟），默认 {DEFAULT_KEEP_MINUTES}")
    p.add_argument("--max-memory-mb", type=int, default=DEFAULT_MAX_MEMORY_MB,
                   help=f"子进程最大内存（MB），默认 {DEFAULT_MAX_MEMORY_MB}")
    p.add_argument("--max-nproc", type=int, default=DEFAULT_MAX_NPROC,
                   help=f"子进程最大进程数，默认 {DEFAULT_MAX_NPROC}")
    p.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CONCURRENT,
                   help=f"最大并发执行数，默认 {DEFAULT_MAX_CONCURRENT}")
    p.add_argument("--public-url", type=str, default="",
                   help="对外可访问的完整地址，用于生成 File 下载链接")
    p.add_argument("--enable-shell", action="store_true",
                   help="启用 Shell 执行支持")
    p.add_argument("--enable-shell-local", action="store_true",
                   help="Windows 本地模式启用 Shell（⚠️ 安全风险，仅限开发环境）")
    p.add_argument("--enable-javascript", action="store_true",
                   help="启用 JavaScript (Node.js) 执行支持")
    args = p.parse_args()

    # ── Config: CLI args > env vars > defaults ──
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

    _enable_shell = (args.enable_shell
                     or os.environ.get("REPL_ENABLE_SHELL", "").lower() in ("1", "true", "yes"))
    _enable_shell_local = (args.enable_shell_local
                           or os.environ.get("REPL_ENABLE_SHELL_LOCAL", "").lower() in ("1", "true", "yes"))
    _enable_javascript = (args.enable_javascript
                          or os.environ.get("REPL_ENABLE_JAVASCRIPT", "").lower() in ("1", "true", "yes"))

    # ── Shell safety gate for local (non-Docker) Windows mode ──
    if _enable_shell and os.name == 'nt' and not _enable_shell_local:
        logger.warning(
            "shell_local_disabled — Shell 在 Windows 本地模式默认禁用（安全原因）。"
            "如需启用，请加 --enable-shell-local 参数（⚠️ 仅限开发环境）。"
            "Docker 容器模式下此限制不适用。"
        )
        _enable_shell = False

    if _enable_shell_local and os.name == 'nt':
        logger.warning(
            "⚠️ shell_local_enabled — Shell 在非 Docker 环境下运行，安全隔离较弱。"
            "生产环境请使用 Docker 部署。"
        )

    # ── Register executors ──
    # Python — always registered
    _EXECUTORS["run_python"] = _executor_template(
        "Python", _py_ast_prescreen, _run_python)

    if _enable_shell:
        _EXECUTORS["run_shell"] = _executor_template(
            "Shell", _sh_prescreen, _run_shell)

    if _enable_javascript:
        # Check node availability
        node_path = shutil.which("node")
        if node_path:
            _EXECUTORS["run_javascript"] = _executor_template(
                "JavaScript", _js_prescreen, _run_javascript)
        else:
            logger.warning("javascript_disabled — Node.js 未安装，跳过 JS 执行器注册。"
                          "请 apt-get install nodejs 或安装 Node.js。")
            _enable_javascript = False

    # ── Background cleanup thread ──
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

    # ── Start server ──
    server = ThreadingHTTPServer(("0.0.0.0", args.port), MCPHandler)
    logger.info("start port=%d timeout=%d max_mem=%d max_nproc=%d max_concurrent=%d keep=%d",
                args.port, DEFAULT_TIMEOUT, _max_memory_mb, _max_nproc, _max_concurrent, _keep_minutes)
    if _allow_dir:
        logger.info("allow_dir=%s", _allow_dir)
    if _no_network:
        logger.info("network=blocked")

    # Language status
    enabled_langs = [k.replace("run_", "") for k in _EXECUTORS.keys()]
    logger.info("languages=%s", ",".join(enabled_langs))
    if _enable_javascript:
        logger.info("node_path=%s", node_path)

    if os.name != 'nt':
        logger.info("os_limits rlimit_as=%dMB rlimit_cpu=%ds rlimit_nproc=%d",
                    _max_memory_mb, DEFAULT_TIMEOUT + 10, _max_nproc)

    # Graceful shutdown
    def _on_shutdown(signum, frame):
        logger.info("signal=%d shutting_down", signum)
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)

    server.timeout = 0.5
    try:
        while not _shutdown_event.is_set():
            server.handle_request()
    finally:
        server.server_close()
        logger.info("shutdown_complete")

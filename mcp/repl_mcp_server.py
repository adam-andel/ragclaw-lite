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
import hmac as _hmac_module
import hashlib as _hashlib_module
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
# ── Network policy (hot-reloadable at runtime) ──
# mode: "deny" (default, block all) | "allow" (open, debug) | "allowlist" (per-domain)
_network_mode: str = "deny"
_allow_domains: list[str] = []      # allowed hostnames in allowlist mode
_allow_methods: list[str] = []      # reserved for L7 filtering (Phase 2)
_policy_file: str = "/tmp/repl_network_policy.json"# ── Egress proxy (network-layer broker, approach B) ───
# Children are forced through this loopback proxy so all HTTP(S) egress
# (incl. asyncio/httpx/curl) hits the allowlist. Port is shared with
# mcp/egress_proxy.py via REPL_EGRESS_PORT.
_EGRESS_PORT: int = int(os.environ.get("REPL_EGRESS_PORT", "1080"))
_no_network: bool = False           # backward-compat alias (sets initial mode=deny)
_max_memory_mb: int = DEFAULT_MAX_MEMORY_MB
_max_nproc: int = DEFAULT_MAX_NPROC
_max_concurrent: int = DEFAULT_MAX_CONCURRENT
_keep_minutes: int = DEFAULT_KEEP_MINUTES
_keep_file: str = "/tmp/repl_keep_minutes.json"  # persisted file-retention (survives restart)
_public_url: str = ""
_enable_shell: bool = False
_enable_shell_local: bool = False
_enable_javascript: bool = False
_shutdown_event = threading.Event()
_exec_semaphore = threading.BoundedSemaphore(DEFAULT_MAX_CONCURRENT)

# ── Auth (Backend session injection + HMAC signature) ──
_REPL_AUTH_SECRET: str | None = None   # shared HMAC secret with Backend; None => auth disabled
_auth_required: bool = False           # reject calls without a valid signature
_isolation_enabled: bool = False       # per-user UID isolation (tied to auth)
_allow_anonymous: bool = False         # if True, allow calls without a valid signature
_uid_pool_base: int = 2000             # first UID of the numeric pool
_uid_pool_size: int = 100              # pool size (modulo space)
_account_cache: dict = {}              # user id -> account dict

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
    """Copy environ, strip credential-like vars, set thread limits for Docker."""
    env = os.environ.copy()
    for k in list(env.keys()):
        if any(pat in k.upper() for pat in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
            env.pop(k, None)
    # Limit BLAS/numpy threads to avoid pthread_create failures under RLIMIT_NPROC
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    # Matplotlib config dir (read-only rootfs workaround)
    env["MPLCONFIGDIR"] = "/tmp/matplotlib"
    # Inject current network policy so the guard preamble can enforce it.
    env["REPL_NETWORK_POLICY"] = json.dumps(_build_policy(), ensure_ascii=False)

   # ── Approach B: force every child through the egress broker ───
    # All HTTP(S) clients (requests/urllib3/httpx sync+async, curl, wget...)
    # read HTTP_PROXY/HTTPS_PROXY, which closes the asyncio/httpx bypass.
    # Internal ERAG traffic (mcp-repl <-> backend) stays direct via NO_PROXY.
    # In deny mode no proxy is injected — the in-process guard already blocks
    # everything, and there is nothing legitimate to proxy.
    if _network_mode != "deny":
        proxy = f"http://127.0.0.1:{_EGRESS_PORT}"
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["http_proxy"] = proxy
        env["https_proxy"] = proxy
        env["NO_PROXY"] = "localhost,127.0.0.1,mcp-repl,erag-lite"
        env["no_proxy"] = "localhost,127.0.0.1,mcp-repl,erag-lite"
    return env


def _acquire_slot(timeout: int = 5) -> bool:
    """Try to acquire a concurrency slot. Returns False if busy."""
    return _exec_semaphore.acquire(timeout=timeout)


def _make_workdir(workspace_id: str | None = None, acct=None) -> str:
    """Create a workdir.

    When isolated (acct set): workdir lives under _allow_dir/user_<name>/,
    owned by that account with mode 700, so one user's code can neither be
    read by nor read others' directories (each account dir is 700).

    Otherwise original behaviour: a per-call UUID dir (or stable workspace_id
    dir) directly under _allow_dir / tempdir.
    """
    if acct is not None and _allow_dir:
        base = os.path.join(_allow_dir, "user_" + acct["name"])
        _ensure_dir_owned(base, acct, 0o700)
        if workspace_id and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", workspace_id):
            workdir = os.path.join(base, workspace_id)
            _ensure_dir_owned(workdir, acct, 0o700)
            return workdir
        call_uuid = str(uuid.uuid4())[:8]
        workdir = os.path.join(base, call_uuid)
        _ensure_dir_owned(workdir, acct, 0o700)
        return workdir

    if _allow_dir and workspace_id:
        # Sanitize: only safe chars, capped length, prevent traversal.
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", workspace_id):
            workdir = os.path.join(_allow_dir, workspace_id)
            os.makedirs(workdir, exist_ok=True)
            return workdir
    call_uuid = str(uuid.uuid4())[:8]
    if _allow_dir:
        workdir = os.path.join(_allow_dir, call_uuid)
        os.makedirs(workdir, exist_ok=True)
        return workdir
    return tempfile.mkdtemp(prefix="repl_")


def _subprocess_flags() -> int:
    """Cross-platform process-creation flags."""
    return subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0


def _verify_auth(auth) -> str | None:
    """Verify a Backend-signed identity envelope.

    Envelope (sent by Backend inside tools/call arguments):
        {"user": "<id>", "exp": <int>, "sig": "<hex>"}
    sig = HMAC-SHA256(secret, f"{user}|{exp}")      (exp=0 means no expiry)

    Returns the verified user id, or None when auth is disabled, or the
    envelope is missing / malformed / expired / has a bad signature.
    """
    if _REPL_AUTH_SECRET is None:
        return None
    if not isinstance(auth, dict):
        return None
    user = auth.get("user")
    sig = auth.get("sig")
    if not isinstance(user, str) or not user:
        return None
    if not isinstance(sig, str) or not sig:
        return None
    try:
        exp = int(auth.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if exp and time.time() > exp:
        return None
    msg = f"{user}|{exp}".encode("utf-8")
    expected = _hmac_module.new(_REPL_AUTH_SECRET.encode("utf-8"), msg,
                                 _hashlib_module.sha256).hexdigest()
    if not _hmac_module.compare_digest(expected, sig):
        return None
    return user


def _set_auth_secret(secret):
    """Hot-reload the shared HMAC secret at runtime (called by Backend).

    ``secret`` may be a non-empty string (enable auth + isolation) or an
    empty/None value (disable). Isolation is tied to auth being enabled.
    """
    global _REPL_AUTH_SECRET, _auth_required, _isolation_enabled
    _REPL_AUTH_SECRET = secret or None
    _auth_required = bool(_REPL_AUTH_SECRET) and not _allow_anonymous
    _isolation_enabled = bool(_REPL_AUTH_SECRET)


def _resolve_user_account(user):
    """Map a verified user id to a numeric pool account (deterministic).

    user -> sha256 -> idx = hash % pool_size -> uid = base + idx.
    We use RAW numeric UIDs (no /etc/passwd entries) so this works on a
    read-only rootfs. Returns a dict {uid, gid, name} or None.
    """
    if not _isolation_enabled or not user:
        return None
    cached = _account_cache.get(user)
    if cached is not None:
        return cached
    h = int(_hashlib_module.sha256(user.encode("utf-8")).hexdigest(), 16)
    idx = h % _uid_pool_size
    uid = _uid_pool_base + idx
    acct = {"uid": uid, "gid": uid, "name": f"u{uid}"}
    _account_cache[user] = acct
    return acct


def _ensure_dir_owned(path: str, acct, mode: int = 0o700) -> None:
    """Create dir (idempotent) and chown/chmod to the target account when isolated."""
    os.makedirs(path, exist_ok=True)
    if acct is not None and os.name != 'nt':
        try:
            os.chown(path, acct["uid"], acct["gid"])
        except OSError:
            pass
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _build_preexec(acct):
    """preexec_fn: apply rlimits, then drop to the target account's UID/GID.

    If dropping privileges fails we _exit(1) so the child never runs as root.
    """
    if acct is None or os.name == 'nt':
        return _set_limits if os.name != 'nt' else None

    def _fn():
        _set_limits()
        try:
            os.setgid(acct["gid"])
            os.setgroups([])
            os.setuid(acct["uid"])
        except OSError:
            os._exit(1)

    return _fn


# ═══════════════════════════════════════════════════════════
# Python executor — Docker-simplified guard
# ═══════════════════════════════════════════════════════════
# Docker provides hardware-level isolation:
#   read_only rootfs, tmpfs workspace, cap_drop ALL, seccomp,
#   no-new-privileges, non-root user, resource limits.
# Python-level guards only handle network blocking (--no-network).
# All import/filesystem/subprocess guards removed — they caused
# compatibility issues with pptx, PIL, ssl, lxml, etc.

def _build_policy() -> dict:
    """Serialize current network policy for injection into child subprocesses."""
    return {
        "mode": _network_mode,
        "domains": list(_allow_domains),
        "methods": list(_allow_methods),
    }


def _set_network_policy(mode: str, domains: list, methods: list) -> None:
    """Apply a new network policy at runtime (hot-reload, no restart)."""
    global _network_mode, _allow_domains, _allow_methods
    _network_mode = mode
    _allow_domains = list(domains)
    _allow_methods = list(methods)
    try:
        with open(_policy_file, "w", encoding="utf-8") as f:
            json.dump(_build_policy(), f, ensure_ascii=False)
    except Exception:
        pass


def _load_policy_file() -> None:
    """Load persisted policy from disk (survives container restart)."""
    global _network_mode, _allow_domains, _allow_methods
    try:
        if os.path.exists(_policy_file):
            with open(_policy_file, "r", encoding="utf-8") as f:
                p = json.load(f)
            _network_mode = p.get("mode", _network_mode)
            _allow_domains = list(p.get("domains", _allow_domains))
            _allow_methods = list(p.get("methods", _allow_methods))
            logger.info("policy_loaded_from_file mode=%s domains=%d",
                        _network_mode, len(_allow_domains))
    except Exception as e:
        logger.warning("policy_load_failed %s", e)


def _set_keep_minutes(mins: int) -> None:
    """Apply a new file-retention duration at runtime (hot-reload, no restart)."""
    global _keep_minutes
    _keep_minutes = max(1, int(mins))
    try:
        with open(_keep_file, "w", encoding="utf-8") as f:
            json.dump({"keep_minutes": _keep_minutes}, f)
    except Exception:
        pass


def _load_keep_file() -> None:
    """Load persisted retention from disk (survives container restart)."""
    global _keep_minutes
    try:
        if os.path.exists(_keep_file):
            with open(_keep_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            _keep_minutes = max(1, int(d.get("keep_minutes", _keep_minutes)))
            logger.info("keep_minutes_loaded_from_file mins=%d", _keep_minutes)
    except Exception as e:
        logger.warning("keep_minutes_load_failed %s", e)


def _py_build_guard(per_call_dir: str) -> str:
    """Always install the network guard; runtime behavior is driven by the
    REPL_NETWORK_POLICY env var (injected per-call from _build_policy()).

    Modes:
      deny      -> block all outbound connections & DNS (default, safe)
      allow     -> permit all (debug only)
      allowlist -> permit only hostnames in 'domains'; bare IP literals are
                   rejected to prevent DNS-rebind / direct-IP bypass.
    """
    preamble = r'''# --- guard preamble (Docker-simplified) ---
import socket as _g_socket
import os as _g_os
import json as _g_json
import ssl as _g_ssl

def _g_load_policy():
    try:
        return _g_json.loads(_g_os.environ.get("REPL_NETWORK_POLICY", "{}"))
    except Exception:
        return {}

_G_POLICY = _g_load_policy()
_G_MODE = _G_POLICY.get("mode", "deny")
_G_DOMAINS = set(d.lower() for d in _G_POLICY.get("domains", []))
_G_METHODS = set(m.upper() for m in _G_POLICY.get("methods", []))
# IPs produced by an allowed getaddrinfo() — used to admit the subsequent
# socket.connect(ip) that create_connection performs internally.
_g_resolved_ips = set()

def _g_is_ip(h):
    try:
        _g_socket.inet_pton(_g_socket.AF_INET, h); return True
    except Exception:
        pass
    try:
        _g_socket.inet_pton(_g_socket.AF_INET6, h); return True
    except Exception:
        pass
    return False

def _g_host_allowed(host):
    if not host:
        return False
    h = str(host).lower()
    if ":" in h:
        h = h.split(":")[0]
    # Reject bare IP literals (anti DNS-rebind / direct-IP bypass)
    if _g_is_ip(h):
        return False
    for d in _G_DOMAINS:
        if h == d or h.endswith("." + d):
            return True
    return False

def _g_create_connection(orig, *a, **kw):
    host = None
    if a and isinstance(a[0], (tuple, list)) and a[0]:
        host = a[0][0]
    if _G_MODE == "allow":
        return orig(*a, **kw)
    if _G_MODE == "allowlist":
        if _g_host_allowed(host):
            return orig(*a, **kw)
        raise PermissionError("网络访问被拒绝（目标不在白名单中）: %s" % (host,))
    raise PermissionError("网络访问已被禁止（deny 模式）。如需外部数据，请通过 MCP 工具获取。")

def _g_getaddrinfo(orig, *a, **kw):
    host = a[0] if a else None
    if _G_MODE == "allow":
        return orig(*a, **kw)
    if _G_MODE == "allowlist":
        if _g_host_allowed(host):
            res = orig(*a, **kw)
            # Cache the resolved IPs so the later socket.connect(ip) (which
            # create_connection performs internally) is recognised as legal.
            for item in res:
                try:
                    _g_resolved_ips.add(item[4][0])
                except Exception:
                    pass
            return res
        raise PermissionError("DNS 解析被拒绝（目标不在白名单中）: %s" % (host,))
    raise PermissionError("网络访问已被禁止（deny 模式，DNS 解析被阻断）。")

# Optional L7 method allowlist (Phase 2 reserved field).
def _g_method_allowed(method):
    if not _G_METHODS:
        return True
    return (method or "").upper() in _G_METHODS

# ── Raw-socket fallback (§7): wrap socket.connect + ssl wrap_socket ──
# Closes the bypass where a C extension or asyncio reaches loop.sock_connect
# -> sock.connect() directly, skipping the create_connection patch above.
# A resolved (whitelisted) IP is admitted via the cache; a bare hardcoded IP
# is rejected in allowlist (anti direct-IP / DNS-rebind bypass).
def _g_connect(self, address, *a, **kw):
    host = address[0] if isinstance(address, (tuple, list)) and address else None
    if _G_MODE == "allow":
        return _orig_connect(self, address, *a, **kw)
    if _G_MODE == "allowlist":
        if host and not _g_is_ip(host):
            if _g_host_allowed(host):
                return _orig_connect(self, address, *a, **kw)
            raise PermissionError("网络访问被拒绝（目标不在白名单中）: %s" % (host,))
        # bare IP literal
        if host in _g_resolved_ips:
            return _orig_connect(self, address, *a, **kw)
        raise PermissionError("网络访问被拒绝（裸 IP 不在解析缓存中）: %s" % (host,))
    raise PermissionError("网络访问已被禁止（deny 模式）。")

def _g_wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True,
                   suppress_ragged_eofs=True, server_hostname=None, session=None):
    if _G_MODE == "allow":
        return _orig_wrap(self, sock, server_side, do_handshake_on_connect,
                          suppress_ragged_eofs, server_hostname, session)
    if _G_MODE == "allowlist":
        if server_hostname and not _g_host_allowed(server_hostname):
            raise PermissionError("TLS 连接被拒绝（目标不在白名单中）: %s" % (server_hostname,))
        # server_hostname None (IP-based TLS) is covered by the connect cache.
        return _orig_wrap(self, sock, server_side, do_handshake_on_connect,
                          suppress_ragged_eofs, server_hostname, session)
    raise PermissionError("网络访问已被禁止（deny 模式）。")

_orig_create_conn = getattr(_g_socket, 'create_connection', None)
_orig_getaddrinfo = _g_socket.getaddrinfo
_orig_connect = _g_socket.socket.connect
_orig_wrap = _g_ssl.SSLContext.wrap_socket
if _orig_create_conn is not None:
    def _g_cc(*a, **kw):
        return _g_create_connection(_orig_create_conn, *a, **kw)
    _g_socket.create_connection = _g_cc
_g_socket.getaddrinfo = lambda *a, **kw: _g_getaddrinfo(_orig_getaddrinfo, *a, **kw)
_g_socket.socket.connect = _g_connect
_g_ssl.SSLContext.wrap_socket = _g_wrap_socket
'''
    return preamble


def _py_ast_prescreen(code: str) -> str | None:
    """Lightweight AST scan — syntax check only. Docker handles security."""
    try:
        _ast_module.parse(code)
    except SyntaxError as e:
        return f"语法错误 (行 {e.lineno}): {e.msg}"
    return None


def _run_python(code: str, workdir: str, timeout: int, acct=None) -> str:
    """Execute Python code in isolated subprocess (preserved from original)."""
    guard = _py_build_guard(workdir)
    full_code = guard + "\n" + code if guard else code

    # Write the script INSIDE workdir (owned by the target account) so the
    # dropped-privilege child can read it (a root-owned /tmp script would be 0600).
    script_path = os.path.join(workdir, f".repl_{uuid.uuid4().hex[:8]}.py")
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(full_code)
        try:
            os.chmod(script_path, 0o644)
        except OSError:
            pass

        env = _sanitize_env()
        if acct is not None:
            env["HOME"] = workdir

        proc = subprocess.run(
            [os.sys.executable, script_path],
            capture_output=True, text=True, timeout=timeout,
            cwd=workdir, env=env,
            creationflags=_subprocess_flags(),
            preexec_fn=_build_preexec(acct),
        )

        parts = [proc.stdout.strip()] if proc.stdout.strip() else []
        if proc.stderr.strip():
            parts.append(f"[stderr]\n{proc.stderr.strip()}")
        if proc.returncode != 0 and not proc.stdout.strip():
            parts.insert(0, f"(进程退出码: {proc.returncode})")
        return "\n".join(parts) or "(无输出)"
    finally:
        try:
            os.unlink(script_path)
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


# Shell network-tool reasons that may be permitted in 'allow' mode
_SHELL_NETWORK_REASONS = {
    "curl", "wget", "netcat", "ncat", "ssh", "scp",
    "ftp", "telnet", "nmap", "socat",
}

# Proxy-aware tools that honor HTTP_PROXY: in allowlist mode they are forced
# through the egress broker (which enforces the domain allowlist), so they are
# safe to permit. nc/ssh/socat etc. ignore proxy env and stay blocked.
_SHELL_PROXY_AWARE = {"curl", "wget"}


def _sh_prescreen(code: str) -> str | None:
    """Scan shell command for dangerous patterns. Returns error or None.

    * 'allow' mode: all network tools permitted, but destructive
      filesystem/kernel patterns remain blocked.
    * 'allowlist' mode: only proxy-aware tools (curl/wget) are permitted —
      they are enforced by the egress broker; nc/ssh/socat stay blocked
      because they bypass the proxy and would evade L7 enforcement.
    * destructive filesystem/kernel patterns remain blocked in every mode.
    """
    for pattern, reason in _SHELL_DANGEROUS:
        if _network_mode == "allow" and reason in _SHELL_NETWORK_REASONS:
            continue
        if _network_mode == "allowlist" and reason in _SHELL_PROXY_AWARE:
            continue
        if re.search(pattern, code, re.IGNORECASE):
            return f"禁止执行 Shell 命令（匹配危险模式: {reason}）"
    return None


def _run_shell(code: str, workdir: str, timeout: int, acct=None) -> str:
    """Execute shell command in isolated subprocess."""
    # Force working directory first
    if os.name == 'nt':
        shell_cmd = ["cmd.exe", "/c", code]
    else:
        # cd to workdir, then execute; use set -e for early failure
        shell_cmd = ["/bin/sh", "-c", f"cd '{workdir}' && {code}"]

    env = _sanitize_env()
    if acct is not None:
        env["HOME"] = workdir
    try:
        proc = subprocess.run(
            shell_cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=workdir, env=env,
            creationflags=_subprocess_flags(),
            preexec_fn=_build_preexec(acct),
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
  // In 'allow' mode, network modules (net/http/https/dns/tls) are permitted;
  // child_process / vm / inspector remain blocked in all modes.
  var _netPol = {};
  try { _netPol = JSON.parse(process.env.REPL_NETWORK_POLICY || '{}'); } catch (e) {}
  var _allowNet = (_netPol.mode === 'allow');
  var blockedModules = _allowNet ? [
    'child_process', 'cluster', 'worker_threads',
    'vm', 'inspector', 'repl', 'v8'
  ] : [
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


def _run_javascript(code: str, workdir: str, timeout: int, acct=None) -> str:
    """Execute JavaScript code via Node.js in isolated subprocess."""
    full_code = _JS_GUARD_PREAMBLE + "\n" + code

    # Write the script INSIDE workdir so the dropped-privilege child can read it.
    script_path = os.path.join(workdir, f".repl_{uuid.uuid4().hex[:8]}.js")
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(full_code)
        try:
            os.chmod(script_path, 0o644)
        except OSError:
            pass

        env = _sanitize_env()
        env["REPL_ALLOW_DIR"] = workdir
        env["NODE_OPTIONS"] = "--no-warnings"
        if acct is not None:
            env["HOME"] = workdir

        # Find node executable
        node_exe = shutil.which("node") or "node"

        proc = subprocess.run(
            [node_exe, script_path],
            capture_output=True, text=True, timeout=timeout,
            cwd=workdir, env=env,
            creationflags=_subprocess_flags(),
            preexec_fn=_build_preexec(acct),
        )

        parts = [proc.stdout.strip()] if proc.stdout.strip() else []
        if proc.stderr.strip():
            parts.append(f"[stderr]\n{proc.stderr.strip()}")
        if proc.returncode != 0 and not proc.stdout.strip():
            parts.insert(0, f"(进程退出码: {proc.returncode})")
        return "\n".join(parts) or "(无输出)"
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# Executor registry — each language registers its own tool
# ═══════════════════════════════════════════════════════════

def _make_workspace_note(workdir: str) -> str:
    """Generate workspace identifier for output prefix.

    Emits the full relative path under _allow_dir (e.g. `user_u2001/<ws>` when
    per-user isolation is active, or just `<ws>` in single-account mode) so the
    Backend can reconstruct the nested /files/... URL and proxy it correctly.
    """
    if not _allow_dir:
        return ""
    try:
        rel = os.path.relpath(workdir, _allow_dir)
    except ValueError:
        return ""
    return f"[workspace: {rel}/]\n"


def _append_download_links(result: str, workdir: str) -> str:
    """Append File download URLs to result if public_url is configured.

    Uses the full relative path under _allow_dir so nested per-user dirs
    (user_uXXXX/<ws>/file) produce correct /files/... URLs.
    """
    if not _public_url or not _allow_dir:
        return result
    try:
        rel = os.path.relpath(workdir, _allow_dir)
    except ValueError:
        return result
    download_base = f"{_public_url}/files/{rel}"
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
    def execute(code: str, timeout: int = DEFAULT_TIMEOUT,
                workspace_id: str | None = None, user: str | None = None) -> str:
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

        # Resolve per-user account (deterministic numeric UID pool).
        acct = None
        if user and _isolation_enabled:
            acct = _resolve_user_account(user)

        try:
            workdir = _make_workdir(workspace_id, acct)
            result = run_fn(code, workdir, timeout, acct)
            ws_note = _make_workspace_note(workdir)
            result = ws_note + result
            result = result[:MAX_OUTPUT]
            result = _append_download_links(result, workdir)

            elapsed_ms = int((time.time() - t0) * 1000)
            logger.info("exec_ok lang=%s user=%s uuid=%s elapsed_ms=%d output_len=%d",
                        lang, user or "-", os.path.basename(workdir) if _allow_dir else "-",
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


def _net_status_text() -> str:
    """Human-readable network status for tool descriptions."""
    if _network_mode == "allow":
        return "网络访问已开放（不受限制，调试用）。"
    if _network_mode == "allowlist":
        dom = ", ".join(_allow_domains) if _allow_domains else "（未配置任何域名）"
        return f"仅允许访问白名单域名: {dom}。"
    return "无网络访问。"


def _build_tools() -> list[dict]:
    """Build MCP tool definitions based on enabled languages."""
    tools = []

    # Python — always enabled
    tools.append({
        "name": "run_python",
        "description": (
            "在隔离子进程中执行 Python 代码并返回输出。适用场景：文件生成、数据处理、计算。"
            + ("工作目录已隔离。" if _allow_dir else "")
            + _net_status_text()
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "完整的 Python 代码"},
                "workspace_id": {
                    "type": "string",
                    "description": "可选：固定 workspace 目录名（仅允许 [A-Za-z0-9_-]，≤64 字符）。"
                                   "同一 workspace_id 的多次调用共享同一工作目录，便于在一个对话内先生成文件再处理它。",
                },
                "auth": {
                    "type": "object",
                    "description": "身份信封（Backend 签名注入；未启用 --auth-secret 时忽略）。"
                                   "结构: {user:str, exp:int(0=不过期), sig:hex}。"
                                   "sig = HMAC-SHA256(secret, f'{user}|{exp}')。",
                    "properties": {
                        "user": {"type": "string"},
                        "exp": {"type": "integer"},
                        "sig": {"type": "string"},
                    },
                },
            },
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
                + ("网络工具已被拦截。" if _network_mode != "allow" else "网络访问已开放（不受限制，调试用）。")
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                "code": {"type": "string", "description": "完整的 Shell 命令"},
                "auth": {
                    "type": "object",
                    "description": "身份信封（Backend 签名注入）。结构见 run_python。",
                    "properties": {
                        "user": {"type": "string"},
                        "exp": {"type": "integer"},
                        "sig": {"type": "string"},
                    },
                },
            },
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
                + ("网络模块已被拦截。" if _network_mode != "allow" else "网络访问已开放（不受限制，调试用）。")
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                "code": {"type": "string", "description": "完整的 JavaScript 代码"},
                "auth": {
                    "type": "object",
                    "description": "身份信封（Backend 签名注入）。结构见 run_python。",
                    "properties": {
                        "user": {"type": "string"},
                        "exp": {"type": "integer"},
                        "sig": {"type": "string"},
                    },
                },
            },
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

        # File download: GET /files/<path-under-allow-dir>  (depth-agnostic;
        # supports nested per-user dirs like user_u2005/<ws>/<file>)
        if self.path.startswith("/files/") and _allow_dir:
            rel = self.path[len("/files/"):].lstrip("/")
            if rel.endswith("/"):
                rel = rel.rstrip("/")
            parts = [p for p in rel.split("/") if p]
            if parts:
                target = os.path.realpath(os.path.join(_allow_dir, *parts))
                root = os.path.realpath(_allow_dir)
                if target == root or target.startswith(root + os.sep):
                    if os.path.isdir(target):
                        try:
                            files = [f for f in os.listdir(target)
                                    if os.path.isfile(os.path.join(target, f))]
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "path": rel,
                                "files": [{"name": f, "url": f"/files/{rel}/{f}"}
                                         for f in sorted(files)]
                            }, ensure_ascii=False).encode())
                            return
                        except Exception:
                            pass
                    elif os.path.isfile(target):
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Disposition",
                                        f'attachment; filename="{os.path.basename(target)}"')
                        self.end_headers()
                        with open(target, "rb") as f:
                            self.wfile.write(f.read())
                        return
            self.send_error(404)
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
            arguments = params.get("arguments", {})
            code = arguments.get("code", "")
            workspace_id = arguments.get("workspace_id")

            # ── Identity: verify Backend-signed envelope (fail closed) ──
            auth = arguments.get("auth") or params.get("auth")
            user = _verify_auth(auth)
            if _auth_required and user is None:
                logger.warning("auth_reject tool=%s reason=invalid_or_missing_sig", tool_name)
                resp = {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32001,
                                  "message": "身份校验失败：缺少或无效的签名（需 Backend 签名的 auth 信封）"}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())
                return

            # Route to correct executor by tool name
            executor = _EXECUTORS.get(tool_name)
            if executor and code:
                result = {"content": [{"type": "text",
                        "text": executor(code, workspace_id=workspace_id, user=user)}]}
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

    def _json_response(self, code: int, obj: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode())

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/policy":
            self._handle_policy_update(body)
            return
        if self.path == "/keep-minutes":
            self._handle_keep_update(body)
            return
        if self.path == "/auth-secret":
            self._handle_auth_secret_update(body)
            return
        self.send_error(404)

    def _handle_policy_update(self, body: dict):
        """Hot-reload network policy (called by backend /api/config/sandbox-network)."""
        mode = body.get("mode")
        if mode not in ("deny", "allow", "allowlist"):
            self._json_response(400, {"error": "invalid mode, expected deny|allow|allowlist"})
            return
        domains = body.get("domains") or []
        methods = body.get("methods") or []
        if isinstance(domains, str):
            domains = [d.strip() for d in domains.split(",") if d.strip()]
        if isinstance(methods, str):
            methods = [m.strip().upper() for m in methods.split(",") if m.strip()]
        _set_network_policy(mode, domains, methods)
        logger.info("policy_updated mode=%s domains=%d", mode, len(domains))
        self._json_response(200, {"status": "ok", "policy": _build_policy()})

    def _handle_keep_update(self, body: dict):
        """Hot-reload file-retention duration (called by backend /api/config/sandbox-network)."""
        mins = body.get("keep_minutes")
        if not isinstance(mins, int) or mins < 1:
            self._json_response(400, {"error": "keep_minutes must be a positive integer (minutes)"})
            return
        _set_keep_minutes(mins)
        logger.info("keep_minutes_updated mins=%d", _keep_minutes)
        self._json_response(200, {"status": "ok", "keep_minutes": _keep_minutes})

    def _handle_auth_secret_update(self, body: dict):
        """Hot-reload the REPL identity HMAC secret (called by backend /api/config/repl-auth).

        Internal-network-only endpoint (no host port mapping), mirroring
        /policy and /keep-minutes. Body: {"secret": "<str>"} — empty/None disables
        auth (single-account fallback). After a successful update, per-user UID
        isolation is enabled/disabled to match.
        """
        secret = body.get("secret")
        if secret is not None and not isinstance(secret, str):
            self._json_response(400, {"error": "secret must be a string"})
            return
        _set_auth_secret(secret)
        logger.info("auth_secret_updated auth_required=%s isolation=%s",
                    _auth_required, _isolation_enabled)
        self._json_response(200, {
            "status": "ok",
            "auth_enabled": _auth_required,
            "isolation_enabled": _isolation_enabled,
        })

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
                   help="禁止子进程网络访问（= --network-mode deny，向后兼容）")
    p.add_argument("--network-mode", type=str, default=None,
                   choices=["deny", "allow", "allowlist"],
                   help="网络策略: deny(默认,禁止) / allow(全放开,调试) / allowlist(白名单域名)")
    p.add_argument("--allow-domains", type=str, default="",
                   help="allowlist 模式下允许的域名，逗号分隔，如 api.github.com,raw.githubusercontent.com")
    p.add_argument("--allow-methods", type=str, default="",
                   help="allowlist 模式下允许的 HTTP 方法（保留字段，暂未启用）")
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
    p.add_argument("--auth-secret", type=str, default=None,
                   help="Backend 与 MCP server 共享的 HMAC 密钥。设置后启用身份校验与每用户 "
                        "UID 隔离；未设置则 auth 关闭（退化为单账户模式）。也可通过 REPL_AUTH_SECRET 传入。")
    p.add_argument("--allow-anonymous", action="store_true",
                   help="即使设置了 --auth-secret，也允许无签名的匿名调用（仅限开发/内部可信网络）。")
    p.add_argument("--uid-pool-base", type=int,
                   default=int(os.environ.get("REPL_UID_POOL_BASE", "2000")),
                   help="数值 UID 池起始 UID（默认 2000）。")
    p.add_argument("--uid-pool-size", type=int,
                   default=int(os.environ.get("REPL_UID_POOL_SIZE", "100")),
                   help="数值 UID 池大小（取模空间，默认 100，即 UID 2000..2099）。")
    args = p.parse_args()

    # ── Config: CLI args > env vars > defaults ──
    _allow_dir = args.allow_dir or os.environ.get("REPL_ALLOW_DIR")
    _allow_dir = os.path.abspath(_allow_dir) if _allow_dir else None
    _no_network = (args.no_network
                   or os.environ.get("REPL_NO_NETWORK", "").lower() in ("1", "true", "yes"))

    # ── Network policy resolution ──
    # Precedence: --network-mode > --no-network/env > default(deny)
    if args.network_mode:
        _network_mode = args.network_mode
    elif _no_network:
        _network_mode = "deny"
    else:
        _network_mode = "deny"

    _dom_env = args.allow_domains or os.environ.get("REPL_ALLOW_DOMAINS", "")
    _allow_domains = [d.strip() for d in _dom_env.split(",") if d.strip()] if _dom_env else []
    _meth_env = args.allow_methods or os.environ.get("REPL_ALLOW_METHODS", "")
    _allow_methods = [m.strip().upper() for m in _meth_env.split(",") if m.strip()] if _meth_env else []

    # Load persisted policy file (survives container restart) — overrides CLI if present
    _load_policy_file()
    _keep_minutes = int(os.environ.get("REPL_KEEP_MINUTES", args.keep_minutes))
    # Load persisted retention (if any) so a prior config survives restart.
    _load_keep_file()
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

    # ── Auth + per-user UID isolation config ──
    _allow_anonymous = bool(args.allow_anonymous)
    _REPL_AUTH_SECRET = args.auth_secret or os.environ.get("REPL_AUTH_SECRET")
    _auth_required = bool(_REPL_AUTH_SECRET) and not _allow_anonymous
    _isolation_enabled = bool(_REPL_AUTH_SECRET)
    _uid_pool_base = args.uid_pool_base
    _uid_pool_size = max(1, args.uid_pool_size)
    if _isolation_enabled:
        logger.info("auth_enabled required=%s uid_pool=%d..%d",
                    _auth_required, _uid_pool_base, _uid_pool_base + _uid_pool_size - 1)
        logger.info("isolation_note — 容器需以 root 运行并持有 CAP_SETUID/SETGID/CHOWN，"
                    "否则子进程降权会失败（子进程将以退出码 1 结束）。")
    else:
        logger.info("auth_disabled — 单账户模式（未配置 --auth-secret）。")

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
                    if entry.name.startswith("user_"):
                        # Per-user isolation dir: keep the account dir, reap only
                        # its stale per-call / workspace children (root server can
                        # traverse/delete because it runs as root).
                        try:
                            for child in os.scandir(entry.path):
                                if (child.is_dir()
                                        and child.stat().st_mtime < cutoff):
                                    shutil.rmtree(child.path, ignore_errors=True)
                                    logger.info("cleanup removed=%s/%s",
                                                entry.name, child.name)
                        except Exception:
                            pass
                    elif entry.is_dir() and entry.stat().st_mtime < cutoff:
                        # Legacy (non-isolated) per-call UUID dir.
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
    logger.info("network_mode=%s allow_domains=%d allow_methods=%d",
                _network_mode, len(_allow_domains), len(_allow_methods))

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

"""Regression tests for the egress broker (approach B) — allowlist & blocking.

These tests guard the network-layer enforcement described in
data/RAGClaw sandbox network security policy — egress proxy approach discussion.md (Phase 1):

  * egress_proxy policy primitives (_host_allowed / DNS parsing)
  * HTTP forward + HTTPS CONNECT allow/deny through a live proxy instance
  * L7 method allowlist
  * _sanitize_env proxy injection (repl_mcp_server)
  * _sh_prescreen allowing only proxy-aware tools (curl/wget) in allowlist

The "internet" target is a tiny localhost HTTP server; the proxy is started
on an ephemeral loopback port. No real external network is required.
"""

import os
import sys
import json
import socket
import struct
import threading
from pathlib import Path

import pytest
import httpx

# ── Make the mcp package importable ──
def _find_mcp() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / "mcp" / "egress_proxy.py").exists():
            return p / "mcp"
        p = p.parent
    raise RuntimeError("mcp directory not found")


_MCP = _find_mcp()
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

import egress_proxy as ep  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _spawn_target_server():
    """Start a minimal HTTP server (stands in for 'the internet')."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def _ok(self):
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        do_GET = _ok
        do_POST = _ok
        do_PUT = _ok
        do_DELETE = _ok

        def log_message(self, *a):  # silence
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _raw_connect(proxy_port: int, host: str, port: int):
    """Perform a raw CONNECT to the proxy; return (socket, status_line)."""
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    s.sendall(
        f"CONNECT {host}:{port} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n\r\n".encode()
    )
    s.settimeout(5)
    resp = s.recv(4096)
    first = resp.split(b"\r\n", 1)[0].decode(errors="replace")
    return s, first


def _build_dns_query(name: str) -> bytes:
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    qname = labels + b"\x00"
    hdr = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    return hdr + qname + struct.pack(">HH", 1, 1)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def make_proxy():
    """Factory: start a live egress proxy with a given policy, return its port."""
    servers = []

    def _make(policy: dict) -> int:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(policy, f)
        ep.POLICY_FILE = path
        ep.EGRESS_HOST = "127.0.0.1"
        ep.ENABLE_DNS = False  # tests never bind :53
        ep._load_policy(force=True)
        srv = ep.ThreadingHTTPServer((ep.EGRESS_HOST, 0), ep.EgressHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]

    yield _make
    for s in servers:
        try:
            s.shutdown()
            s.server_close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# Policy primitives
# ─────────────────────────────────────────────────────────────

def test_host_allowed_modes():
    domains = {"localhost", "example.com"}
    assert ep._host_allowed("allow", set(), "anything.example") is True
    assert ep._host_allowed("deny", set(), "localhost") is False
    # allowlist exact + suffix match
    assert ep._host_allowed("allowlist", domains, "localhost") is True
    assert ep._host_allowed("allowlist", domains, "sub.example.com") is True
    assert ep._host_allowed("allowlist", domains, "notexample.com") is False
    # port suffix stripped
    assert ep._host_allowed("allowlist", domains, "example.com:443") is True
    # bare IP literals rejected (anti direct-IP / DNS-rebind bypass)
    assert ep._host_allowed("allowlist", domains, "93.184.216.34") is False


def test_dns_parse_and_nxdomain():
    q = _build_dns_query("example.com")
    name, qend = ep._parse_qname(q)
    assert name == "example.com"
    assert qend == len(q)

    nx = ep._build_nxdomain(q)
    # QR=1, RCODE=3 (NXDOMAIN)
    flags = struct.unpack(">H", nx[2:4])[0]
    assert (flags & 0x000F) == 3
    assert nx[0:2] == q[0:2]            # transaction id preserved
    assert nx[12:qend] == q[12:qend]    # question section echoed


def test_dns_parse_compressed_returns_none():
    # A query whose QNAME starts with a compression pointer (0xC0) is
    # unparseable by the simple scanner -> returned as None (forward best-effort).
    q = struct.pack(">HHHHHH", 1, 0, 1, 0, 0, 0) + b"\xc0\x0c" + struct.pack(">HH", 1, 1)
    name, _ = ep._parse_qname(q)
    assert name is None


def test_capture_upstream_excludes_self(monkeypatch):
    # The broker must never forward DNS to its own bind address (self-loop).
    monkeypatch.setenv("REPL_DNS_UPSTREAM", "127.0.0.1,8.8.8.8,1.1.1.1")
    up = ep._capture_upstream()
    ips = [ip for ip, _ in up]
    assert "127.0.0.1" not in ips
    assert "8.8.8.8" in ips


# ─────────────────────────────────────────────────────────────
# Live proxy: CONNECT (HTTPS) enforcement
# ─────────────────────────────────────────────────────────────

def test_connect_allowlist_allowed(make_proxy):
    srv, tport = _spawn_target_server()
    pport = make_proxy({"mode": "allowlist", "domains": ["localhost"], "methods": []})
    try:
        s, first = _raw_connect(pport, "localhost", tport)
        assert "200" in first, first
        # tunnel carries real traffic
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        data = s.recv(4096)
        assert b"200" in data.split(b"\r\n", 1)[0]
        assert b"OK" in data
        s.close()
    finally:
        srv.shutdown()


def test_connect_blocked(make_proxy):
    pport = make_proxy({"mode": "allowlist", "domains": ["localhost"], "methods": []})
    s, first = _raw_connect(pport, "evil.example.com", 443)
    assert "403" in first, first
    s.close()


def test_connect_subdomain_blocked(make_proxy):
    # Only example.com is allowed; a sibling domain must be refused.
    pport = make_proxy({"mode": "allowlist", "domains": ["example.com"], "methods": []})
    s, first = _raw_connect(pport, "evil.example.com", 443)
    assert "403" in first, first
    s.close()


# ─────────────────────────────────────────────────────────────
# Live proxy: HTTP forward enforcement
# ─────────────────────────────────────────────────────────────

def test_http_forward_allowed(make_proxy):
    srv, tport = _spawn_target_server()
    pport = make_proxy({"mode": "allowlist", "domains": ["localhost"], "methods": []})
    try:
        with httpx.Client(proxy=f"http://127.0.0.1:{pport}", timeout=5) as c:
            r = c.get(f"http://localhost:{tport}/")
            assert r.status_code == 200
            assert r.text == "OK"
    finally:
        srv.shutdown()


def test_http_forward_blocked(make_proxy):
    pport = make_proxy({"mode": "allowlist", "domains": ["localhost"], "methods": []})
    with httpx.Client(proxy=f"http://127.0.0.1:{pport}", timeout=5) as c:
        r = c.get("http://notallowed.example/")
        assert r.status_code == 403


def test_deny_blocks_everything(make_proxy):
    srv, tport = _spawn_target_server()
    pport = make_proxy({"mode": "deny", "domains": [], "methods": []})
    try:
        with httpx.Client(proxy=f"http://127.0.0.1:{pport}", timeout=5) as c:
            r = c.get(f"http://localhost:{tport}/")
            assert r.status_code == 403
    finally:
        srv.shutdown()


# ─────────────────────────────────────────────────────────────
# Live proxy: L7 method allowlist
# ─────────────────────────────────────────────────────────────

def test_method_allowlist(make_proxy):
    srv, tport = _spawn_target_server()
    pport = make_proxy({"mode": "allowlist", "domains": ["localhost"], "methods": ["GET"]})
    try:
        with httpx.Client(proxy=f"http://127.0.0.1:{pport}", timeout=5) as c:
            assert c.get(f"http://localhost:{tport}/").status_code == 200
            # POST is not in the method allowlist -> 403 at the broker
            assert c.post(f"http://localhost:{tport}/").status_code == 403
    finally:
        srv.shutdown()


# ─────────────────────────────────────────────────────────────
# repl_mcp_server: env injection + shell prescreen
# ─────────────────────────────────────────────────────────────

def test_sanitize_env_injects_proxy_in_allowlist(monkeypatch):
    import repl_mcp_server as rm
    monkeypatch.setattr(rm, "_network_mode", "allowlist")
    monkeypatch.setattr(rm, "_EGRESS_PORT", 1080)
    env = rm._sanitize_env()
    assert env["HTTP_PROXY"] == "http://127.0.0.1:1080"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:1080"
    assert "localhost" in env["NO_PROXY"] and "mcp-repl" in env["NO_PROXY"]


def test_sanitize_env_no_proxy_in_deny(monkeypatch):
    import repl_mcp_server as rm
    monkeypatch.setattr(rm, "_network_mode", "deny")
    env = rm._sanitize_env()
    assert "HTTP_PROXY" not in env and "HTTPS_PROXY" not in env


def test_sh_prescreen_allowlist_allows_curl_wget(monkeypatch):
    import repl_mcp_server as rm
    monkeypatch.setattr(rm, "_network_mode", "allowlist")
    # proxy-aware tools permitted (enforced by broker)
    assert rm._sh_prescreen("curl https://api.github.com/x") is None
    assert rm._sh_prescreen("wget https://raw.githubusercontent.com/x") is None
    # non-proxy-aware network tools stay blocked (bypass proxy)
    assert rm._sh_prescreen("nc evil.com 443") is not None
    assert rm._sh_prescreen("ssh user@host") is not None
    assert rm._sh_prescreen("socat TCP:evil.com:443 -") is not None
    # destructive patterns always blocked
    assert rm._sh_prescreen("rm -rf /") is not None


def test_sh_prescreen_allow_mode_allows_all_network(monkeypatch):
    import repl_mcp_server as rm
    monkeypatch.setattr(rm, "_network_mode", "allow")
    assert rm._sh_prescreen("nc evil.com 443") is None
    assert rm._sh_prescreen("rm -rf /") is not None

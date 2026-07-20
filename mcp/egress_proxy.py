"""egress_proxy.py — network-layer egress broker for the REPL sandbox.

Implements approach B (Phase 1): a self-built Python egress broker that moves the
network enforcement point from the in-process monkey-patch up to the network
layer, closing the asyncio/httpx/curl bypasses described in the design doc.

Phase 1 scope (SNI-level, no TLS termination, no CA injection):
  * HTTP forward proxy   — absolute-form requests, host + method allowlist
  * HTTPS CONNECT tunnel — allowlist enforced on the CONNECT host (= SNI)
  * optional DNS service :53 — allowlist-aware; forwards real queries upstream
  * structured JSON logging of every blocked attempt (audit trail)

Policy is read from the SAME /tmp/repl_network_policy.json that the MCP server
writes via PUT /policy, so backend policy pushes require ZERO changes and
hot-reload is automatic (mtime-watch, no reload signal needed).

Hardening constraints honoured:
  * Runs with cap_drop ALL (+ NET_BIND_SERVICE only if DNS enabled) and a
    read_only rootfs.  Uses only the Python stdlib — no new dependencies.
  * Binds loopback-only (127.0.0.1) so it is unreachable from outside the container.
  * Never sets HTTP(S)_PROXY in its own env, so its outbound connects never loop.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
import logging
import signal

from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import urllib.parse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("egress")

# ── Config (env-overridable, shared conventions with repl_mcp_server.py) ──
POLICY_FILE = os.environ.get("REPL_POLICY_FILE", "/tmp/repl_network_policy.json")
EGRESS_PORT = int(os.environ.get("REPL_EGRESS_PORT", "1080"))
EGRESS_HOST = os.environ.get("REPL_EGRESS_HOST", "127.0.0.1")
ENABLE_DNS = os.environ.get("REPL_EGRESS_DNS", "1") in ("1", "true", "yes")

# Make 100% sure the proxy never routes its own outbound through itself.
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

# ── In-process policy snapshot (hot-reloaded on file mtime change) ──
_policy_lock = threading.Lock()
_policy = {"mode": "deny", "domains": [], "methods": []}
_policy_mtime = 0.0
_shutdown = threading.Event()


def _load_policy(force: bool = False) -> None:
    global _policy, _policy_mtime
    try:
        mtime = os.path.getmtime(POLICY_FILE)
    except OSError:
        return
    if not force and mtime == _policy_mtime:
        return
    try:
        with open(POLICY_FILE, "r", encoding="utf-8") as f:
            p = json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("policy_load_failed %s", e)
        return
    with _policy_lock:
        _policy = {
            "mode": p.get("mode", "deny"),
            "domains": [d.lower() for d in p.get("domains", [])],
            "methods": [m.upper() for m in p.get("methods", [])],
        }
        _policy_mtime = mtime


def _get_policy():
    _load_policy()
    with _policy_lock:
        return _policy["mode"], set(_policy["domains"]), set(_policy["methods"])


def _is_ip(h: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, h)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        socket.inet_pton(socket.AF_INET6, h)
        return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _host_allowed(mode: str, domains: set, host) -> bool:
    """Mirror of _g_host_allowed in the Python guard preamble."""
    if not host:
        return False
    h = str(host).lower()
    if ":" in h:
        h = h.split(":")[0]
    if mode == "allow":
        return True
    if mode == "allowlist":
        # Reject bare IP literals (anti DNS-rebind / direct-IP bypass).
        if _is_ip(h):
            return False
        for d in domains:
            if h == d or h.endswith("." + d):
                return True
        return False
    # deny
    return False


def _log_blocked(kind: str, host, method: str = "", detail: str = "") -> None:
    rec = {
        "event": "egress_blocked",
        "kind": kind,            # connect | http | method | dns
        "host": str(host),
        "method": method,
        "detail": detail,
    }
    logger.info("egress_blocked %s", json.dumps(rec, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════
# HTTP forward proxy + HTTPS CONNECT tunnel
# ═══════════════════════════════════════════════════════════

def _tunnel(client, upstream):
    """Bidirectional blind copy between two sockets (CONNECT tunnel)."""
    def _pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:  # noqa: BLE001
                pass

    t1 = threading.Thread(target=_pump, args=(client, upstream), daemon=True)
    t2 = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


def _relay_response(upstream, wfile) -> None:
    """Copy an upstream HTTP response verbatim to the client socket."""
    try:
        while True:
            data = upstream.recv(65536)
            if not data:
                break
            wfile.write(data)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            wfile.flush()
        except Exception:  # noqa: BLE001
            pass


class EgressHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RAGCLAW-Egress/1.0"

    def _proxy_error(self, code: int, msg: str) -> None:
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _proxy_http(self, method: str) -> None:
        self.close_connection = True
        mode, domains, methods = _get_policy()

        # L7 method allowlist (Phase 2 reserved field, now usable).
        if methods and method.upper() not in methods:
            _log_blocked("method", self.host if hasattr(self, "host") else "",
                         method=method)
            self._proxy_error(403, "Egress blocked: method not allowed: %s" % method)
            return

        parsed = urllib.parse.urlparse(self.path)
        host = parsed.hostname
        self.host = host  # stash for logging
        if not host:
            self._proxy_error(400, "Egress: malformed proxy request")
            return
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if not _host_allowed(mode, domains, host):
            _log_blocked("http", host, method=method)
            self._proxy_error(403, "Egress blocked: host not in allowlist: %s" % host)
            return

        try:
            upstream = socket.create_connection((host, int(port)), timeout=15)
        except Exception as e:  # noqa: BLE001
            self._proxy_error(502, "Egress upstream connect failed: %s" % e)
            return

        # Build origin-form request and relay headers to upstream.
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        f = upstream.makefile("wb")
        try:
            f.write(("%s %s HTTP/1.1\r\n" % (method, path)).encode())
            for k, v in self.headers.items():
                lk = k.lower()
                if lk in ("proxy-connection", "proxy-authorization",
                          "keep-alive", "te", "trailers", "transfer-encoding"):
                    continue
                f.write(("%s: %s\r\n" % (k, v)).encode())
            f.write(("Host: %s\r\n" % host).encode())
            f.write(b"Connection: close\r\n")
            f.write(b"\r\n")
            # Relay request body (Content-Length only; chunked is rare for proxies).
            cl = self.headers.get("Content-Length")
            if cl:
                remaining = int(cl)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            f.flush()
        except Exception as e:  # noqa: BLE001
            self._proxy_error(502, "Egress upstream write failed: %s" % e)
            upstream.close()
            return

        logger.debug("egress_allow http %s -> %s", method, host)
        _relay_response(upstream, self.wfile)
        upstream.close()

    def do_CONNECT(self):
        self.close_connection = True
        host = self.path.split(":")[0]
        port = self.path.split(":")[1] if ":" in self.path else "443"

        mode, domains, _ = _get_policy()
        if not _host_allowed(mode, domains, host):
            _log_blocked("connect", host)
            self._proxy_error(403, "Egress blocked: host not in allowlist: %s" % host)
            return

        try:
            upstream = socket.create_connection((host, int(port)), timeout=15)
        except Exception as e:  # noqa: BLE001
            self._proxy_error(502, "Egress upstream connect failed: %s" % e)
            return

        self.send_response(200, "Connection Established")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()  # ensure 200 is on the wire before we tunnel raw bytes
        logger.debug("egress_allow connect %s:%s", host, port)
        _tunnel(self.connection, upstream)
        try:
            upstream.close()
        except Exception:  # noqa: BLE001
            pass

    # Route every standard method through the same forwarder.
    do_GET = lambda self: EgressHandler._proxy_http(self, "GET")
    do_POST = lambda self: EgressHandler._proxy_http(self, "POST")
    do_PUT = lambda self: EgressHandler._proxy_http(self, "PUT")
    do_DELETE = lambda self: EgressHandler._proxy_http(self, "DELETE")
    do_HEAD = lambda self: EgressHandler._proxy_http(self, "HEAD")
    do_PATCH = lambda self: EgressHandler._proxy_http(self, "PATCH")
    do_OPTIONS = lambda self: EgressHandler._proxy_http(self, "OPTIONS")

    def log_message(self, _fmt, *args):  # silence default request logging
        logger.debug("http %s", args[0] if args else "")


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ═══════════════════════════════════════════════════════════
# Optional DNS service (:53) — allowlist-aware, forwards upstream
# ═══════════════════════════════════════════════════════════

def _parse_qname(data: bytes):
    """Return (name, qend). name is None if compressed/unparseable."""
    i = 12
    labels = []
    while i < len(data):
        length = data[i]
        if length == 0:
            i += 1
            break
        if (length & 0xC0) == 0xC0:  # compression pointer — can't parse name
            return (None, i + 2 + 4)
        i += 1
        if i + length > len(data):
            return (None, len(data))
        labels.append(data[i:i + length].decode("ascii", "replace"))
        i += length
    return (".".join(labels), i + 4)  # + QTYPE(2) + QCLASS(2)


def _build_nxdomain(data: bytes) -> bytes:
    name, qend = _parse_qname(data)
    qd = struct.unpack(">H", data[4:6])[0]
    flags = 0x8183  # QR=1, RD=1, RA=1, RCODE=3 (NXDOMAIN)
    header = (data[0:2] + struct.pack(">H", flags)
              + struct.pack(">H", qd) + b"\x00\x00\x00\x00\x00\x00")
    return header + data[12:qend]


def _capture_upstream():
    """Real upstream resolvers the DNS service forwards to.

    Avoids a self-loop: we never forward to our own bind address, and the
    entrypoint passes the original nameservers via REPL_DNS_UPSTREAM so the
    capture is race-free against the /etc/resolv.conf rewrite.
    """
    env_up = os.environ.get("REPL_DNS_UPSTREAM", "")
    candidates = [u.strip() for u in env_up.split(",") if u.strip()]
    if not candidates:
        try:
            with open("/etc/resolv.conf") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2:
                            candidates.append(parts[1])
        except Exception:  # noqa: BLE001
            pass
    upstream = []
    for ip in candidates:
        if ip == EGRESS_HOST:  # never forward to ourselves
            continue
        upstream.append((ip, 53))
    if not upstream:
        upstream = [("8.8.8.8", 53), ("1.1.1.1", 53)]
    return upstream


class _DNSCore:
    def __init__(self, upstream):
        self.upstream = upstream

    def resolve(self, data: bytes):
        name, _ = _parse_qname(data)
        mode, domains, _ = _get_policy()
        if name is None:
            # compressed/unparseable query — forward best-effort
            return self._forward(data)
        if not _host_allowed(mode, domains, name):
            _log_blocked("dns", name)
            return _build_nxdomain(data)
        return self._forward(data) or _build_nxdomain(data)

    def _forward(self, data: bytes):
        for ip, port in self.upstream:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(5)
                s.sendto(data, (ip, port))
                resp, _ = s.recvfrom(65535)
                return resp
            except Exception:  # noqa: BLE001
                continue
            finally:
                try:
                    s.close()
                except Exception:  # noqa: BLE001
                    pass
        return None


def _dns_udp_loop(core, sock):
    while not _shutdown.is_set():
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except Exception:  # noqa: BLE001
            break
        try:
            resp = core.resolve(data)
            if resp:
                sock.sendto(resp, addr)
        except Exception:  # noqa: BLE001
            pass


def _dns_tcp_client(core, conn):
    try:
        hdr = conn.recv(2)
        if len(hdr) < 2:
            return
        length = struct.unpack(">H", hdr)[0]
        data = b""
        while len(data) < length:
            chunk = conn.recv(length - len(data))
            if not chunk:
                return
            data += chunk
        resp = core.resolve(data)
        if resp:
            conn.sendall(struct.pack(">H", len(resp)) + resp)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _dns_tcp_loop(core, lsock):
    while not _shutdown.is_set():
        try:
            conn, _ = lsock.accept()
        except socket.timeout:
            continue
        except Exception:  # noqa: BLE001
            break
        threading.Thread(target=_dns_tcp_client, args=(core, conn),
                         daemon=True).start()


def start_dns():
    upstream = _capture_upstream()
    logger.info("dns upstream=%s", upstream)
    core = _DNSCore(upstream)
    try:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind((EGRESS_HOST, 53))
        udp.settimeout(1.0)
        threading.Thread(target=_dns_udp_loop, args=(core, udp),
                         daemon=True).start()
    except Exception as e:  # noqa: BLE001
        logger.warning("dns_udp_bind_failed %s (continuing without DNS service)", e)
        return
    try:
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind((EGRESS_HOST, 53))
        tcp.listen(64)
        tcp.settimeout(1.0)
        threading.Thread(target=_dns_tcp_loop, args=(core, tcp),
                         daemon=True).start()
    except Exception as e:  # noqa: BLE001
        logger.warning("dns_tcp_bind_failed %s", e)


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    _load_policy(force=True)
    logger.info("starting egress proxy listen=%s:%d dns=%s policy=%s",
                EGRESS_HOST, EGRESS_PORT, ENABLE_DNS, POLICY_FILE)

    proxy = ThreadingHTTPServer((EGRESS_HOST, EGRESS_PORT), EgressHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    if ENABLE_DNS:
        start_dns()

    def _on_signal(signum, _frame):
        logger.info("signal=%d shutting_down", signum)
        _shutdown.set()
        proxy.shutdown()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        while not _shutdown.is_set():
            time.sleep(1)
    finally:
        proxy.server_close()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    main()

"""skill_secret_proxy.py — secret-zero injection proxy (Component A).

Runs INSIDE the ragclaw-egress container (dual-attached: internal network for the
REPL sandbox, public bridge for real egress). It is the ONLY place a skill API KEY
exists at runtime other than the backend's encrypted config.enc.

Trust boundary:
  * The REPL sandbox sends plain HTTP to http://ragclaw-egress:9090/<proxy_path>/<rest>.
    It carries NO API KEY — only the request the third-party CLI intended to make.
  * This proxy injects `Authorization: <header_format>` from its in-memory KEY map
    and forwards the request over real TLS to `<upstream_base>/<rest>`.
  * It DROPS any `Authorization` header the sandbox tried to send (defeats an LLM
    forging `--api_key`).

Wiring:
  * The backend pushes keys via `PUT /secret` (see backend/app/services/skill_secret.py).
    Keys live ONLY in this process's memory — never written to disk, never logged.
  * Upstream hosts are whitelisted implicitly: only registered `proxy_path`s are
    forwarded, so an arbitrary host/path cannot be proxied (no open-proxy).

Hardening (mirrors egress_proxy.py):
  * Python stdlib only (no third-party deps — keeps the egress image minimal).
  * Binds the internal host/port; not published to a host port.
  * Clears its own HTTP(S)_PROXY env so outbound connects never loop back.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import logging
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import http.client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("skill-secret-proxy")

# ── Config (env-overridable, shared conventions with egress_proxy.py) ──
HOST = os.environ.get("SKILL_SECRET_PROXY_HOST", "0.0.0.0")
PORT = int(os.environ.get("SKILL_SECRET_PROXY_PORT", "9090"))

# Per-folder sliding-window rate limit: max N requests per WINDOW_SECONDS.
_RATE_MAX = int(os.environ.get("SKILL_SECRET_RATE_MAX", "120"))
_RATE_WINDOW = float(os.environ.get("SKILL_SECRET_RATE_WINDOW", "60"))

# Make 100% sure the proxy never routes its own outbound through itself.
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)


# ── In-memory KEY map (the only runtime home of skill API keys) ──
# keyed by proxy_path -> {folder, upstream_base, header_format, api_key}
_SECRETS_LOCK = threading.Lock()
_SECRETS: dict[str, dict] = {}
_FOLDER_INDEX: dict[str, str] = {}  # folder -> proxy_path
_RATE: dict[str, deque] = {}  # folder -> timestamps


def _register(payload: dict) -> None:
    """Store a pushed secret. Raises ValueError on a malformed payload."""
    folder = payload.get("folder")
    proxy_path = payload.get("proxy_path") or folder
    upstream_base = payload.get("upstream_base")
    api_key = payload.get("api_key")
    if not folder or not proxy_path or not upstream_base or not api_key:
        raise ValueError("folder, proxy_path, upstream_base and api_key are required")
    parsed = urllib.parse.urlparse(upstream_base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"invalid upstream_base: {upstream_base}")
    with _SECRETS_LOCK:
        _SECRETS[proxy_path] = {
            "folder": folder,
            "upstream_base": upstream_base,
            "header_format": payload.get("header_format", "Bearer {}"),
            "api_key": api_key,
        }
        _FOLDER_INDEX[folder] = proxy_path
    logger.info("registered secret folder=%s proxy_path=%s upstream=%s",
                folder, proxy_path, upstream_base)


def _unregister(folder: str) -> bool:
    with _SECRETS_LOCK:
        proxy_path = _FOLDER_INDEX.pop(folder, None)
        if proxy_path:
            _SECRETS.pop(proxy_path, None)
            _RATE.pop(folder, None)
            return True
    return False


def _lookup(proxy_path: str):
    with _SECRETS_LOCK:
        return _SECRETS.get(proxy_path)


def _rate_ok(folder: str) -> bool:
    """Sliding-window limiter; returns False when the folder is over quota."""
    now = time.monotonic()
    with _SECRETS_LOCK:
        dq = _RATE.setdefault(folder, deque())
        while dq and now - dq[0] > _RATE_WINDOW:
            dq.popleft()
        if len(dq) >= _RATE_MAX:
            return False
        dq.append(now)
    return True


def _forward(upstream_base: str, rest: str, method: str, headers: dict, body: bytes):
    """Forward to the real upstream over TLS and return (status, resp_headers, resp_body)."""
    parsed = urllib.parse.urlparse(upstream_base)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    target_path = (parsed.path.rstrip("/") + "/" + rest.lstrip("/")).rstrip("/") or "/"
    conn = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    c = conn(host, port, timeout=30)
    try:
        c.request(method, target_path, body=body or None, headers=headers)
        resp = c.getresponse()
        resp_body = resp.read()
        return resp.status, dict(resp.getheaders()), resp_body
    finally:
        c.close()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Silence the default stderr request logging; we log only meaningful events.
    def log_message(self, *args):
        pass

    def _send(self, code: int, payload: bytes | None = None, headers: dict | None = None):
        self.send_response(code)
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        if payload is not None:
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_header("Content-Length", "0")
            self.end_headers()

    # ── PUT /secret — backend pushes a KEY (in-memory only) ──
    def do_PUT(self):
        if self.path.rstrip("/") != "/secret":
            self._send(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw or b"{}")
            _register(payload)
        except (ValueError, json.JSONDecodeError) as e:
            self._send(400, (f"bad secret payload: {e}").encode())
            return
        self._send(200, b'{"status":"ok"}', {"Content-Type": "application/json"})

    # ── DELETE /secret?folder=<folder> — backend clears a KEY ──
    def do_DELETE(self):
        if self.path.split("?")[0].rstrip("/") != "/secret":
            self._send(404)
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        folder = (qs.get("folder") or [None])[0]
        if not folder or not _unregister(folder):
            self._send(404, b'{"status":"not_found"}', {"Content-Type": "application/json"})
            return
        self._send(200, b'{"status":"ok"}', {"Content-Type": "application/json"})

    # ── Anything else: inject the KEY and forward to the real upstream ──
    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        self._proxy("GET")

    def do_PUT_generic(self):
        self._proxy("PUT")

    def _proxy(self, method: str):
        path = self.path.split("?")[0]
        parts = [p for p in path.split("/") if p != ""]
        if not parts:
            self._send(403, b"forbidden: unknown proxy path")
            return
        proxy_path = parts[0]
        rest = "/" + "/".join(parts[1:])
        secret = _lookup(proxy_path)
        if secret is None:
            # Whitelist enforcement: only registered proxy_paths are forwarded.
            logger.warning("denied unregistered proxy_path=%s", proxy_path)
            self._send(403, b"forbidden: unregistered skill proxy path")
            return
        folder = secret["folder"]
        if not _rate_ok(folder):
            self._send(429, b"rate limited")
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""

        # Build upstream headers: copy caller headers but DROP any Authorization
        # (the sandbox must never choose the KEY) and Host (recomputed upstream).
        fwd = {}
        for k, v in self.headers.items():
            if k.lower() in ("authorization", "host", "content-length", "connection"):
                continue
            fwd[k] = v
        fwd["Authorization"] = secret["header_format"].format(secret["api_key"])
        fwd["Host"] = urllib.parse.urlparse(secret["upstream_base"]).hostname or ""

        try:
            status, resp_headers, resp_body = _forward(
                secret["upstream_base"], rest, method, fwd, body
            )
        except Exception as e:
            logger.warning("upstream forward failed folder=%s: %s", folder, e)
            self._send(502, b"bad gateway")
            return

        out = {k: v for k, v in resp_headers.items()
               if k.lower() not in ("transfer-encoding", "connection")}
        self._send(status, resp_body, out)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = _ThreadingHTTPServer((HOST, PORT), _Handler)
    logger.info("skill-secret injection proxy listening on %s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

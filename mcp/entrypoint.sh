#!/bin/sh
# REPL MCP Server entrypoint — Python / Shell / JavaScript sandbox
mkdir -p /app/workspace
# v2: /app/workspace is now a persistent named volume (erag_workspace) that may
# be pre-populated from the image with mcpuser ownership. The server runs as
# root (compose user:"0"), so ensure root owns the mountpoint — otherwise root is
# denied write under Docker Desktop/WSL2 user-remap and cannot create per-user
# workdirs. No-op when already root-owned.
chown root:root /app/workspace

# ── Approach B (network-layer): the egress broker now runs in a SEPARATE
# container (erag-egress) on the internal Docker network. This sandbox
# container is attached ONLY to erag-internal (internal: true), which has NO
# default route — so any direct egress to the internet fails at the routing
# layer, regardless of client proxy compliance or the in-process guard.
#
# All HTTP(S) egress is forced through the broker via the HTTP(S)_PROXY env
# injected into children (see repl_mcp_server._sanitize_env). We only need to
# point DNS at the broker's built-in allowlist-aware DNS service
# (REPL_EGRESS_HOST on the internal network). The broker container itself has
# a public default route (it is also attached to the external bridge), so it
# can forward allowed traffic.
#
# NOTE: /etc/resolv.conf is a Docker bind-mount and usually stays writable
# under a read_only rootfs; if it isn't, skip silently (the broker is still
# reachable by IP).
if [ -w /etc/resolv.conf ]; then
  printf 'nameserver %s\noptions ndots:0\n' "${REPL_EGRESS_HOST:-127.0.0.1}" > /etc/resolv.conf
fi

# python_repl_mcp_server.py delegates to repl_mcp_server.py (backward compat)
exec python python_repl_mcp_server.py "$@"

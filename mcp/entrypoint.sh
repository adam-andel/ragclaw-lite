#!/bin/sh
# REPL MCP Server entrypoint — Python / Shell / JavaScript sandbox
mkdir -p /app/workspace

# ── Approach B: start the egress broker in the background ──
# It reads the SAME /tmp/repl_network_policy.json the MCP server writes via
# PUT /policy, so backend policy pushes need no extra glue and hot-reload is
# automatic. The proxy binds loopback (127.0.0.1) only.
python egress_proxy.py &
# (backgrounded before exec; becomes a child of PID 1 and keeps running)

# Capture the ORIGINAL upstream nameservers before we rewrite resolv.conf, so
# the proxy's built-in DNS service can forward real queries without looping.
ORIG_NS=$(awk '/^nameserver/ {print $2}' /etc/resolv.conf 2>/dev/null | tr '\n' ',' | sed 's/,$//')
export REPL_DNS_UPSTREAM="${ORIG_NS:-8.8.8.8,1.1.1.1}"

# Rewrite DNS to point at the proxy's built-in allowlist-aware DNS service.
# /etc/resolv.conf is a Docker bind-mount, so it stays writable even under a
# read_only rootfs. Only the loopback resolver is referenced, so external
# containers are unaffected.
if [ "${REPL_EGRESS_DNS:-1}" != "0" ]; then
  printf 'nameserver 127.0.0.1\noptions ndots:0\n' > /etc/resolv.conf
fi

# python_repl_mcp_server.py delegates to repl_mcp_server.py (backward compat)
exec python python_repl_mcp_server.py "$@"

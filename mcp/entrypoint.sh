#!/bin/sh
# REPL MCP Server entrypoint — Python / Shell / JavaScript sandbox
mkdir -p /app/workspace

# python_repl_mcp_server.py delegates to repl_mcp_server.py (backward compat)
exec python python_repl_mcp_server.py "$@"

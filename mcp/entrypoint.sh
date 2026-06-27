#!/bin/sh
# Entrypoint: ensure workspace directory exists after tmpfs mount
mkdir -p /app/workspace

# Run the MCP server with all arguments passed through
exec python python_repl_mcp_server.py "$@"

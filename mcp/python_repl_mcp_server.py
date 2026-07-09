"""Backward-compatible wrapper — delegates to repl_mcp_server.py.

This file exists so existing Dockerfiles, entrypoint.sh, and mcp_repl.ps1
continue to work without changes. All new features (Shell/JS support) are
in repl_mcp_server.py.

Usage (unchanged):
  python python_repl_mcp_server.py --port 9200 --allow-dir /app/workspace
"""

import os
import sys

# Find repl_mcp_server.py in the same directory
_here = os.path.dirname(os.path.abspath(__file__))
_repl = os.path.join(_here, "repl_mcp_server.py")

if not os.path.exists(_repl):
    print("ERROR: repl_mcp_server.py not found in", _here, file=sys.stderr)
    sys.exit(1)

# Load and run the real server
with open(_repl, "rb") as f:
    code = compile(f.read(), _repl, "exec")

# Forward all CLI args, including new --enable-shell / --enable-javascript
sys.argv[0] = _repl
exec(code, {"__name__": "__main__", "__file__": _repl})

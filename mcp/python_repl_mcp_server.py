# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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

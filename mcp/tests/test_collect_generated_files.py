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
"""Unit tests for the structured file-ref channel — sandbox (MCP server) side.

Covers mcp/repl_mcp_server._collect_generated_files — the MCP server's side of
the channel: diffing the workspace snapshot and emitting uid-free
{name, path, mimeType} entries (the old [File]/[workspace:] text tags are gone).

The Backend's consumer side (MCPClient._call_tool_http parsing
structuredContent.files) is tested separately under
backend/tests/unit/test_mcp_client_structured_content.py.

This file runs inside the mcp-repl dev container, where the server source lives
at /app (next to this tests/ dir), so it can import the server module directly.
"""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

# In the mcp-repl dev container this file is at /app/tests/... and the server
# source (repl_mcp_server.py) is at /app, so the mcp root is the parent dir.
MCP_SERVER_PATH = Path(__file__).resolve().parents[1] / "repl_mcp_server.py"


@pytest.fixture(scope="module")
def repl_server():
    """Load mcp/repl_mcp_server.py as a standalone module (no package needed).

    The server module is stdlib-only at import time (the HTTP server is only
    started under ``if __name__ == "__main__"``), so loading it directly is safe
    and keeps these tests independent of the Backend package layout.
    """
    spec = importlib.util.spec_from_file_location(
        "repl_mcp_server_under_test", MCP_SERVER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(root: Path, rel: str, content: str = "x") -> Path:
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_collect_includes_new_files_and_strips_uid_prefix(repl_server):
    with tempfile.TemporaryDirectory() as root:
        allow = Path(root) / "allow"
        allow.mkdir()
        # Per-user nested sandbox: allow/user_u1234/dir1
        workdir = allow / "user_u1234" / "dir1"
        workdir.mkdir(parents=True)

        repl_server._allow_dir = str(allow)
        before = repl_server._snapshot_files(str(workdir))  # empty before run
        _write(workdir, "a.txt", "hello")
        _write(workdir, "sub/b.txt", "world")

        files = repl_server._collect_generated_files(str(workdir), before)

        assert {f["name"] for f in files} == {"a.txt", "b.txt"}
        by_name = {f["name"]: f for f in files}
        # uid prefix (user_u1234/) must be stripped -> sandbox-relative path
        assert by_name["a.txt"]["path"] == "dir1/a.txt"
        assert by_name["b.txt"]["path"] == "dir1/sub/b.txt"
        assert by_name["a.txt"]["mimeType"] == "text/plain"


def test_collect_keeps_non_uid_path_when_no_prefix(repl_server):
    with tempfile.TemporaryDirectory() as root:
        allow = Path(root) / "allow"
        allow.mkdir()
        workdir = allow / "ws"  # no per-user prefix
        workdir.mkdir()

        repl_server._allow_dir = str(allow)
        before = repl_server._snapshot_files(str(workdir))
        _write(workdir, "report.txt")

        files = repl_server._collect_generated_files(str(workdir), before)
        assert files == [{"name": "report.txt", "path": "ws/report.txt",
                          "mimeType": "text/plain"}]


def test_collect_excludes_unchanged_files(repl_server):
    with tempfile.TemporaryDirectory() as root:
        allow = Path(root) / "allow"
        allow.mkdir()
        workdir = allow / "ws"
        workdir.mkdir()

        repl_server._allow_dir = str(allow)
        _write(workdir, "old.txt", "keep")
        before = repl_server._snapshot_files(str(workdir))  # snapshot WITH old.txt
        _write(workdir, "new.txt", "fresh")  # a NEW file appears after snapshot

        files = repl_server._collect_generated_files(str(workdir), before)
        assert {f["name"] for f in files} == {"new.txt"}


def test_collect_without_before_includes_everything(repl_server):
    with tempfile.TemporaryDirectory() as root:
        allow = Path(root) / "allow"
        allow.mkdir()
        workdir = allow / "ws"
        workdir.mkdir()

        repl_server._allow_dir = str(allow)
        _write(workdir, "x.txt")
        _write(workdir, "y.txt")

        files = repl_server._collect_generated_files(str(workdir), None)
        assert {f["name"] for f in files} == {"x.txt", "y.txt"}


def test_collect_returns_empty_when_allow_dir_unset(repl_server):
    saved = repl_server._allow_dir
    try:
        repl_server._allow_dir = ""
        with tempfile.TemporaryDirectory() as root:
            _write(Path(root), "z.txt")
            assert repl_server._collect_generated_files(root, None) == []
    finally:
        repl_server._allow_dir = saved


def test_collect_mime_fallback_for_unknown_extension(repl_server):
    with tempfile.TemporaryDirectory() as root:
        allow = Path(root) / "allow"
        allow.mkdir()
        workdir = allow / "ws"
        workdir.mkdir()

        repl_server._allow_dir = str(allow)
        before = repl_server._snapshot_files(str(workdir))
        _write(workdir, "data.unknownext", "blob")

        files = repl_server._collect_generated_files(str(workdir), before)
        assert len(files) == 1
        # Unknown extension -> octet-stream fallback (never None/empty)
        assert files[0]["mimeType"] == "application/octet-stream"

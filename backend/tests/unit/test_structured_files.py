"""Unit tests for the structured file-ref channel (no [File] regex).

Covers:
  1. mcp/repl_mcp_server._collect_generated_files — the MCP server's side of
     the channel: diffing the workspace snapshot and emitting uid-free
     {name, path, mimeType} entries (the old [File]/[workspace:] text tags
     are gone).
  2. app/services/mcp_client.MCPClient._call_tool_http — the Backend's side:
     parsing `structuredContent.files` out of the MCP CallToolResult so the
     file metadata survives into ToolResult.files instead of being dropped.
"""
from __future__ import annotations

import asyncio
import importlib.util
import tempfile
from pathlib import Path

import pytest

from app.services.mcp_client import MCPClient, ToolResult

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_PATH = REPO_ROOT / "mcp" / "repl_mcp_server.py"


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


def _fake_post_returning(canned):
    """Build an async stand-in for MCPClient._http_post that returns `canned`."""
    async def _post(*_a, **_k):
        return canned
    return _post


# ─────────────────────────────────────────────────────────────────────────────
# MCP server: _collect_generated_files
# ─────────────────────────────────────────────────────────────────────────────

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
        # uid prefix (user_u1234/) must be stripped → sandbox-relative path
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
        # Unknown extension → octet-stream fallback (never None/empty)
        assert files[0]["mimeType"] == "application/octet-stream"


# ─────────────────────────────────────────────────────────────────────────────
# Backend: MCPClient._call_tool_http structuredContent parsing
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_tool_http_parses_structured_content_files():
    client = MCPClient()
    canned = {
        "result": {
            "content": [{"type": "text", "text": "code output here"}],
            "structuredContent": {
                "files": [
                    {"name": "a.txt", "path": "dir1/a.txt", "mimeType": "text/plain"},
                    {"name": "b.csv", "path": "dir1/b.csv", "mimeType": "text/csv"},
                ]
            },
        }
    }
    client._http_post = _fake_post_returning(canned)  # type: ignore[assignment]

    result = await client._call_tool_http(
        {"endpoint": "http://repl:9200"}, "run_python", {"code": "..."}, 30
    )

    assert result.ok is True
    assert result.result == "code output here"
    assert result.files == [
        {"name": "a.txt", "path": "dir1/a.txt", "mimeType": "text/plain"},
        {"name": "b.csv", "path": "dir1/b.csv", "mimeType": "text/csv"},
    ]


@pytest.mark.asyncio
async def test_call_tool_http_no_structured_content_yields_empty_files():
    client = MCPClient()
    canned = {"result": {"content": [{"type": "text", "text": "no files"}]}}
    client._http_post = _fake_post_returning(canned)  # type: ignore[assignment]

    result = await client._call_tool_http(
        {"endpoint": "http://repl:9200"}, "run_python", {"code": "..."}, 30
    )

    assert result.ok is True
    assert result.result == "no files"
    assert result.files == []


@pytest.mark.asyncio
async def test_call_tool_http_structured_content_missing_files_key():
    client = MCPClient()
    canned = {"result": {"content": [{"type": "text", "text": "x"}],
                         "structuredContent": {"other": 1}}}
    client._http_post = _fake_post_returning(canned)  # type: ignore[assignment]

    result = await client._call_tool_http(
        {"endpoint": "http://repl:9200"}, "run_python", {"code": "..."}, 30
    )

    assert result.ok is True
    assert result.files == []


@pytest.mark.asyncio
async def test_call_tool_http_structured_content_non_dict_is_ignored():
    client = MCPClient()
    # Malformed structuredContent (a list, not a dict) must not crash and must
    # not leak into files.
    canned = {"result": {"content": [{"type": "text", "text": "x"}],
                         "structuredContent": ["unexpected"]}}
    client._http_post = _fake_post_returning(canned)  # type: ignore[assignment]

    result = await client._call_tool_http(
        {"endpoint": "http://repl:9200"}, "run_python", {"code": "..."}, 30
    )

    assert result.ok is True
    assert result.files == []


@pytest.mark.asyncio
async def test_call_tool_http_empty_content_falls_back_to_json():
    client = MCPClient()
    canned = {"result": {"content": [], "structuredContent": {"files": []}}}
    client._http_post = _fake_post_returning(canned)  # type: ignore[assignment]

    result = await client._call_tool_http(
        {"endpoint": "http://repl:9200"}, "run_python", {"code": "..."}, 30
    )

    assert result.ok is True
    assert result.files == []


def test_tool_result_default_files_is_none():
    tr = ToolResult(tool_name="x", ok=True, result="y")
    assert tr.files is None

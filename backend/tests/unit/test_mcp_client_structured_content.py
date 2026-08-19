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
"""Tests for the Backend side of the structured file-reference channel.

Backend's MCPClient._call_tool_http must parse ``structuredContent.files`` out of
the MCP JSON-RPC CallToolResult so file metadata survives into ToolResult.files
instead of being dropped. (The sandbox-side collector,
repl_mcp_server._collect_generated_files, is tested under mcp/tests/ — next to
the server code it exercises.)
"""
from __future__ import annotations

import asyncio
import pytest

from app.services.mcp_client import MCPClient, ToolResult


def _fake_post_returning(canned):
    """Build an async stand-in for MCPClient._http_post that returns `canned`."""
    async def _post(*_a, **_k):
        return canned
    return _post


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

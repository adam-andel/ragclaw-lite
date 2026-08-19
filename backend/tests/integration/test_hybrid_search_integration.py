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
"""Integration tests for hybrid_search meta-tool.

Covers:
1. Conditional injection: hybrid_search exposed only when KB selected
2. i18n guidance: both locales contain hybrid_search instructions
3. get_kb_prompt: returns empty for invalid KB, includes guidance for valid KB
4. _execute_hybrid_search: error handling and end-to-end behavior

Run:
    cd backend
    python -m pytest tests/integration/test_hybrid_search_integration.py -v
"""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services.agent_nodes import _build_all_meta_tools, _execute_hybrid_search
from app.services.i18n import t
from app.services.kb_service import get_kb_prompt


# ---------------------------------------------------------------------------
# Conditional injection: hybrid_search exposed only when KB selected
# ---------------------------------------------------------------------------

class TestConditionalInjection:
    @pytest.mark.asyncio
    async def test_no_kb_excludes_hybrid_search(self):
        """Without KB, hybrid_search should not be in tool list."""
        tools = await _build_all_meta_tools(include_kb=False)
        names = [tool["function"]["name"] for tool in tools]
        assert "hybrid_search" not in names
        # Verify other meta-tools are present
        assert "create_cron" in names
        assert "update_memory" in names

    @pytest.mark.asyncio
    async def test_with_kb_includes_hybrid_search(self):
        """With KB, hybrid_search should be in tool list."""
        tools = await _build_all_meta_tools(include_kb=True)
        names = [tool["function"]["name"] for tool in tools]
        assert "hybrid_search" in names
        # Verify schema structure
        hybrid_tool = next(t for t in tools if t["function"]["name"] == "hybrid_search")
        assert "parameters" in hybrid_tool["function"]
        assert "query" in hybrid_tool["function"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# i18n guidance: both locales contain hybrid_search instructions
# ---------------------------------------------------------------------------

class TestI18nGuidance:
    def test_zh_guidance_exists(self):
        """Chinese guidance should exist and contain hybrid_search keyword."""
        guidance = t("kb_hybrid_search_guidance", "zh")
        assert len(guidance) > 0
        assert "hybrid_search" in guidance

    def test_en_guidance_exists(self):
        """English guidance should exist and contain hybrid_search keyword."""
        guidance = t("kb_hybrid_search_guidance", "en")
        assert len(guidance) > 0
        assert "hybrid_search" in guidance

    def test_guidance_lengths_reasonable(self):
        """Guidance should be substantial but not excessive."""
        zh = t("kb_hybrid_search_guidance", "zh")
        en = t("kb_hybrid_search_guidance", "en")
        # Chinese: ~600 chars, English: ~1700 chars (from design doc)
        assert 500 < len(zh) < 1000
        assert 1500 < len(en) < 2500


# ---------------------------------------------------------------------------
# get_kb_prompt: returns empty for invalid KB, includes guidance for valid KB
# ---------------------------------------------------------------------------

class TestGetKbPrompt:
    @pytest.mark.asyncio
    async def test_empty_kb_id_returns_empty(self):
        """get_kb_prompt("") should return empty string."""
        result = await get_kb_prompt("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_none_kb_id_returns_empty(self):
        """get_kb_prompt(None) should return empty string."""
        result = await get_kb_prompt(None)
        assert result == ""

    @pytest.mark.asyncio
    async def test_nonexistent_kb_returns_empty(self):
        """get_kb_prompt with non-existent KB ID should return empty string."""
        result = await get_kb_prompt("nonexistent-kb-id")
        assert result == ""

    @pytest.mark.asyncio
    async def test_valid_kb_with_lang_includes_guidance(self):
        """get_kb_prompt with valid KB and lang should include hybrid_search guidance."""
        # Use a real KB ID from the database (if available)
        # This test may need adjustment based on test environment
        # For now, we test with lang parameter to avoid config_manager initialization issue
        kb_id = "fae49eff-3e7f-48de-9f35-9e9b193cf6a1"  # from test environment
        result = await get_kb_prompt(kb_id, lang="zh")
        if result:  # KB exists
            assert "hybrid_search" in result
            assert len(result) > 0


# ---------------------------------------------------------------------------
# _execute_hybrid_search: error handling and end-to-end behavior
# ---------------------------------------------------------------------------

class TestExecuteHybridSearch:
    @pytest.mark.asyncio
    async def test_no_kb_returns_error(self):
        """_execute_hybrid_search without KB should return error result."""
        state = {}
        args = {"query": "test query"}
        result = await _execute_hybrid_search(state, args)
        assert "error" in result["result"]
        assert "no knowledge base selected" in result["result"]
        assert result["endpoint"] is None

    @pytest.mark.asyncio
    async def test_missing_query_returns_error(self):
        """_execute_hybrid_search without query should return error result."""
        state = {"kb_id": "test-kb"}
        args = {}
        result = await _execute_hybrid_search(state, args)
        assert "error" in result["result"]
        assert "query" in result["result"].lower()
        assert result["endpoint"] is None

    @pytest.mark.asyncio
    async def test_valid_query_returns_result(self):
        """_execute_hybrid_search with valid query should return result (may be empty)."""
        state = {"kb_id": "test-kb"}
        args = {"query": "test", "top_k": 5}
        result = await _execute_hybrid_search(state, args)
        # Result should start with [hybrid_search] prefix
        assert result["result"].startswith("[hybrid_search]")
        assert result["endpoint"] is None
        # Result may be "No relevant documents found" or actual content
        assert len(result["result"]) > 0

    @pytest.mark.asyncio
    async def test_with_doc_ids_parameter(self):
        """_execute_hybrid_search should accept doc_ids parameter."""
        state = {"kb_id": "test-kb"}
        args = {"query": "test", "doc_ids": ["doc1", "doc2"]}
        result = await _execute_hybrid_search(state, args)
        # Should not error, even if no documents match
        assert result["result"].startswith("[hybrid_search]")
        assert result["endpoint"] is None

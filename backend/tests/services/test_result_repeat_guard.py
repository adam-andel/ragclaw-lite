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
"""Result-level repeat guard: the model re-invokes a tool with slightly
different arguments but keeps getting back the *same* answer (e.g. asking for
the working directory and running os.getcwd() three times). The argument-based
degenerate-loop guard misses this because the code text differs; the
result-level guard must catch it and force a stop so the final answer is
produced instead of looping until the round quota.
"""

import pytest
from unittest.mock import AsyncMock

from app.services import agent_nodes as an


def _state_with_results(results, tool_calls=None):
    """Build a minimal state whose tool_messages hold `results` (most-recent
    last), each preceded by an assistant tool_calls message. `tool_calls` is the
    NEW call the model would make this round (the guard must reject it)."""
    tool_messages = []
    for i, r in enumerate(results):
        tool_messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": f"call_{i}", "type": "function",
                            "function": {"name": "run_python",
                                         "arguments": f'{{"code": "variant_{i}"}}'}}],
        })
        tool_messages.append({
            "role": "tool", "tool_call_id": f"call_{i}", "name": "run_python",
            "content": f"[run_python] {r}",
        })
    return {
        "query": "what is the current working directory",
        "kb_id": "kb-123",
        "final_answer": "",
        "cache_hit": False,
        "citations": [],
        "active_skill": {},
        "available_tools": [{
            "type": "function", "function": {
                "name": "run_python",
                "description": "Run python",
                "parameters": {"type": "object", "properties": {}},
            }
        }],
        "rag_context": "",
        "tool_results": [f"[run_python] {r}" for r in results],
        "tool_messages": tool_messages,
        "download_entries": [],
        "skill_stack": [],
        "loaded_skill_ids": [],
        "subdir": "ws-1",
        "skill_switch_count": 0,
        "tool_round": 3,
        "skill_switch_quota": 1,
        "tool_round_quota": 10,
        "_pending_new_tool_calls": tool_calls,
    }


@pytest.mark.asyncio
async def test_result_repeat_forces_stop(monkeypatch):
    """Three identical results across rounds → guard trips, no new tool call."""
    # The model would re-issue run_python (different code, same answer).
    new_call = [{"id": "call_new", "type": "function",
                 "function": {"name": "run_python",
                              "arguments": '{"code": "print(os.getcwd())"}'}}]

    async def fake_tools(*a, **k):
        return {"tool_calls": new_call, "content": ""}

    monkeypatch.setattr(an, "_chat_with_tools_resilient", fake_tools)

    # tool_decision_node assembles KB + skill-catalogue prompts via the DB; this
    # test only exercises the result-level repeat guard, so stub those lookups
    # (the isolated test DB has no tables). get_kb_prompt is tolerant, but
    # _build_skill_catalogue_prompt queries the skills table unguarded.
    monkeypatch.setattr(an, "get_kb_prompt", AsyncMock(return_value=""))
    monkeypatch.setattr(an, "_build_skill_catalogue_prompt", AsyncMock(return_value=""))

    state = _state_with_results(
        ["/app/workspace/user_u10000/test_gen_file"] * 3,
        tool_calls=new_call,
    )
    out = await an.tool_decision_node(state)
    assert out.get("tool_calls") is None, (
        "result-level repeat guard should force stop, got tool_calls=%r" % out.get("tool_calls"))


@pytest.mark.asyncio
async def test_distinct_results_allow_continue(monkeypatch):
    """Different results each round → no loop, model may continue."""
    new_call = [{"id": "call_new", "type": "function",
                 "function": {"name": "run_python",
                              "arguments": '{"code": "print(1)"}'}}]

    async def fake_tools(*a, **k):
        return {"tool_calls": new_call, "content": ""}

    monkeypatch.setattr(an, "_chat_with_tools_resilient", fake_tools)

    # tool_decision_node assembles KB + skill-catalogue prompts via the DB; this
    # test only exercises the result-level repeat guard, so stub those lookups
    # (the isolated test DB has no tables). get_kb_prompt is tolerant, but
    # _build_skill_catalogue_prompt queries the skills table unguarded.
    monkeypatch.setattr(an, "get_kb_prompt", AsyncMock(return_value=""))
    monkeypatch.setattr(an, "_build_skill_catalogue_prompt", AsyncMock(return_value=""))

    # 3 distinct results → not a repeat.
    state = _state_with_results(
        ["/app/a", "/app/b", "/app/c"],
        tool_calls=new_call,
    )
    out = await an.tool_decision_node(state)
    assert out.get("tool_calls") is not None, (
        "distinct results should NOT trip the guard")


def test_tool_result_signature_strips_prefix():
    assert an._tool_result_signature("[run_python] /app/x") == "/app/x"
    assert an._tool_result_signature("  [run_python] /app/x  ") == "/app/x"
    assert an._tool_result_signature("") == ""

"""Unit tests for agent_step SSE streaming (Route D observability).

Asserts that the key agent-graph nodes emit the correct ``agent_step``
events via the ``emit`` callback wired into state. Heavy dependencies
(DB / filesystem / LLM / MCP) are mocked so the test runs in isolation.

Run:
    cd backend
    python -m pytest tests/unit/test_agent_steps.py -q
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services import agent_nodes as nodes
from app.services.agent_nodes import (
    _emit,
    skill_loader_node,
    skill_switcher_node,
    parallel_retrieval_node,
    tool_executor_node,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class _FakeSession:
    """Minimal async context-manager standing in for async_session()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSkill:
    def __init__(self, name="文档生成助手", sid="skill-doc", folder_name="doc_gen"):
        self.id = sid
        self.name = name
        self.description = "生成文档"
        self.folder_name = folder_name
        self.is_active = True


def make_collector():
    steps = []

    def emit(stage: str, message: str, **extra):
        steps.append({"stage": stage, "message": message, **extra})

    return steps, emit


@pytest.fixture
def patch_globals(monkeypatch):
    """Mock the heavy external dependencies used by the nodes under test."""
    steps, emit = make_collector()

    # DB session (entered by skill_switcher even on non-DB paths)
    monkeypatch.setattr(nodes, "async_session", _FakeSession)

    # Skill body loading — return a dummy prompt + empty tool list
    async def fake_load(folder_name):
        return ("SYS_PROMPT", [])

    monkeypatch.setattr(nodes, "_load_skill_body_and_tools", fake_load)

    # Skill lookup — return a fake skill
    async def fake_get_skill(db, name, tenant_id):
        return _FakeSkill(name=name)

    monkeypatch.setattr(nodes, "get_skill_by_name", fake_get_skill)

    # Retrieval backend
    def fake_search(kb_id, query):
        return [
            {"content": "c1", "doc_name": "d1", "doc_id": "id1",
             "chunk_index": 0, "fusion_score": 0.9, "heading": "", "page": None},
            {"content": "c2", "doc_name": "d2", "doc_id": "id2",
             "chunk_index": 1, "fusion_score": 0.8, "heading": "", "page": None},
        ]

    monkeypatch.setattr(nodes.hybrid_search, "search", fake_search)

    # Tool executor backend (script path returns a [File] result)
    fake_repl = types.SimpleNamespace(
        ok=True,
        result="[File] https://example.com/files/abc/X.pptx",
        error=None,
    )

    async def _fake_script(*a, **k):
        return fake_repl

    monkeypatch.setattr(nodes, "execute_script_tool", _fake_script)

    # REPL server config lookup (script path bails out early if None)
    async def _fake_repl_cfg(*a, **k):
        return {"endpoint": "http://repl"}

    monkeypatch.setattr(nodes, "_get_repl_server_config", _fake_repl_cfg)

    return steps, emit


def base_state(emit, **over):
    state = {
        "emit": emit,
        "query": "帮我生成一份 PPT",
        "kb_id": "kb-123",
        "tenant_id": "t-1",
    }
    state.update(over)
    return state


# ---------------------------------------------------------------------------
# _emit helper
# ---------------------------------------------------------------------------

class TestEmitHelper:
    def test_emits_with_extra(self):
        steps, emit = make_collector()
        _emit({"emit": emit}, "skill_load", "已加载技能：PPT美化", skill="PPT美化")
        assert steps == [{"stage": "skill_load", "message": "已加载技能：PPT美化", "skill": "PPT美化"}]

    def test_noop_when_emit_none(self):
        # must not raise
        _emit({"emit": None}, "x", "y")
        _emit({}, "x", "y")

    def test_swallows_callback_exception(self):
        def boom(stage, message, **extra):
            raise RuntimeError("kaboom")

        # must not propagate
        _emit({"emit": boom}, "x", "y")


# ---------------------------------------------------------------------------
# skill_loader_node
# ---------------------------------------------------------------------------

class TestSkillLoader:
    @pytest.mark.asyncio
    async def test_skill_load_emitted(self, patch_globals):
        steps, emit = patch_globals
        state = base_state(emit, active_skill={
            "id": "p1", "name": "PPT美化", "folder_name": "ppt_beautify",
        })
        await skill_loader_node(state)
        assert any(s["stage"] == "skill_load" for s in steps)
        assert steps[0]["skill"] == "PPT美化"

    @pytest.mark.asyncio
    async def test_no_emit_when_no_active_skill(self, patch_globals):
        steps, emit = patch_globals
        state = base_state(emit, active_skill=None)
        await skill_loader_node(state)
        assert not any(s["stage"] == "skill_load" for s in steps)


# ---------------------------------------------------------------------------
# skill_switcher_node
# ---------------------------------------------------------------------------

def _switcher_state(emit, fname, arguments, **over):
    return base_state(emit, tool_calls=[{
        "id": "call_1",
        "function": {"name": fname, "arguments": arguments},
    }], **over)


class TestSkillSwitcher:
    @pytest.mark.asyncio
    async def test_done_skill_emits_return(self, patch_globals):
        steps, emit = patch_globals
        state = _switcher_state(
            emit, "done_skill", "{}",
            skill_stack=[{"name": "PPT美化"}, {"name": "文档生成助手"}],
        )
        await skill_switcher_node(state)
        assert any(s["stage"] == "skill_return" for s in steps)
        ret = [s for s in steps if s["stage"] == "skill_return"][0]
        assert ret["skill"] == "PPT美化"

    @pytest.mark.asyncio
    async def test_use_skill_success_emits_switch_with_reason(self, patch_globals):
        steps, emit = patch_globals
        state = _switcher_state(
            emit, "use_skill",
            '{"skill_name": "文档生成助手", "reason": "需要先生成PPT文档再美化"}',
            skill_stack=[{"name": "PPT美化", "id": "p1", "folder_name": "ppt_beautify"}],
            loaded_skill_ids=["p1"],
            skill_switch_count=0,
        )
        await skill_switcher_node(state)
        sw = [s for s in steps if s["stage"] == "skill_switch"]
        assert sw, "expected a skill_switch event"
        assert "文档生成助手" in sw[0]["message"]
        assert "需要先生成PPT文档再美化" in sw[0]["message"]
        assert sw[0]["reason"] == "需要先生成PPT文档再美化"
        assert sw[0]["skill"] == "文档生成助手"

    @pytest.mark.asyncio
    async def test_use_skill_missing_name_fails(self, patch_globals):
        steps, emit = patch_globals
        state = _switcher_state(emit, "use_skill", "{}",
                                skill_stack=[{"name": "PPT美化"}])
        await skill_switcher_node(state)
        assert any(s["stage"] == "skill_switch_fail" for s in steps)

    @pytest.mark.asyncio
    async def test_use_skill_not_found_fails(self, patch_globals, monkeypatch):
        steps, emit = patch_globals
        # override lookup to return None
        async def _none(db, name, tenant_id):
            return None
        monkeypatch.setattr(nodes, "get_skill_by_name", _none)
        state = _switcher_state(
            emit, "use_skill", '{"skill_name": "不存在"}',
            skill_stack=[{"name": "PPT美化", "id": "p1", "folder_name": "x"}],
            loaded_skill_ids=["p1"], skill_switch_count=0,
        )
        await skill_switcher_node(state)
        fails = [s for s in steps if s["stage"] == "skill_switch_fail"]
        assert fails and "未找到" in fails[0]["message"]

    @pytest.mark.asyncio
    async def test_use_skill_exceeds_limit_fails(self, patch_globals):
        steps, emit = patch_globals
        state = _switcher_state(
            emit, "use_skill", '{"skill_name": "文档生成助手"}',
            skill_stack=[{"name": "PPT美化", "id": "p1", "folder_name": "x"}],
            loaded_skill_ids=["p1"],
            skill_switch_count=nodes.MAX_SKILL_SWITCHES,
        )
        await skill_switcher_node(state)
        fails = [s for s in steps if s["stage"] == "skill_switch_fail"]
        assert fails and "上限" in fails[0]["message"]

    @pytest.mark.asyncio
    async def test_use_skill_already_loaded_fails(self, patch_globals):
        steps, emit = patch_globals
        state = _switcher_state(
            emit, "use_skill", '{"skill_name": "文档生成助手"}',
            skill_stack=[{"name": "PPT美化", "id": "p1", "folder_name": "x"}],
            loaded_skill_ids=["p1", "skill-doc"],  # doc id already loaded
            skill_switch_count=0,
        )
        await skill_switcher_node(state)
        fails = [s for s in steps if s["stage"] == "skill_switch_fail"]
        assert fails and "已在生效栈中" in fails[0]["message"]

    @pytest.mark.asyncio
    async def test_unknown_control_tool_fails(self, patch_globals):
        steps, emit = patch_globals
        state = _switcher_state(emit, "frobnicate", "{}",
                                skill_stack=[{"name": "PPT美化"}])
        await skill_switcher_node(state)
        assert any(s["stage"] == "skill_switch_fail" for s in steps)


# ---------------------------------------------------------------------------
# parallel_retrieval_node
# ---------------------------------------------------------------------------

class TestRetrieval:
    @pytest.mark.asyncio
    async def test_retrieval_and_done_emitted(self, patch_globals):
        steps, emit = patch_globals
        state = base_state(emit)
        await parallel_retrieval_node(state)
        stages = [s["stage"] for s in steps]
        assert "retrieval" in stages
        done = [s for s in steps if s["stage"] == "retrieval_done"]
        assert done, "expected retrieval_done"
        assert "命中 2 段" in done[0]["message"]

    @pytest.mark.asyncio
    async def test_skip_when_cache_hit(self, patch_globals):
        steps, emit = patch_globals
        state = base_state(emit, cache_hit=True)
        result = await parallel_retrieval_node(state)
        assert result == {}
        assert not steps


# ---------------------------------------------------------------------------
# tool_executor_node
# ---------------------------------------------------------------------------

class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_tool_label_emitted(self, patch_globals):
        steps, emit = patch_globals
        state = base_state(
            emit,
            tool_calls=[{
                "id": "c1",
                "function": {"name": "run_python", "arguments": "{}"},
            }],
            active_skill={"folder_name": "ppt_beautify"},
            available_tools=[{
                "function": {"name": "run_python"},
                "_source": "script",
                "_script_path": "gen.py",
                "_func_name": "run_python",
            }],
        )
        await tool_executor_node(state)
        tool = [s for s in steps if s["stage"] == "tool"]
        assert tool, "expected a tool event"
        assert tool[0]["tool"] == "run_python"
        assert "执行 Python 脚本" in tool[0]["message"]

    @pytest.mark.asyncio
    async def test_tool_done_on_file(self, patch_globals):
        steps, emit = patch_globals
        state = base_state(
            emit,
            tool_calls=[{
                "id": "c1",
                "function": {"name": "run_python", "arguments": "{}"},
            }],
            active_skill={"folder_name": "ppt_beautify"},
            available_tools=[{
                "function": {"name": "run_python"},
                "_source": "script",
                "_script_path": "gen.py",
                "_func_name": "run_python",
            }],
        )
        await tool_executor_node(state)
        done = [s for s in steps if s["stage"] == "tool_done"]
        assert done, "expected a tool_done event for [File] output"
        assert "X.pptx" in done[0]["message"]

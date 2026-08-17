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
from app.services.config_manager import config_manager
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

class _FakeResult:
    """Stand-in for the SQLAlchemy Result returned by ``session.execute``.

    The nodes under test only ever call ``.scalars().all()`` (e.g. the
    ``use_skill`` fuzzy-fallback registry scan at agent_nodes.py:1216), so an
    empty list is the correct behaviour for an isolated unit test.
    """

    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    """Minimal async context-manager + DB session standing in for async_session()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return _FakeResult()


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

    # FS-disabled routing gate: the dev container mounts the shared skills
    # volume but the test skills have no enable-symlink, so
    # is_skill_effectively_enabled() returns False and the nodes skip loading.
    # Isolate this unit test from that environment-dependent gate (it is
    # exercised by integration paths, not here).
    monkeypatch.setattr(nodes, "is_skill_effectively_enabled", lambda folder_name: True)

    # Skill body loading — return a dummy prompt + empty tool list
    async def fake_load(folder_name, user_id=None):
        return ("SYS_PROMPT", [])

    monkeypatch.setattr(nodes, "_load_skill_body_and_tools", fake_load)

    # Skill lookup — return a fake skill
    async def fake_get_skill(db, name, tenant_id):
        return _FakeSkill(name=name)

    monkeypatch.setattr(nodes, "get_skill_by_name", fake_get_skill)

    # Retrieval backend — parallel_retrieval_node calls vector_store.search and
    # bm25_index.search (then hybrid_search.fuse), so mock both real entry points.
    def fake_search(kb_id, query, *top_k):
        # Shape matches both fuse() branches: vector needs id/content/score/
        # metadata, BM25 needs chunk_id/content/score (+ optional doc fields).
        return [
            {"id": "id1:0", "chunk_id": "id1:0", "content": "c1", "score": 0.9,
             "metadata": {"doc_id": "id1", "filename": "d1", "heading": "",
                          "chunk_index": 0, "page": None},
             "doc_id": "id1", "doc_name": "d1", "heading": "", "chunk_index": 0,
             "page": None},
            {"id": "id2:1", "chunk_id": "id2:1", "content": "c2", "score": 0.8,
             "metadata": {"doc_id": "id2", "filename": "d2", "heading": "",
                          "chunk_index": 1, "page": None},
             "doc_id": "id2", "doc_name": "d2", "heading": "", "chunk_index": 1,
             "page": None},
        ]

    monkeypatch.setattr(nodes.vector_store, "search", fake_search)
    monkeypatch.setattr(nodes.bm25_index, "search", fake_search)

    # Tool executor backend (script path returns structured file refs, no [File] text)
    fake_repl = types.SimpleNamespace(
        ok=True,
        result="File generated successfully",
        files=[{"name": "X.pptx", "path": "X.pptx",
                "mimeType": "application/vnd.ms-powerpoint"}],
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
        _emit({"emit": emit}, "skill_load", "Loaded skill: PPT美化", skill="PPT美化")
        assert steps == [{"stage": "skill_load", "message": "Loaded skill: PPT美化", "skill": "PPT美化"}]

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
        assert fails and "no available skill" in fails[0]["message"]

    @pytest.mark.asyncio
    async def test_use_skill_exceeds_limit_fails(self, patch_globals):
        steps, emit = patch_globals
        state = _switcher_state(
            emit, "use_skill", '{"skill_name": "文档生成助手"}',
            skill_stack=[{"name": "PPT美化", "id": "p1", "folder_name": "x"}],
            loaded_skill_ids=["p1"],
            skill_switch_count=config_manager.skill_switch_quota,
        )
        await skill_switcher_node(state)
        fails = [s for s in steps if s["stage"] == "skill_switch_fail"]
        assert fails and "skill_switch_limit" in fails[0]["message"]

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
        assert fails and "already active in the stack" in fails[0]["message"]

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
        assert "Retrieved 2 chunk(s)" in done[0]["message"]

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
        assert "Run Python script" in tool[0]["message"]

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


# ---------------------------------------------------------------------------
# Persistence channel (agent_steps) — Route D observability
# ---------------------------------------------------------------------------

class TestAgentStepsPersistence:
    def test_emit_accumulates_separate_channel(self):
        # _emit must write into state["agent_steps"] for durable persistence,
        # independent of (and in addition to) the SSE collector.
        steps, emit = make_collector()
        st = {"emit": emit}
        _emit(st, "retrieval", "Retrieving from knowledge base…")
        _emit(st, "tool", "Running tool: Run Python script", tool="run_python")
        assert "agent_steps" in st
        assert len(st["agent_steps"]) == 2
        assert st["agent_steps"][0]["stage"] == "retrieval"
        assert st["agent_steps"][1]["extra"] == {"tool": "run_python"}
        # SSE collector still received the same events (dual channel)
        assert len(steps) == 2

    def test_emit_accumulates_without_emit_callback(self):
        st: dict = {}
        _emit(st, "routing", "Analyzing intent…")
        assert st["agent_steps"][0]["stage"] == "routing"

    def test_emit_never_raises_on_accumulation_failure(self):
        # A hostile state that breaks setdefault/get must not crash the graph.
        class Bad:
            def setdefault(self, k, d):
                raise RuntimeError("no")

            def get(self, k, d=None):
                return d

        _emit(Bad(), "x", "y")  # must not raise


class TestSanitizeLlmMessages:
    def test_drops_agent_step_marker(self):
        from app.services.agent_graph import _sanitize_llm_messages

        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q", "agent_step": True},
            {"role": "assistant", "content": "a"},
        ]
        clean = _sanitize_llm_messages(msgs)
        assert len(clean) == 2
        assert all("agent_step" not in m for m in clean)

    def test_passthrough_when_clean(self):
        from app.services.agent_graph import _sanitize_llm_messages

        msgs = [{"role": "user", "content": "q"}]
        assert _sanitize_llm_messages(msgs) == msgs


class TestBuildGenerationMessagesIsolation:
    def test_agent_step_history_is_stripped(self):
        from app.services.agent_graph import ragclaw_agent_graph

        state = {
            "query": "hi",
            "conversation_history": [
                {"role": "user", "content": "earlier"},
                # A leaked processing-trace marker must never reach the LLM.
                {"role": "user", "content": "LEAKED_STEP_TEXT", "agent_step": True},
            ],
            "tool_results": [],
            "download_entries": [],
            "rag_context": "",
            "active_skill": None,
            "kb_prompt": "",
        }
        msgs, _ = ragclaw_agent_graph.build_generation_messages(state)
        assert all(not m.get("agent_step") for m in msgs)
        joined = " ".join(m.get("content", "") for m in msgs)
        assert "LEAKED_STEP_TEXT" not in joined

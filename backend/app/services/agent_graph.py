"""ERAG Agent Graph — LangGraph state machine for routing, retrieval, and tool calls.

The graph handles everything up to LLM generation. chat.py reads the final
state from the graph, then handles streaming LLM generation + SSE output + post-processing.

Graph topology:
    entry ──(cache hit)──→ END
    entry ──(cache miss)─→ fanout ─┬─→ router (LLM) ──┐
                                   └─→ retrieval (I/O) ┘  (run in PARALLEL)
                                   join ──(skill)──→ skill_loader → tool_decision
                                   join ─(no skill)─→ tool_decision
    tool_decision ─(no tools)─→ build_context → END
    tool_decision ─(tools)───→ tool_executor → tool_decision (loop)
"""

from langgraph.graph import StateGraph, END, START

from app.services.config_manager import config_manager
from app.services.agent_state import EragAgentState
from app.services.agent_nodes import (
    entry_node,
    fanout_node,
    join_node,
    skill_router_node,
    skill_loader_node,
    skill_switcher_node,
    parallel_retrieval_node,
    tool_decision_node,
    tool_executor_node,
    build_context_node,
    resume_replay_node,
    limit_suspend_node,
)


def _build_graph() -> StateGraph:
    """Construct the ERAG agent state graph.

    skill_router_node (LLM call) and parallel_retrieval_node (I/O) run in
    PARALLEL after the cache gate, so retrieval no longer waits for the router's
    LLM latency. They converge at `join`, which then routes to skill_loader
    (if a skill was selected) or straight to tool_decision.
    """
    workflow = StateGraph(EragAgentState)

    # Register nodes
    workflow.add_node("entry", entry_node)
    workflow.add_node("fanout", fanout_node)
    workflow.add_node("router", skill_router_node)
    workflow.add_node("retrieval", parallel_retrieval_node)
    workflow.add_node("join", join_node)
    workflow.add_node("skill_loader", skill_loader_node)
    workflow.add_node("skill_switcher", skill_switcher_node)
    workflow.add_node("tool_decision", tool_decision_node)
    workflow.add_node("tool_executor", tool_executor_node)
    workflow.add_node("build_context", build_context_node)

    # Entry point: cache gate (normal) | resume_replay (continue) | build_context (stop)
    def route_entry(state: dict) -> str:
        action = state.get("resume_action")
        if action == "continue":
            return "resume_replay"
        if action == "stop":
            return "build_context"
        return "entry"

    workflow.add_conditional_edges(
        START, route_entry,
        {"entry": "entry", "resume_replay": "resume_replay", "build_context": "build_context"},
    )

    # entry → END (cache hit) | fanout (cache miss)
    def route_after_entry(state: dict) -> str:
        return "end" if state.get("cache_hit") else "fanout"

    workflow.add_conditional_edges(
        "entry",
        route_after_entry,
        {"end": END, "fanout": "fanout"},
    )

    # fanout → router (LLM) AND retrieval (I/O) — PARALLEL branches
    workflow.add_edge("fanout", "router")
    workflow.add_edge("fanout", "retrieval")

    # router → join, retrieval → join (converge)
    workflow.add_edge("router", "join")
    workflow.add_edge("retrieval", "join")

    # join → skill_loader (has skill) | tool_decision (no skill)
    def route_after_join(state: dict) -> str:
        if state.get("active_skill"):
            return "skill_loader"
        return "tool_decision"

    workflow.add_conditional_edges(
        "join",
        route_after_join,
        {"skill_loader": "skill_loader", "tool_decision": "tool_decision"},
    )

    # Skill loader → tool_decision
    workflow.add_edge("skill_loader", "tool_decision")

    # Tool decision → skill_switcher (meta control) | tool_executor | build_context
    def route_after_tool_decision(state: dict) -> str:
        if state.get("pending_limit"):
            return "limit_suspend"
        tool_calls = state.get("tool_calls")
        if tool_calls:
            fname = tool_calls[0].get("function", {}).get("name", "")
            if fname in ("use_skill", "done_skill", "list_skills"):
                return "skill_switcher"
            return "tool_executor"
        return "build_context"

    workflow.add_conditional_edges(
        "tool_decision",
        route_after_tool_decision,
        {"skill_switcher": "skill_switcher", "tool_executor": "tool_executor",
         "build_context": "build_context", "limit_suspend": "limit_suspend"},
    )

    # Tool executor → back to tool_decision (for multi-round)
    workflow.add_edge("tool_executor", "tool_decision")

    # Skill switcher → tool_decision (re-decide) | limit_suspend (挂起)
    def route_after_skill_switcher(state: dict) -> str:
        if state.get("pending_limit"):
            return "limit_suspend"
        return "tool_decision"

    workflow.add_conditional_edges(
        "skill_switcher", route_after_skill_switcher,
        {"tool_decision": "tool_decision", "limit_suspend": "limit_suspend"},
    )

    # Resume replay → skill_switcher (原因A 重放) | tool_decision (原因B 重决策)
    def route_resume(state: dict) -> str:
        if (state.get("pending_limit") or {}).get("kind") == "tool_round":
            return "tool_decision"
        return "skill_switcher"

    workflow.add_conditional_edges(
        "resume_replay", route_resume,
        {"tool_decision": "tool_decision", "skill_switcher": "skill_switcher"},
    )

    # Register resume/suspend nodes
    workflow.add_node("resume_replay", resume_replay_node)
    workflow.add_node("limit_suspend", limit_suspend_node)

    # Limit suspend → END (挂起出口)
    workflow.add_edge("limit_suspend", END)

    # Build context → END
    workflow.add_edge("build_context", END)

    # Compile (no checkpointer for now; can add for conversation resume later)
    return workflow.compile()


class EragAgentGraph:
    """Public API for the ERAG agent graph.

    Usage from chat.py:

        graph = EragAgentGraph()
        state = await graph.run(initial_state)

        if state["cache_hit"]:
            yield cached answer...
        else:
            messages = graph.build_generation_messages(state)
            async for token in llm_client.chat_stream(messages):
                yield SSE token
            yield SSE citations
            yield SSE done
    """

    def __init__(self):
        self._graph = _build_graph()

    async def run(self, initial_state: dict) -> dict:
        """Run the graph from initial state to completion.

        Returns the final EragAgentState with all fields populated.
        """
        result = await self._graph.ainvoke(initial_state)
        return result

    def build_generation_messages(self, state: dict) -> list[dict]:
        """Build the final message list for LLM generation.

        Assembles system prompt (from SKILL or default), conversation history,
        memory context, tool results, rag context, and user query.
        """
        active_skill = state.get("active_skill") or {}
        system_prompt = active_skill.get(
            "system_prompt",
            config_manager.system_prompt,
        )

        # KB-specific instruction (set by skill_router_node), appended after the
        # stable system prompt so the cached prefix stays consistent across KBs.
        kb_prompt = state.get("kb_prompt") or ""
        if kb_prompt:
            system_prompt = system_prompt + "\n\n## 知识库背景与偏好\n" + kb_prompt

        # ── Final generation guidance: when no tools were executed, prevent
        # the LLM from outputting [TOOL_CALL] or JSON tool invocations in free text.
        # The tool-decision phase already determined no tools were needed (or usable),
        # so the LLM should generate a natural language answer. ──
        tool_results = state.get("tool_results", [])
        final_note = ""
        if not tool_results:
            # Check if the skill prompt tells the LLM to use tools
            has_tool_instruction = (
                "run_python" in system_prompt
                or "工具" in system_prompt
                or "调用" in system_prompt
            )
            if has_tool_instruction:
                final_note = (
                    "\n\n## ⚠️ 当前阶段：最终回答生成\n\n"
                    "这是最终生成阶段，不再有工具调用能力。"
                    "请直接以自然语言回复用户的问题。"
                    "**绝对不要**输出 [TOOL_CALL]、JSON 格式的工具调用、或任何代码块伪装成工具调用。"
                    "如果用户的任务需要工具但工具未执行，请如实告知用户。"
                )

        cron_rule = (
            "\n\n## Scheduled Task Rule\n\n"
            "If the user wants to create a recurring or one-time scheduled task "
            "(e.g., 'every morning at 9', '每周一', '每小时'), do NOT answer directly. "
            "Instead output ONLY a single JSON object with this exact shape:\n"
            '{\n'
            '  "type": "cron",\n'
            '  "name": "<short task name>",\n'
            '  "cron_expr": "<Linux crontab 5-field expression>",\n'
            '  "max_runs": <integer or null for infinite>,\n'
            '  "task_content": "<the exact task to execute>",\n'
            '  "description": "<optional description>"\n'
            '}\n'
            "Examples:\n"
            '- "每天早上9点总结昨日文档" → cron_expr "0 9 * * *"\n'
            '- "每30分钟检查一次" → cron_expr "*/30 * * * *"\n'
            '- "只执行一次，今晚8点" → cron_expr "0 20 * * *", max_runs 1\n'
            "Do not wrap the JSON in markdown code fences."
        )

        # final_note is intentionally kept OUT of the system prompt — it lives in
        # the user message (dynamic region) below. This keeps the cached system
        # prefix constant regardless of tool execution, so provider-side prompt
        # caches don't get split into two incompatible groups.
        messages = [{"role": "system", "content": system_prompt + cron_rule}]

        # Conversation history
        history = state.get("conversation_history", [])
        if history:
            messages.extend(history)

        # Build user message with all context
        user_parts = []

        if state.get("memory_context"):
            user_parts.append(f"## 用户偏好与历史记忆\n{state['memory_context']}")

        if state.get("tool_results"):
            tool_text = "\n".join(
                f"- {r}" for r in state["tool_results"]
            )
            user_parts.append(f"## 工具调用结果\n{tool_text}")

        if state.get("rag_context"):
            user_parts.append(f"## 参考文档\n{state['rag_context']}")

        user_parts.append(f"## 问题\n{state['query']}")
        # Final-stage constraint stays in the dynamic user region (only varies with
        # tool execution); keeping it out of the system prefix preserves cache hits.
        if final_note:
            user_parts.append(final_note)
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})

        return messages


# Singleton
erag_agent_graph = EragAgentGraph()

"""ERAG Agent Graph — LangGraph state machine for routing, retrieval, and tool calls.

The graph handles everything up to LLM generation. chat.py reads the final
state from the graph, then handles streaming LLM generation + SSE output + post-processing.

Graph topology:
    router ──(cache hit)──→ END
    router ──(no hit, no skill)──→ retrieval
    router ──(no hit, skill)───→ skill_loader → retrieval
    retrieval ──────────→ tool_decision
    tool_decision ─(no tools)─→ build_context → END
    tool_decision ─(tools)───→ tool_executor → tool_decision (loop)
"""

from langgraph.graph import StateGraph, END

from app.services.config_manager import config_manager
from app.services.agent_state import EragAgentState
from app.services.agent_nodes import (
    skill_router_node,
    skill_loader_node,
    skill_switcher_node,
    parallel_retrieval_node,
    tool_decision_node,
    tool_executor_node,
    build_context_node,
)


def _build_graph() -> StateGraph:
    """Construct the ERAG agent state graph."""
    workflow = StateGraph(EragAgentState)

    # Register nodes
    workflow.add_node("router", skill_router_node)
    workflow.add_node("skill_loader", skill_loader_node)
    workflow.add_node("skill_switcher", skill_switcher_node)
    workflow.add_node("retrieval", parallel_retrieval_node)
    workflow.add_node("tool_decision", tool_decision_node)
    workflow.add_node("tool_executor", tool_executor_node)
    workflow.add_node("build_context", build_context_node)

    # Entry point
    workflow.set_entry_point("router")

    # Router → END (cache hit) or skill_loader (has skill) or retrieval (no skill)
    def route_after_router(state: dict) -> str:
        if state.get("cache_hit"):
            return "end"
        if state.get("active_skill"):
            return "skill_loader"
        return "retrieval"

    workflow.add_conditional_edges(
        "router",
        route_after_router,
        {"end": END, "skill_loader": "skill_loader", "retrieval": "retrieval"},
    )

    # Skill loader → retrieval
    workflow.add_edge("skill_loader", "retrieval")

    # Retrieval → tool_decision
    workflow.add_edge("retrieval", "tool_decision")

    # Tool decision → skill_switcher (meta control) | tool_executor | build_context
    def route_after_tool_decision(state: dict) -> str:
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
        {"skill_switcher": "skill_switcher", "tool_executor": "tool_executor", "build_context": "build_context"},
    )

    # Tool executor → back to tool_decision (for multi-round)
    workflow.add_edge("tool_executor", "tool_decision")

    # Skill switcher → back to tool_decision (re-decide with updated skill/tools)
    workflow.add_edge("skill_switcher", "tool_decision")

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

        messages = [{"role": "system", "content": system_prompt + final_note + cron_rule}]

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
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})

        return messages


# Singleton
erag_agent_graph = EragAgentGraph()

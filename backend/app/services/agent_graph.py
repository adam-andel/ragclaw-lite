"""RAGClaw Agent Graph — LangGraph state machine for routing, retrieval, and tool calls.

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

import re

from app.services.config_manager import config_manager
from app.services.conversation_summary import context_breakdown, fit_assembly_context
from app.services.i18n import t as _t
from app.services.token_count import count_messages_tokens
from app.services.agent_state import RagclawAgentState
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


def sandbox_network_rule(execution: bool = False) -> str:
    """Render the live sandbox egress policy as a prompt fragment.

    The policy is read at call time (not import time) so a change saved in
    Settings takes effect on the very next turn, matching the hot-reload the
    MCP REPL server already gets via PUT /policy.

    Exposed at module level because both the creation path (the cron rule in
    ``build_generation_messages``) and the execution path (``cron_graph``) need
    the same authoritative text.

    Args:
        execution: True when a cron job is being RUN. The job already exists, so
            "don't create it" is meaningless; the run must fail loudly instead.
    """
    mode = (config_manager.sandbox_network_mode or "deny").strip().lower()

    if mode == "allow":
        mode_line = (
            "Mode: ALLOW — the sandbox has unrestricted outbound network access. "
            "Any host may be reached."
        )
    elif mode == "allowlist":
        domains = [
            d.strip()
            for d in re.split(r"[\s,]+", config_manager.sandbox_allow_domains or "")
            if d.strip()
        ]
        if domains:
            mode_line = (
                "Mode: ALLOWLIST — outbound requests are permitted ONLY to these "
                "domains: " + ", ".join(domains) + ". Every other host is blocked "
                "(the request will fail; it is proxied and filtered)."
            )
        else:
            # allowlist with an empty list denies everything in practice.
            mode_line = (
                "Mode: ALLOWLIST, but the allowlist is EMPTY — in practice every "
                "outbound request is blocked, exactly like DENY."
            )
    else:
        mode_line = (
            "Mode: DENY — the sandbox has NO outbound network access at all. "
            "DNS resolution itself fails. Every HTTP/API/web request from task code "
            "WILL fail. Libraries such as requests/urllib/httpx, and tools such as "
            "curl/wget, cannot reach anything."
        )

    return _t(
        "sandbox_network_rule_exec" if execution else "sandbox_network_rule",
        config_manager.prompt_language,
        mode_line=mode_line,
    )


def _build_graph() -> StateGraph:
    """Construct the RAGClaw agent state graph.

    skill_router_node (LLM call) and parallel_retrieval_node (I/O) run in
    PARALLEL after the cache gate, so retrieval no longer waits for the router's
    LLM latency. They converge at `join`, which then routes to skill_loader
    (if a skill was selected) or straight to tool_decision.
    """
    workflow = StateGraph(RagclawAgentState)

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

    # join → skill_loader (always; skill_loader injects the always-available meta
    # tools — control tools + Python Executor native tools like run_python — so the
    # agent's native file/code capabilities work even when no skill is selected).
    def route_after_join(state: dict) -> str:
        return "skill_loader"

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

   # Skill switcher → tool_decision (re-decide) | limit_suspend (suspend))
    def route_after_skill_switcher(state: dict) -> str:
        if state.get("pending_limit"):
            return "limit_suspend"
        return "tool_decision"

    workflow.add_conditional_edges(
        "skill_switcher", route_after_skill_switcher,
        {"tool_decision": "tool_decision", "limit_suspend": "limit_suspend"},
    )

   # Resume replay → skill_switcher (cause A: replay) | tool_decision (cause B: re-decide))
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

   # Limit suspend → END (suspension exit))
    workflow.add_edge("limit_suspend", END)

    # Build context → END
    workflow.add_edge("build_context", END)

    # Compile (no checkpointer for now; can add for conversation resume later)
    return workflow.compile()


class RagclawAgentGraph:
    """Public API for the RAGClaw agent graph.

    Usage from chat.py:

        graph = RagclawAgentGraph()
        state = await graph.run(initial_state)

        if state["cache_hit"]:
            yield cached answer...
        else:
            messages, _ = graph.build_generation_messages(state)
            async for token in llm_client.chat_stream(messages):
                yield SSE token
            yield SSE citations
            yield SSE done
    """

    def __init__(self):
        self._graph = _build_graph()

    async def run(self, initial_state: dict) -> dict:
        """Run the graph from initial state to completion.

        Returns the final RagclawAgentState with all fields populated.
        """
        result = await self._graph.ainvoke(initial_state)
        return result

    def build_generation_messages(self, state: dict, include_cron_rule: bool = True) -> tuple:
        """Build the final message list for LLM generation.

        Assembles system prompt (from SKILL or default), conversation history,
        memory context, tool results, rag context, and user query.
        """
        active_skill = state.get("active_skill") or {}
        # Part 1 (identity/security) is the ALWAYS-ON base — never replaced by an
        # active skill. Part 2 (native capabilities) is replaced by the skill's own
        # system_prompt when one is selected, otherwise the constant base.
        identity_prompt = config_manager.system_prompt_identity
        cap_prompt = active_skill.get(
            "system_prompt",
            config_manager.system_prompt_capabilities,
        )

        # KB-specific instruction (set by skill_router_node). Lands in system②
        # together with system_prompt / user_memory / pin, so system① (the always-on
        # constants) stays a stable, independently cacheable prefix regardless of which
        # KB or pinned instruction is active.
        kb_prompt = state.get("kb_prompt") or ""

        # ── Final-answer guidance: an ALWAYS-ON constant suffix (merged from the
        # old branch-only final_stage_note). It forbids the model from emitting
        # [TOOL_CALL] / JSON tool calls in the final answer (fixes D1 — previously
        # only applied when no tool_results existed) AND asks for a one-line
        # divergent closing. Lives in the stable system prefix alongside file_rule
        # so provider-side prompt caches stay consistent. ──
        # tool_results is still needed below by _assemble (RAG sentinel guard).
        tool_results = state.get("tool_results", [])

        cron_rule = (
            "\n\n## Scheduled Task Rule\n\n"
            "If the user wants to create a recurring or one-time scheduled task "
            "(e.g., 'every morning at 9', '每周一', '每小时'), do NOT answer the task "
            "content yourself. The system creates the scheduled task for you via the "
            "create_cron tool — call that tool with the task's name, cron_expr, and "
            "task_content. Useful cron_expr examples:\n"
            '- "每天早上9点总结昨日文档" → cron_expr "0 9 * * *"\n'
            '- "每30分钟检查一次" → cron_expr "*/30 * * * *"\n'
            '- "只执行一次，今晚8点" → cron_expr "0 20 * * *", max_runs 1\n'
            "Once the scheduled task has been created (a create_cron tool result is "
            "present in the conversation), your final answer must be a plain-language "
            "confirmation ONLY — never output the task as JSON, never emit [TOOL_CALL], "
            "and never wrap anything in code fences.\n\n"
            # A scheduled task runs unattended: a script that silently falls back to
            # placeholder data would report success forever while emitting garbage.
            # Both rules below are also appended at execution time (cron_graph), so
            # the constraint holds whether the code is written now or at run time.
            + _t("cron_no_fallback_rule", config_manager.prompt_language)
            + "\n\n"
            # Surface the live egress policy at CREATION time so the model can refuse
            # an impossible task up front instead of improvising a fake one at runtime.
            + sandbox_network_rule()
        ) if include_cron_rule else ""

        # file_answer_rule: constant suffix appended every turn so the model never
        # re-pastes a generated file's source code. The heading lives inside the
        # i18n template, so we only add a blank-line separator here — the previous
        # code prepended a redundant English "## ..." line (a stray heading on the
        # zh path). final_answer_rule (D1 fix + closing suggestion) is appended the
        # same way, so both stay in the stable system prefix for cache consistency.
        file_rule = "\n\n" + _t("file_answer_rule", config_manager.prompt_language)
        final_answer_rule = "\n\n" + _t("final_answer_guidance", config_manager.prompt_language)
        # ── Assembly-point budget guard (the only hard ceiling) ──
        # build_context_with_summary applied SEMANTIC compression at turn start but
        # deliberately performs no mechanical trimming -- it cannot see the RAG /
        # memory / tool payload assembled here. This guard runs with the complete
        # payload and trims (rag -> memory -> summary -> history -> tool_payload) on
        # TRANSIENT copies, writing nothing back. `q` and `mem` are threaded in as
        # parameters (never read from `state`) so the phase-3 query truncation and
        # memory trimming can take effect.
        def _assemble(s, h, rag, payload, q, mem):
            # Per-conversation pinned instruction (system②) — a sacred prefix that
            # fit_assembly_context never trims. Always injected, never folded into
            # summary_text. Mirrors the floor format in _empty_context_request_tokens.
            pin = state.get("pinned_instruction") or ""
            user_memory = (state.get("user_memory") or "").strip()

            # system① — ALWAYS-ON constants (cron is gated by include_cron_rule but
            # constant within a session; file/final are unconditional). Placed FIRST
            # with its own cache breakpoint so this stable block is shared across all
            # requests regardless of the active skill/config/system_prompt.
            const_prefix = cron_rule + file_rule + final_answer_rule

            # system② — ALWAYS-ON identity/security base (Part 1) + per-session
            # context (KB / user memory / pinned instruction). Part 1 is never
            # replaced by an active skill, so it stays a stable base regardless of
            # which skill or config is active.
            var_prefix = identity_prompt
            if kb_prompt:
                var_prefix += f"\n\n## Knowledge Base Background & Preferences\n{kb_prompt}"
            if user_memory:
                var_prefix += f"\n\n## User Memory & Preferences\n{user_memory}"
            if pin:
                var_prefix += f"\n\n## Pinned Instructions\n{pin}"

            # system③ — native-capability base (Part 2), placed LAST among the
            # system messages so it is the OUTERMOST cache unit. It changes most
            # often (an active skill replaces it with its own system_prompt), so
            # keeping it at the tail means skill/KP/pin churn invalidates only this
            # trailing block, not the stable identity base above.
            cap_prefix = cap_prompt

            msgs = []
            if const_prefix.strip():
                msgs.append({"role": "system", "content": const_prefix})
            if var_prefix.strip():
                msgs.append({"role": "system", "content": var_prefix})
            if cap_prefix.strip():
                msgs.append({"role": "system", "content": cap_prefix})
            if s:
                msgs.append(
                    {"role": "system", "content": "## Earlier conversation summary (compressed)\n" + s}
                )
            if h:
                # Defense-in-depth: drop any bare suspension sentinel
                # ("tool_round_limit" / "skill_switch_limit") from history before
                # it reaches the LLM. Suspensions are no longer persisted as
                # assistant messages (they live only in pending_limit_states), so
                # in normal operation history never contains these codes. This
                # filter remains as a safety net so a stray sentinel can never be
                # parroted back as a fake answer on the next turn.
                sentinel = re.compile(r"^(tool_round_limit|skill_switch_limit)\s*$")
                msgs.extend(
                    m for m in h
                    if not (m.get("role") == "assistant"
                            and isinstance(m.get("content"), str)
                            and sentinel.match(m["content"].strip()))
                )
            user_parts = []
            if payload:
                tool_text = "\n".join(f"- {r}" for r in payload)
                user_parts.append(f"## Tool Call Results\n{tool_text}")
            if rag:
                # When a skill executed tools, the answer is built from the tool
                # result, not from retrieval — so never surface the "no relevant
                # documents" sentinel (it would make the model claim it found
                # nothing). Genuine retrieved context is still injected.
                if not (rag.strip() == "No relevant documents found" and active_skill and tool_results):
                    user_parts.append(f"## Reference Documents\n{rag}")
            if mem:
                user_parts.append(f"## Conversation Memory (archived summary)\n{mem}")
            user_parts.append(f"## Question\n{q}")
            msgs.append({"role": "user", "content": "\n\n".join(user_parts)})
            return msgs

        trimmed_s, trimmed_h, trimmed_rag, trimmed_mem, trimmed_p, trimmed_q, dropped = fit_assembly_context(
            summary_text=state.get("conversation_summary"),
            history=state.get("conversation_history", []),
            rag_context=state.get("rag_context"),
            memory_context=state.get("memory_context"),
            tool_payload=state.get("tool_results", []),
            query=state.get("query") or "",
            payload_kind="results",
            build_messages=_assemble,
        )
        messages = _assemble(trimmed_s, trimmed_h, trimmed_rag, trimmed_p, trimmed_q, trimmed_mem)
        messages = _sanitize_llm_messages(messages)
        # Stash the persistent/transient split of THIS submission so the caller can
        # report it without re-deriving the post-trim components. Kept on `state`
        # (not in the return tuple) so existing 2-tuple unpacking keeps working.
        try:
            state["context_breakdown"] = context_breakdown(
                trimmed_s, trimmed_h, count_messages_tokens(messages)
            )
        except Exception:  # telemetry must never break generation
            pass
        return messages, dropped


def _sanitize_llm_messages(messages: list[dict]) -> list[dict]:
    """Defensive guard: ensure no agent-step trace ever reaches the LLM.

    Agent steps are persisted in a separate channel and must never be injected
    into the LLM message list (neither as a top-level message nor as a key on a
    message). Drop any message carrying an ``agent_step`` marker so a future
    regression cannot leak the processing trace into the model context.
    """
    return [m for m in messages if not (isinstance(m, dict) and m.get("agent_step"))]


# Singleton
ragclaw_agent_graph = RagclawAgentGraph()

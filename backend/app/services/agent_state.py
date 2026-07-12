"""EragAgentState — LangGraph state definition for the ERAG agent.

Uses TypedDict with Annotated reducers. The graph handles routing,
retrieval, and tool calls; LLM generation and SSE streaming happen
outside the graph in chat.py.
"""

from typing import TypedDict, Annotated, Callable
import operator


class EragAgentState(TypedDict):
    """State that flows through the ERAG agent graph.

    Some fields use Annotated with operator.add for accumulation
    across multiple graph cycles (e.g. tool_results for multi-round tool calls).
    """

    # ── Input ──
    query: str
    user_id: str
    tenant_id: str
    skill_id: str | None          # Optional: force a specific SKILL
    kb_id: str                    # Single KB per conversation (design rule)
    kb_prompt: str               # KB-specific instruction injected into system prompt
    conversation_history: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]

    # ── Runtime (not persisted) ──
    emit: Callable[[str, str, dict], None] | None   # SSE progress callback (agent_step); None = no streaming

    # ── Router output (Layer 1: name + description only) ──
    active_skill: dict | None     # {id, name, description, folder_name, system_prompt} — top of skill_stack
    available_tools: list[dict]   # Tools in OpenAI function-calling format (empty until skill_loader)
    skip_cache: bool              # When True, bypass all cache (used on regenerate)

    # ── Skill loader output (Layer 2: SKILL.md full text + tools) ──
    # system_prompt is stored inside active_skill after skill_loader runs

    # ── Retrieval output ──
    rag_context: str              # Formatted text of retrieved chunks
    citations: list[dict]         # Citation metadata for the frontend
    memory_context: str           # Recalled user memories as text

    # ── Tool call cycle ──
    tool_calls: list[dict] | None # LLM-decided tool calls (None = no tools)
    tool_round: int               # How many tool rounds executed (prevents infinite loop)
    tool_results: Annotated[list[str], operator.add]  # Accumulated across rounds
    tool_messages: Annotated[list[dict], operator.add]  # Full tool call/result messages for LLM

    # ── Output ──
    cache_hit: bool               # Set by router on cache hit
    final_answer: str             # Set by router on cache hit, else empty
    retrieval_ms: float           # Measured in retrieval node

    # ── Skill orchestration (Route D: stack-based chaining) ──
    skill_stack: list[dict]       # Stack of loaded skills; last entry = current/active_skill
    loaded_skill_ids: list[str]   # Dedupe list of loaded skill ids (TypedDict has no set)
    skill_switch_count: int       # Number of use_skill pushes (bounded by skill_switch_quota)
    workspace_id: str             # Stable workdir shared across tool calls in a turn/conversation
    conversation_id: str | None   # Conversation id, used to scope the workspace_id

    # ── 限额模型（替代硬常量比较；"继续"= quota += MAX，历史 count 不动）──
    skill_switch_quota: int       # 当前技能切换总额度，初始 = MAX_SKILL_SWITCHES
    tool_round_quota: int         # 当前工具轮次总额度，初始 = MAX_TOOL_ROUNDS
    pending_limit: dict | None    # 挂起信息: {kind, message, deferred_tool_call}; 非空即等待用户确认
    resume_action: str | None     # "continue" | "stop" | None(新问题)

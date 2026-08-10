"""RagclawAgentState — LangGraph state definition for the RAGClaw agent.

Uses TypedDict with Annotated reducers. The graph handles routing,
retrieval, and tool calls; LLM generation and SSE streaming happen
outside the graph in chat.py.
"""

from typing import TypedDict, Annotated, Callable
import operator


class RagclawAgentState(TypedDict):
    """State that flows through the RAGClaw agent graph.

    Some fields use Annotated with operator.add for accumulation
    across multiple graph cycles (e.g. tool_results for multi-round tool calls).
    """

    # ── Input ──
    query: str
    user_id: str
    tenant_id: str
    # User-authored free-text memory & preferences (from the profile page).
    # Injected into the system prompt so the LLM can personalize. Empty when
    # the user has not written anything.
    user_memory: str
    skill_id: str | None          # Optional: force a specific SKILL
    kb_id: str                    # Single KB per conversation (design rule)
    kb_prompt: str               # KB-specific instruction injected into system prompt
    conversation_history: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]
    conversation_summary: str          # Compressed oldest history; injected as a system message (see conversation_summary.py)

    # ── Runtime (not persisted) ──
    emit: Callable[[str, str, dict], None] | None   # SSE progress callback (agent_step); None = no streaming
    # Per-submission context telemetry callback (context_usage SSE event).
    # Fired every time a payload is handed to the LLM so the frontend meter
    # tracks the LATEST submission, including intermediate tool rounds.
    # Deliberately separate from `emit`: this is transient telemetry and must
    # NOT be accumulated into agent_steps / persisted.
    emit_usage: Callable[[dict], None] | None
    # Persistent-vs-transient token split of the last submission (see
    # conversation_summary.context_breakdown).
    context_breakdown: dict | None

    # ── Router output (Layer 1: name + description only) ──
    active_skill: dict | None     # {id, name, description, folder_name, system_prompt} — top of skill_stack
    available_tools: list[dict]   # Tools in OpenAI function-calling format (empty until skill_loader)
    skip_cache: bool              # When True, bypass all cache (used on regenerate)

    # ── Skill loader output (Layer 2: SKILL.md full text + tools) ──
    # system_prompt is stored inside active_skill after skill_loader runs

    # ── Retrieval output ──
    rag_context: str              # Formatted text of retrieved chunks
    memory_context: str           # Formatted text of recalled conversation memory (archived L0/L1)
    citations: list[dict]         # Citation metadata for the frontend

    # ── Tool call cycle ──
    tool_calls: list[dict] | None # LLM-decided tool calls (None = no tools)
    tool_round: int               # How many tool rounds executed (prevents infinite loop)
    tool_results: Annotated[list[str], operator.add]  # Accumulated across rounds
    tool_messages: Annotated[list[dict], operator.add]  # Full tool call/result messages for LLM
    download_entries: Annotated[list[dict], operator.add]  # Structured file refs from MCP tools (no regex)

    # ── Output ──
    cache_hit: bool               # Set by router on cache hit
    final_answer: str             # Set by router on cache hit, else empty
    retrieval_ms: float           # Measured in retrieval node

    # ── Persisted processing trace (Route D observability) ──
    # Accumulated in place by _emit during the graph run, then persisted to the
    # agent_steps table. Never sent to the LLM and never saved to MEM0.
    agent_steps: list[dict]      # [{stage, message, extra, ts}]

    # ── Skill orchestration (Route D: stack-based chaining) ──
    skill_stack: list[dict]       # Stack of loaded skills; last entry = current/active_skill
    loaded_skill_ids: list[str]   # Dedupe list of loaded skill ids (TypedDict has no set)
    skill_switch_count: int       # Number of use_skill pushes (bounded by skill_switch_quota)
    workspace_id: str             # Stable workdir shared across tool calls in a turn/conversation
    conversation_id: str | None   # Conversation id, used to scope the workspace_id

   # ── Quota model (replaces hard-constant comparison; "continue" = quota += MAX, history count untouched) ───
    skill_switch_quota: int      # Current total skill-switch quota, initialized to MAX_SKILL_SWITCHES
    tool_round_quota: int        # Current total tool-round quota, initialized to MAX_TOOL_ROUNDS
    pending_limit: dict | None   # Suspension info: {kind, message, deferred_tool_call}; non-empty means awaiting user confirmation
    resume_action: str | None    # "continue" | "stop" | None (new question))

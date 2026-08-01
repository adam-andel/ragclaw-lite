"""Pydantic schemas for Chat API."""

from datetime import datetime
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    # Empty string is allowed when resuming a suspended run (resume_action
    # continue/stop): the real query is persisted in the suspension snapshot,
    # not in the request body. A genuine new question must still be non-empty
    # (validated in the endpoint).
    query: str = Field(default="")
    kb_id: str = Field(...)             # Keep required: one KB per conversationB
    skill_id: str | None = None         # Optional: specify a SKILL; None means auto-route
    conversation_id: str | None = None
    skip_cache: bool = False            # Skip the cache when regenerating
    resume_action: str | None = None    # "continue" | "stop" | None (new question))
    workspace_dir: str | None = None   # Optional: user-selected workspace sub-directory
                                        # (relative under their sandbox root; "" = root).
                                        # Routed to REPL as workspace_id; confined to user_u<uid>/ by _ws_safe.
    timezone: str | None = None        # Optional: user's local IANA timezone (e.g. Asia/Shanghai).
                                        # Used to interpret natural-language times when creating cron jobs.


class CitationSchema(BaseModel):
    doc_id: str
    doc_name: str
    chunk_index: int | None = None
    heading: str | None = None
    page: int | None = None
    score: float


class AgentStepOut(BaseModel):
    """One persisted agent processing trace entry (Route D observability)."""

    id: str
    seq: int = 0
    stage: str
    message: str
    skill: str | None = None
    tool: str | None = None
    detail: str | None = None
    extra: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    status: str | None = None
    citations: list[CitationSchema] = []
    cache_hit: bool = False
    token_count: int | None = None
    ttft_ms: int = 0
    retrieval_ms: int = 0
    llm_ms: int = 0
    created_at: datetime
    # Persisted processing trace (Route D observability). Excluded from the LLM
    # context and from MEM0 memory; surfaced to the frontend for replay after reload.
    agent_steps: list[AgentStepOut] = []

    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    id: str
    title: str
    kb_id: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    model_config = {"from_attributes": True}


class ConversationDetail(BaseModel):
    id: str
    title: str
    kb_id: str | None = None
    user_id: str | None = None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse] = []
    # Persistent-context state (see ConversationSummaryState). Returned here as
    # well so the context modal can be opened with a single cheap
    # ``?include_messages=false`` fetch instead of a dedicated endpoint.
    summary_text: str = ""
    summary_msg_count: int = 0
    total_messages: int = 0

    model_config = {"from_attributes": True}


class ConversationSummaryState(BaseModel):
    """Persistent-context state: the compressed summary plus its folding cursor.

    ``summary_msg_count`` is the number of oldest messages already folded into
    ``summary_text``; messages after it are still sent verbatim to the model.
    """

    conversation_id: str
    summary_text: str = ""
    summary_msg_count: int = 0
    total_messages: int = 0


class SummaryUpdateRequest(BaseModel):
    """Manual summary edit. The folding cursor is intentionally NOT editable."""

    summary_text: str = Field(default="")


class CompactRequest(BaseModel):
    """Manual compaction: fold this fraction of the un-summarized tail."""

    fraction: float = Field(default=0.5, gt=0.0, le=1.0)


class ConversationMessagesPage(BaseModel):
    """Server-side paginated messages, paginated by rounds (one Q&A = one round)."""

    conversation_id: str
    page: int
    page_size: int
    total_rounds: int
    total_pages: int
    total_messages: int
    has_more: bool # Whether an earlier page exists (page > 1)）
    messages: list[MessageResponse] = []

    model_config = {"from_attributes": True}


class PendingLimitResponse(BaseModel):
    """A durable Human-in-the-Loop pause waiting for the user (survives refresh)."""

    conversation_id: str
    message_id: str
    message: str
    kind: str

    model_config = {"from_attributes": True}

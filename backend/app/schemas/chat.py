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


class CitationSchema(BaseModel):
    doc_id: str
    doc_name: str
    chunk_index: int | None = None
    heading: str | None = None
    page: int | None = None
    score: float


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

    model_config = {"from_attributes": True}


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

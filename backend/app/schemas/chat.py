"""Pydantic schemas for Chat API."""

from datetime import datetime
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    kb_id: str = Field(...)
    conversation_id: str | None = None


class CitationSchema(BaseModel):
    doc_id: str
    doc_name: str
    heading: str | None = None
    page: int | None = None
    content_snippet: str = ""
    score: float


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    citations: list[CitationSchema] = []
    cache_hit: bool = False
    token_count: int | None = None
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

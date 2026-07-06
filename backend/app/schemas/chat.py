"""Pydantic schemas for Chat API."""

from datetime import datetime
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1)
    kb_id: str = Field(...)              # 保持必填，一次对话一个 KB
    skill_id: str | None = None          # 可选：指定 SKILL，None 则自动路由
    conversation_id: str | None = None
    skip_cache: bool = False             # 重新生成时跳过缓存


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

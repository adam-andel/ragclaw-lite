"""Pydantic schemas for Retrieval & Stats APIs."""

from datetime import datetime
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    kb_id: str | None = None
    vector_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    bm25_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    top_k: int = Field(default=10, ge=1, le=50)
    threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    doc_ids: list[str] | None = None


class SearchResultResponse(BaseModel):
    chunk_id: str
    doc_name: str
    heading: str | None = None
    page: int | None = None
    content: str
    vector_score: float
    bm25_score: float
    fusion_score: float


class HotQuestion(BaseModel):
    question: str
    count: int


class StatsOverview(BaseModel):
    document_count: int
    chunk_count: int
    conversation_count: int
    message_count: int
    cache_hit_rate: float
    today_token_cost: float
    hot_questions: list[HotQuestion]
    recent_conversations: list[dict]

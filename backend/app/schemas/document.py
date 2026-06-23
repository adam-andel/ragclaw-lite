"""Pydantic schemas for Document API."""

from datetime import datetime
from pydantic import BaseModel, Field


# ---- Knowledge Base ----
class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None


class KBUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None


class KBResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    doc_count: int = 0
    vector_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- Document ----
class DocumentResponse(BaseModel):
    id: str
    kb_id: str | None = None  # legacy, kept for backward compat
    filename: str
    file_type: str
    file_size: int
    status: str
    error_message: str | None = None
    chunk_count: int = 0
    progress: int = 0
    owner_id: str | None = None
    kb_ids: list[str] = []   # which KBs this doc belongs to (new m2m)
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class DocumentStatusResponse(BaseModel):
    id: str
    status: str
    error_message: str | None = None
    chunk_count: int = 0
    progress: int = 0


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    size: int


class DocKBLinkRequest(BaseModel):
    doc_ids: list[str]


class DocKBLinkResponse(BaseModel):
    added: int
    skipped: int


# ---- Chunk ----
class ChunkResponse(BaseModel):
    id: str
    doc_id: str
    chunk_index: int
    content: str
    token_count: int
    heading: str | None = None
    page: int | None = None

    model_config = {"from_attributes": True}

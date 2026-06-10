"""Pydantic schemas for Document API."""

from datetime import datetime
from pydantic import BaseModel, Field


# ---- Knowledge Base ----
class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None


class KBResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- Document ----
class DocumentResponse(BaseModel):
    id: str
    kb_id: str
    filename: str
    file_type: str
    file_size: int
    status: str
    error_message: str | None = None
    chunk_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentStatusResponse(BaseModel):
    id: str
    status: str
    error_message: str | None = None
    chunk_count: int = 0


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

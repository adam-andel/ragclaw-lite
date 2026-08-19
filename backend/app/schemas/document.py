# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Pydantic schemas for Document API."""

from datetime import datetime
from pydantic import BaseModel, Field


# ---- Knowledge Base ----
class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    prompt: str | None = None


class KBUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    prompt: str | None = None


class RetrievalConfigResponse(BaseModel):
    """Per-KB retrieval configuration. Null means "use global default"."""
    vector_weight: float | None = None
    bm25_weight: float | None = None
    vector_top_k: int | None = None
    bm25_top_k: int | None = None
    final_top_k: int | None = None
    similarity_threshold: float | None = None
    model_config = {"from_attributes": True}


class RetrievalConfigUpdate(BaseModel):
    """Update per-KB retrieval configuration. All fields optional; null means "reset to global default"."""
    vector_weight: float | None = None
    bm25_weight: float | None = None
    vector_top_k: int | None = None
    bm25_top_k: int | None = None
    final_top_k: int | None = None
    similarity_threshold: float | None = None


class KBResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    prompt: str | None = None
    doc_count: int = 0
    vector_count: int = 0
    created_at: datetime
    updated_at: datetime
    # Non-blocking config-time warnings, e.g. the KB instruction eating too
    # large a share of the context window. Only populated by the update
    # endpoint; list/create responses always leave it empty. Each entry is
    # {"code": <BARE_CODE>, "params": {...}} -- the frontend localizes it.
    warnings: list[dict] = []
    # Per-KB retrieval configuration (null = use global default)
    vector_weight: float | None = None
    bm25_weight: float | None = None
    vector_top_k: int | None = None
    bm25_top_k: int | None = None
    final_top_k: int | None = None
    similarity_threshold: float | None = None

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


class ChunkListResponse(BaseModel):
    items: list[ChunkResponse]
    total: int
    page: int
    size: int

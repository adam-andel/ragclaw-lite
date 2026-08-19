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
"""MemoryChunk ORM model — archived conversation-memory vectors.

When the rolling-window (L0) summary grows past the HIGH% threshold, the older
fold paragraphs are archived: each becomes a MemoryChunk so it can be recalled
later via hybrid (vector + BM25) search, independent of the user's document
knowledge base. Chunks are persisted BEFORE embedding (embedding-agnostic) so
keyword (BM25) retrieval keeps working even when no embedding model is installed.
"""

from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.document import gen_uuid


class MemoryChunk(Base):
    """One archived fold paragraph from a conversation's compressed summary."""

    __tablename__ = "memory_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    # Pseudo-KB id used by the vector store / BM25 index: f"mem_{conversation_id}".
    # Kept on every row so rebuild-from-DB can reconstruct the index namespace.
    mem_kb_id: Mapped[str] = mapped_column(String(36), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    heading: Mapped[str] = mapped_column(String(200), default="")
    page: Mapped[int] = mapped_column(Integer, default=0)
    # True once a vector has been written to Chroma. Rows with embedded=False are
    # retried on startup (process_pending_memory). BM25 is built regardless.
    embedded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

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
"""KnowledgeBase ORM model."""

from datetime import datetime
from sqlalchemy import String, Text, DateTime, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.document import gen_uuid


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Per-KB retrieval configuration (nullable = use global defaults)
    vector_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    bm25_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    vector_top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bm25_top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    similarity_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Many-to-many via kb_documents
    doc_links: Mapped[list["KBDocument"]] = relationship(
        "KBDocument", back_populates="knowledge_base", cascade="all, delete-orphan"
    )

"""Document and Chunk ORM models."""

import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Text, Float, DateTime, ForeignKey, Enum as SAEnum, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import Base


class DocStatus(str, enum.Enum):
    PENDING = "pending"        # waiting in processing queue
    UPLOADED = "uploaded"      # file saved, queued
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"


def gen_uuid():
    return str(uuid.uuid4())


class Document(Base):
    """Documents exist independently of knowledge bases (many-to-many)."""
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    # Legacy: kept for backward compatibility during migration; will be removed later
    kb_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(20))  # pdf, docx, md, txt
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[DocStatus] = mapped_column(SAEnum(DocStatus), default=DocStatus.PENDING)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100 processing progress
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    chunks: Mapped[list["Chunk"]] = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")
    kb_links: Mapped[list["KBDocument"]] = relationship("KBDocument", back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    doc_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    heading: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # cached embedding blob
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    def to_metadata(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "chunk_index": self.chunk_index,
            "heading": self.heading or "",
            "page": self.page or 0,
            "token_count": self.token_count,
        }


class KBDocument(Base):
    """Many-to-many: which documents belong to which knowledge bases."""
    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    kb_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_bases.id"), index=True)
    doc_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    knowledge_base: Mapped["KnowledgeBase"] = relationship("KnowledgeBase", back_populates="doc_links")
    document: Mapped["Document"] = relationship("Document", back_populates="kb_links")

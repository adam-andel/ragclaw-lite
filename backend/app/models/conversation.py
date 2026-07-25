"""Conversation and Message ORM models."""

import json
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.document import gen_uuid


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    title: Mapped[str] = mapped_column(String(500), default="New Conversation")
    kb_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_bases.id"), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    citations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    ttft_ms: Mapped[int] = mapped_column(Integer, default=0)
    retrieval_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    # Persisted agent processing trace (Route D observability). Loaded explicitly
    # via selectinload in the messages endpoint; kept out of the LLM context.
    agent_steps: Mapped[list["AgentStep"]] = relationship(
        "AgentStep", back_populates="message_ref",
        order_by="AgentStep.seq", cascade="all, delete-orphan",
    )

    @property
    def citations(self) -> list[dict]:
        if self.citations_json:
            return json.loads(self.citations_json)
        return []

    @citations.setter
    def citations(self, value: list[dict]):
        self.citations_json = json.dumps(value, ensure_ascii=False)


class AgentStep(Base):
    """Persisted agent processing trace (Route D observability).

    Stored in a separate channel from the LLM context and MEM0 memory: agent
    steps are never injected into conversation_history / tool_results, and are
    never fed to the memory-extraction LLM. One row per emitted step, linked to
    the assistant message the turn produced via ``message_id`` (filled after the
    message is persisted).
    """

    __tablename__ = "agent_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("messages.id"), index=True, nullable=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    message_ref: Mapped["Message"] = relationship("Message", back_populates="agent_steps")

    @property
    def extra(self) -> dict | None:
        if self.extra_json:
            try:
                return json.loads(self.extra_json)
            except Exception:
                return None
        return None

    @property
    def skill(self) -> str | None:
        ex = self.extra
        return ex.get("skill") if ex else None

    @property
    def tool(self) -> str | None:
        ex = self.extra
        return ex.get("tool") if ex else None

    @property
    def detail(self) -> str | None:
        ex = self.extra
        return ex.get("detail") if ex else None


class PendingLimitState(Base):
    """Persisted snapshot for an in-flight Human-in-the-Loop pause (quota suspension).

    Replaces the previous in-memory ``pending_by_conv`` dict so a suspended turn
    survives page refresh / process restart and can be resumed or stopped.
    One row per conversation (the most recent pause wins).
    """

    __tablename__ = "pending_limit_states"

    conversation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36))
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

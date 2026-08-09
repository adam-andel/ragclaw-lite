"""Conversation and Message ORM models."""

import json
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, Boolean, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.document import gen_uuid


def _next_message_seq(context) -> int:
    """Assign the next per-conversation ``Message.seq`` at INSERT time.

    A context-sensitive column default: SQLAlchemy invokes it once per inserted
    row with that row's pending parameters available, so EVERY insert path gets
    a correct seq without the caller having to remember. Keeping the assignment
    here (rather than at each call site) is what makes the
    "seq is dense and monotonic per conversation" invariant unconditional.

    Under asyncio the ORM flush runs inside a greenlet, so the synchronous
    ``context.connection`` API used below is valid.

    Rows flushed together are batched into ONE executemany, and every row's
    Python-side default is evaluated BEFORE that statement runs -- so a bare
    ``SELECT max(seq)`` would hand the whole batch the same number and trip the
    (conversation_id, seq) unique index. The batch shares this ExecutionContext,
    so the high-water mark is memoized on it and only the first row of a batch
    pays for the query. Rows from an EARLIER flush in the same transaction are
    already on the connection, hence visible to that query.
    """
    params = context.get_current_parameters()
    conv_id = params.get("conversation_id")
    if not conv_id:
        return 1

    cache = getattr(context, "_ragclaw_seq_hwm", None)
    if cache is None:
        cache = {}
        try:
            context._ragclaw_seq_hwm = cache
        except AttributeError:  # pragma: no cover - defensive, context is a plain object
            cache = None

    last = cache.get(conv_id) if cache is not None else None
    if last is None:
        table = Message.__table__
        last = int(
            context.connection.scalar(
                select(func.coalesce(func.max(table.c.seq), 0)).where(
                    table.c.conversation_id == conv_id
                )
            )
            or 0
        )

    nxt = last + 1
    if cache is not None:
        cache[conv_id] = nxt
    return nxt


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    title: Mapped[str] = mapped_column(String(500), default="New Conversation")
    kb_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_bases.id"), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Conversation compression: the oldest history is summarized to stay within
    # the context window. summary_text holds the accumulated compressed transcript.
    # Raw messages in the messages table are NEVER modified.
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cursor: messages with seq <= this value are already folded into the summary
    # and must NOT be replayed verbatim. Message seq values are contiguous from 0
    # (no edits/deletes), so this also serves as a positional index into the
    # seq-ordered history.
    summary_msg_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Display-only: how many L0 fold paragraphs have been pushed to vector/BM25
    # memory. There is no secondary summary tier -- archived folds are recalled
    # on demand instead of being re-summarized into an always-injected block.
    summary_archived_count: Mapped[int] = mapped_column(Integer, default=0)
    # Per-conversation pinned instruction. Always injected into the LLM system
    # prefix (a sacred, non-trimmable block) so it applies to every turn. Never
    # folded into summary_text and never written back by context compression.
    pinned_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.seq"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"), index=True)
    # Per-conversation monotonic ordering key, assigned by _next_message_seq at
    # insert time. `id` is a UUID (no ordering semantics) and `created_at` has no
    # tiebreaker, so seq is the ONLY reliable total order for history -- and the
    # carrier for the compression cursor. Unique per (conversation_id, seq).
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True, default=_next_message_seq)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    citations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-message content tokens (+4 per-message overhead), written at insert time.
    # Distinct from `token_count` (whole-turn prompt tokens, surfaced in the UI
    # capacity bar). Used by Layer-1 compression to locate the oldest 2/3 token
    # boundary by whole conversation rounds without re-encoding full history.
    content_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

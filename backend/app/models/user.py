"""User ORM model with password hashing."""

import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Enum as SAEnum, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.database import Base
from app.models.document import gen_uuid


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, values_callable=lambda e: [m.value for m in e]),
        default=UserRole.USER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Multi-tenant: each user belongs to a tenant; admin can see all
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Per-user dedicated Linux UID for the REPL sandbox isolation account.
    # Randomly assigned at creation, stored with a unique constraint. Every user
    # gets one, so the sandbox always has an exact UID to drop privileges to.
    # Expanding the UID range never touches existing rows.
    repl_uid: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)

    # User-authored free-text memory & preferences (manual, NOT MEM0-extracted).
    # Injected into the LLM system prompt as part of the task background so the
    # model can personalize. Deliberately kept separate from the auto-extracted
    # MEM0 memory graph (see agent_nodes.py).
    memory: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User's IANA timezone (e.g. "Asia/Shanghai", "America/New_York"). Persisted
    # from the profile so code execution and scheduling stamp files/times in the
    # user's locale instead of the sandbox container's default UTC. Nullable:
    # when unset the request-time browser-detected value (or UTC) is used.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

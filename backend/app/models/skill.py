"""Skill and MCPServer ORM models.

- Skill: folder-based skill index. Filesystem is source of truth, DB is cache.
  Stores folder_name + name + description(≤250 chars) for fast routing.
- MCPServer: registered MCP server definitions (unchanged).
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Boolean, Integer, false
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Skill(Base):
    """Folder-based skill index.

    The filesystem (data/skills/{folder_name}/SKILL.md) is the source of truth.
    This DB row is a cache for fast routing (Layer 1: name + description only).
    """
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    folder_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(250), nullable=True)  # strictly ≤250 chars for Layer 1 routing
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MCPServer(Base):
    """Registered MCP server definition (HTTP or stdio transport)."""
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    transport_type: Mapped[str] = mapped_column(String(20), nullable=False, default="http")  # http | stdio
    # HTTP
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # stdio
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args_json: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON array of args
    env_json: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON object of env vars
    # Common
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Marks platform-mandated servers (e.g. Python Executor) that are managed
    # by code/seed rather than the user UI. Built-in servers are hidden from
    # the MCP management list and protected from delete/rename via the API.
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

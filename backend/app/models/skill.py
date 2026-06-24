"""Skill, SkillTool, and MCPServer ORM models.

- Skill: prompt strategy profiles, NOT bound to KBs (KB selected per-conversation)
- SkillTool: many-to-many link between skills and MCP tools
- MCPServer: registered MCP server definitions
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Skill(Base):
    """A named prompt/tool profile. Does NOT bind to knowledge bases.

    KB is chosen by the user in conversation; the Skill provides
    system_prompt + tool bindings only.
    """
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tools: Mapped[list["SkillTool"]] = relationship(
        "SkillTool", back_populates="skill", cascade="all, delete-orphan"
    )


class SkillTool(Base):
    """Many-to-many link: skill ↔ (tool_name on a specific MCP server)."""
    __tablename__ = "skill_tools"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    skill_id: Mapped[str] = mapped_column(String(36), ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mcp_server_id: Mapped[str] = mapped_column(String(36), ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # tool-level overrides

    skill: Mapped["Skill"] = relationship("Skill", back_populates="tools")
    mcp_server: Mapped["MCPServer"] = relationship("MCPServer", back_populates="tool_links")


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tool_links: Mapped[list["SkillTool"]] = relationship(
        "SkillTool", back_populates="mcp_server", cascade="all, delete-orphan"
    )

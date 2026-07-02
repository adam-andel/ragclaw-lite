"""Pydantic schemas for folder-based Skill API."""

from datetime import datetime
from pydantic import BaseModel, Field


# ── Skill (folder-based) ──

class SkillCreate(BaseModel):
    """Online skill creation — generates SKILL.md + folder."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=250)
    mcp_servers: list[str] = Field(default_factory=list)
    is_active: bool = True
    body: str = Field("", description="SKILL.md markdown body (after front matter)")


class SkillUpdate(BaseModel):
    """Update SKILL.md content directly."""
    content: str = Field(..., description="Full SKILL.md content (front matter + body)")


class SkillResponse(BaseModel):
    """Skill info from DB index + SKILL.md content."""
    id: str
    tenant_id: str | None = None
    folder_name: str
    name: str
    description: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Parsed from SKILL.md
    mcp_servers: list[str] = []
    skill_md_content: str | None = None  # Full SKILL.md text

    model_config = {"from_attributes": True}


class SkillListResponse(BaseModel):
    items: list[SkillResponse]
    total: int
    page: int
    size: int


# ── Resource management ──

class ResourceFileInfo(BaseModel):
    name: str
    path: str
    size: int


class ResourceListResponse(BaseModel):
    scripts: list[ResourceFileInfo] = []
    data: list[ResourceFileInfo] = []
    references: list[ResourceFileInfo] = []
    _root: list[ResourceFileInfo] = []


class ResourceUploadResponse(BaseModel):
    path: str
    size: int


# ── Sync ──

class SyncResponse(BaseModel):
    added: int
    updated: int
    deactivated: int

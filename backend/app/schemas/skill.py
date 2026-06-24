"""Pydantic schemas for SKILL CRUD API."""

from datetime import datetime
from pydantic import BaseModel, Field


# ── Skill ──

class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    system_prompt: str = Field("", max_length=10000)
    is_active: bool = True


class SkillUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    system_prompt: str | None = Field(None, max_length=10000)
    is_active: bool | None = None


class SkillToolInfo(BaseModel):
    """Lightweight tool info embedded in Skill response."""
    id: str
    tool_name: str
    mcp_server_id: str
    mcp_server_name: str = ""

    model_config = {"from_attributes": True}


class SkillResponse(BaseModel):
    id: str
    tenant_id: str | None = None
    name: str
    description: str | None = None
    system_prompt: str
    is_active: bool
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    tools: list[SkillToolInfo] = []

    model_config = {"from_attributes": True}


# ── Skill-Tool binding ──

class SkillToolBindRequest(BaseModel):
    tool_name: str = Field(..., min_length=1)
    mcp_server_id: str = Field(...)
    config_json: str | None = None


class SkillToolBindResponse(BaseModel):
    id: str
    skill_id: str
    tool_name: str
    mcp_server_id: str


class SkillListResponse(BaseModel):
    items: list[SkillResponse]
    total: int
    page: int
    size: int

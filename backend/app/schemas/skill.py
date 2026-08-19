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
"""Pydantic schemas for folder-based Skill API."""

from datetime import datetime
from pydantic import BaseModel, Field


# ── Skill (folder-based) ──

class SkillCreate(BaseModel):
    """Online skill creation — generates SKILL.md + folder."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=250, description="≤250 chars for Layer 1 routing")
    mcp_servers: list[str] = Field(default_factory=list)
    is_active: bool = True
    body: str = Field("", description="SKILL.md markdown body (after front matter)")


class SkillUpdate(BaseModel):
    """Update SKILL.md content and/or the secret-zero API KEY."""
    content: str | None = Field(
        None, description="Full SKILL.md content (front matter + body). Omit to leave unchanged."
    )
    api_key: str | None = Field(
        None,
        description="Secret-zero API KEY for injection-proxy routing. Set to enable "
        "proxy injection; set to empty string to clear (fall back to vanilla). "
        "Omit to leave unchanged.",
    )


class SkillResponse(BaseModel):
    """Skill info from DB index + SKILL.md content."""
    id: str
    tenant_id: str | None = None
    folder_name: str
    name: str
    description: str | None = None
    is_active: bool
    enabled: bool = False  # FS truth: enable-symlink present on the shared skills volume
    created_at: datetime
    updated_at: datetime
    # Parsed from SKILL.md
    mcp_servers: list[str] = []
    skill_md_content: str | None = None  # Full SKILL.md text
    # Secret-zero: True when an injection-proxy API KEY is configured for this skill.
    api_key_configured: bool = False

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

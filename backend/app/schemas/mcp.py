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
"""Pydantic schemas for MCP Server CRUD API."""

from datetime import datetime
from pydantic import BaseModel, Field


class MCPServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    transport_type: str = Field("http", pattern=r"^(http|stdio)$")
    # HTTP
    endpoint: str | None = Field(None, max_length=500)
    # stdio
    command: str | None = Field(None, max_length=500)
    args_json: str | None = None
    env_json: str | None = None
    # Common
    timeout_seconds: int = Field(30, ge=1, le=300)
    is_active: bool = True


class MCPServerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    transport_type: str | None = Field(None, pattern=r"^(http|stdio)$")
    endpoint: str | None = Field(None, max_length=500)
    command: str | None = Field(None, max_length=500)
    args_json: str | None = None
    env_json: str | None = None
    timeout_seconds: int | None = Field(None, ge=1, le=300)
    is_active: bool | None = None


class MCPServerResponse(BaseModel):
    id: str
    tenant_id: str | None = None
    name: str
    transport_type: str
    endpoint: str | None = None
    command: str | None = None
    args_json: str | None = None
    env_json: str | None = None
    timeout_seconds: int
    is_active: bool
    is_builtin: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class MCPServerListResponse(BaseModel):
    items: list[MCPServerResponse]
    total: int
    page: int
    size: int

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
    created_at: datetime

    model_config = {"from_attributes": True}


class MCPServerListResponse(BaseModel):
    items: list[MCPServerResponse]
    total: int
    page: int
    size: int

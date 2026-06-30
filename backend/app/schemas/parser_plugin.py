"""Pydantic schemas for parser plugin management API."""

from datetime import datetime
from pydantic import BaseModel, Field


class PluginInfo(BaseModel):
    """Plugin metadata + current enabled state, returned by GET /api/plugins."""

    name: str
    display_name: str
    description: str
    category: str
    extensions: list[str]
    version: str
    enabled: bool
    disabled_by: str | None = None
    disabled_at: datetime | None = None
    reason: str | None = None


class PluginDisablePayload(BaseModel):
    reason: str | None = Field(None, max_length=500)


class PluginListResponse(BaseModel):
    items: list[PluginInfo]
    total: int

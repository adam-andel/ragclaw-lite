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

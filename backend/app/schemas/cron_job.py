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
"""Pydantic schemas for cron job API."""

from pydantic import BaseModel, Field, ConfigDict
from typing import Literal


class CronJobCreate(BaseModel):
    name: str = Field(..., max_length=200)
    description: str | None = None
    cron_expr: str = Field(..., max_length=100)
    timezone: str = Field(default="UTC", max_length=50)
    max_runs: int | None = Field(default=None, ge=1)
    task_content: str
    kb_id: str | None = None
    skill_id: str | None = None
    workspace_dir: str | None = None


class CronJobUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    cron_expr: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=50)
    max_runs: int | None = Field(default=None, ge=1)
    task_content: str | None = None
    kb_id: str | None = None
    skill_id: str | None = None
    workspace_dir: str | None = None


class CronJobResponse(BaseModel):
    id: str
    tenant_id: str | None
    user_id: str | None
    name: str
    description: str | None
    cron_expr: str
    timezone: str
    max_runs: int | None
    run_count: int
    task_content: str
    kb_id: str | None
    skill_id: str | None
    workspace_dir: str | None
    status: str
    next_run_at: str | None
    last_run_at: str | None
    last_result: str | None
    last_error: str | None
    created_at: str | None
    updated_at: str | None

    model_config = ConfigDict(from_attributes=True)


class CronJobListResponse(BaseModel):
    items: list[CronJobResponse]
    total: int
    page: int
    size: int


class CronJobRunResponse(BaseModel):
    id: str
    cron_job_id: str
    started_at: str | None
    finished_at: str | None
    status: str
    output: str | None
    result_json: str | None
    error: str | None

    model_config = ConfigDict(from_attributes=True)


class CronJobRunListResponse(BaseModel):
    items: list[CronJobRunResponse]
    total: int
    page: int
    size: int

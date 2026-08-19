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
"""Notification service for creating and managing user notifications."""

from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType
from app.models.cron_job import CronJob


async def create_notification(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str | None,
    title: str,
    content: str | None = None,
    type: NotificationType = NotificationType.SYSTEM,
    link: str | None = None,
) -> Notification:
    """Create a notification for a user."""
    notification = Notification(
        user_id=user_id,
        tenant_id=tenant_id,
        title=title,
        content=content,
        type=type,
        link=link,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


async def create_cron_job_notification(
    db: AsyncSession,
    job: CronJob,
    status: str,
    result: str | None,
    error: str | None,
) -> Notification | None:
    """Create a notification for the owner of a cron job after execution."""
    if not job.user_id:
        return None

    if status == "executed":
        title = f"Cron job executed: {job.name}"
        content = (result or "Task executed")[:10000]
    else:
        title = f"Cron job failed: {job.name}"
        content = (error or "Task failed")[:10000]

    return await create_notification(
        db,
        user_id=job.user_id,
        tenant_id=job.tenant_id,
        title=title,
        content=content,
        type=NotificationType.CRON_JOB,
        link=f"/cron-jobs?job={job.id}&logs=1",
    )

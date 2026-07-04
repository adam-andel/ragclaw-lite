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

    if status == "success":
        title = f"定时任务执行成功：{job.name}"
        content = (result or "任务已执行完成")[:500]
    else:
        title = f"定时任务执行失败：{job.name}"
        content = (error or "任务执行失败")[:500]

    return await create_notification(
        db,
        user_id=job.user_id,
        tenant_id=job.tenant_id,
        title=title,
        content=content,
        type=NotificationType.CRON_JOB,
        link=f"/cron-jobs",
    )

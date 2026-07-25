"""Cron job ORM models for scheduled agent tasks."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, Boolean, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.document import gen_uuid


class CronJobStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class CronJob(Base):
    """Scheduled task definition persisted in the database."""

    __tablename__ = "cron_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    cron_expr: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    max_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)

    task_content: Mapped[str] = mapped_column(Text, nullable=False)
    kb_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    skill_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workspace_dir: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="创建定时任务时对话中所选的工作目录（相对沙箱根路径）"
    )

    status: Mapped[CronJobStatus] = mapped_column(
        SAEnum(CronJobStatus, values_callable=lambda e: [m.value for m in e]),
        default=CronJobStatus.SCHEDULED,
    )

    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CronJobRun(Base):
    """Execution log for a single cron job run."""

    __tablename__ = "cron_job_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    cron_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("cron_jobs.id"), index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

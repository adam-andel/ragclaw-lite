"""Notification ORM model for in-app user notifications."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, ForeignKey, Boolean, Enum as SAEnum, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.document import gen_uuid


class NotificationType(str, enum.Enum):
    CRON_JOB = "cron_job"
    SYSTEM = "system"


class Notification(Base):
    """User notification persisted in the database."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("idx_notifications_user_read", "user_id", "read"),
        Index("idx_notifications_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[NotificationType] = mapped_column(
        SAEnum(NotificationType, values_callable=lambda e: [m.value for m in e]),
        default=NotificationType.SYSTEM,
    )
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

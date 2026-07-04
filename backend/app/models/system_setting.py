"""System-wide non-sensitive settings key-value store."""

from datetime import datetime, timezone

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SystemSetting(Base):
    """Single-row-per-setting table. Value is a JSON-serialized string."""

    __tablename__ = "system_settings"

    setting_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, default=_utc_iso, onupdate=_utc_iso)

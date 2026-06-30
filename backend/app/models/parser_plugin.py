"""Parser plugin enable/disable state (system-wide, admin-managed)."""

from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ParserPluginState(Base):
    """Records disabled state of built-in parsers.

    Convention: only rows for DISABLED plugins exist.
    Absence of a row = enabled (default). This keeps the table small and
    avoids the need to seed on every new parser addition.
    """

    __tablename__ = "parser_plugin_state"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    disabled_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

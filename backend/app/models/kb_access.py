"""KB-User access junction table."""

from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from app.database import Base
from app.models.document import gen_uuid


class KBUserAccess(Base):
    """Many-to-many: which users can access which knowledge bases."""
    __tablename__ = "kb_user_access"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    kb_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_bases.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

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
"""Refresh token ORM model (opaque, DB-backed, revocable, rotatable).

Refresh tokens are random opaque strings stored hashed (like passwords). Keeping
them in the DB (rather than as stateless JWTs) lets us revoke individual sessions
(logout, admin disable, password change) and rotate on every use.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # SHA-256 hex of the opaque refresh token. The raw token is only ever sent
    # to the client once; we never persist it.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Optional device/browser label for the session list UI.
    device: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @staticmethod
    def default_ttl() -> timedelta:
        # Refresh tokens live 30 days; rotated to a fresh value on every use.
        return timedelta(days=30)

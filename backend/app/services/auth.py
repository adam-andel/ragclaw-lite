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
"""JWT authentication utilities."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.models.refresh_token import RefreshToken
from app.services.config_manager import config_manager

# --- Config ---
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30  # 30 min; refreshed transparently via refresh token
REFRESH_TOKEN_TTL_DAYS = 30  # rotated to a fresh value on every use


def get_jwt_secret() -> str:
    """Return the JWT signing secret.

    Sourced from the DB-backed ConfigManager (auto-generated on first boot,
    rotated via the admin UI). No external mounted secret is required. The
    value is read from ConfigManager's in-memory cache, which is updated
    synchronously when the secret is rotated in the UI — so a rotation takes
    effect on the very next token sign/verify with zero backend restart.
    """
    secret = config_manager.jwt_secret
    if not secret:
        raise RuntimeError(
            "JWT secret is empty — ConfigManager failed to initialize it on startup."
        )
    return secret

security = HTTPBearer(auto_error=False)


# --- Password ---
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain[:72].encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- JWT Token ---
def create_access_token(user_id: str, username: str, role: str, tenant_id: str | None) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "tenant_id": tenant_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_jwt_secret(), algorithms=[ALGORITHM])
    except JWTError:
        return None


# --- Refresh tokens (opaque, DB-backed, revocable, rotatable) ---
def _hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_raw_refresh_token() -> str:
    """Generate a new opaque refresh token (returned to the client once)."""
    return secrets.token_urlsafe(48)


async def create_refresh_token(
    db: AsyncSession,
    user_id: str,
    raw: str,
    device: str | None = None,
) -> RefreshToken:
    """Persist a refresh token (hashed) and return the ORM row."""
    rt = RefreshToken(
        user_id=user_id,
        token_hash=_hash_refresh_token(raw),
        device=device,
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    )
    db.add(rt)
    await db.flush()
    return rt


async def rotate_refresh_token(
    db: AsyncSession,
    old: RefreshToken,
    device: str | None = None,
) -> tuple[RefreshToken, str]:
    """Revoke the old refresh token and issue a brand-new one (rotation).

    Returns ``(new_row, raw_token)``; the caller is responsible for committing
    and returning ``raw_token`` to the client exactly once.
    """
    old.is_revoked = True
    raw = issue_raw_refresh_token()
    new_rt = await create_refresh_token(db, old.user_id, raw, device=device)
    await db.flush()
    return new_rt, raw


async def verify_refresh_token(
    db: AsyncSession,
    raw: str,
    user_id: str | None = None,
) -> RefreshToken | None:
    """Validate a raw refresh token and return its (still-valid) row, else None.

    Checks hash match, not revoked, not expired, and (optionally) ownership.
    Does NOT revoke/rotate — callers decide (refresh rotates; logout revokes).
    """
    token_hash = _hash_refresh_token(raw)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    result = await db.execute(stmt)
    rt = result.scalar_one_or_none()
    if rt is None or rt.is_revoked:
        return None
    if rt.expires_at < datetime.utcnow():
        return None
    if user_id is not None and rt.user_id != user_id:
        return None
    return rt


async def revoke_refresh_token(db: AsyncSession, raw: str) -> bool:
    """Revoke a single refresh token (logout). Returns True if one was revoked."""
    rt = await verify_refresh_token(db, raw)
    if rt is None:
        return False
    rt.is_revoked = True
    await db.flush()
    return True


async def revoke_all_user_refresh_tokens(db: AsyncSession, user_id: str) -> int:
    """Revoke every refresh token for a user (logout-all / password change / disable).

    Returns the number of rows revoked.
    """
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.is_revoked == False  # noqa: E712
        )
    )
    rows = result.scalars().all()
    for rt in rows:
        rt.is_revoked = True
    await db.flush()
    return len(rows)


# --- FastAPI Dependencies ---
async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency: extract and validate the current user from Bearer token."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MISSING_AUTH_TOKEN")

    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_OR_EXPIRED_TOKEN")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_TOKEN")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="USER_NOT_FOUND_OR_DISABLED")

    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency: require admin role."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ADMIN_REQUIRED")
    return current_user


async def get_current_staff(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency: require admin or moderator."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MODERATOR):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="STAFF_REQUIRED")
    return current_user


def can_manage_user(actor: User, target: User) -> bool:
    """Check if actor can manage target user.
    ADMIN can manage everyone.
    MODERATOR can only manage USER role.
    """
    if actor.role == UserRole.ADMIN:
        return True
    if actor.role == UserRole.MODERATOR and target.role == UserRole.USER:
        return True
    return False


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Dependency: extract user if token present, else None (optional auth)."""
    if credentials is None:
        return None
    payload = decode_token(credentials.credentials)
    if payload is None:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

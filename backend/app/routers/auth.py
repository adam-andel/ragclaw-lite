"""Authentication routes: login, profile."""

import hashlib
import os
import re
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.user import (
    LoginRequest, RegisterRequest, SetupStatusResponse,
    TokenResponse, UserResponse, UserUpdateRequest,
    RefreshRequest, RefreshResponse,
)
from app.services.context_budget import check_field_budget
from app.services.auth import (
    hash_password, verify_password, create_access_token, get_current_user,
    issue_raw_refresh_token, create_refresh_token,
    verify_refresh_token, rotate_refresh_token, revoke_refresh_token,
    revoke_all_user_refresh_tokens,
)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# Structural allowlist for an IANA timezone string. Rejects path traversal
# ("..", leading "/") and anything outside [A-Za-z0-9_./-]. The REPL sandbox
# performs the authoritative validity check against its zoneinfo data, so this
# only guards against obviously malformed / injection-shaped input here.
_TZ_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


def _validate_timezone(tz: str | None) -> str | None:
    """Return a sanitized IANA timezone, or None to clear.

    Raises HTTPException(400) on a structurally invalid non-empty value.
    """
    if tz is None:
        return None
    tz = tz.strip()
    if not tz:
        return None
    if ".." in tz or tz.startswith("/") or not _TZ_RE.match(tz):
        raise HTTPException(400, "INVALID_TIMEZONE")
    return tz


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login and get access + refresh tokens."""
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="USER_DISABLED")

    token = create_access_token(user.id, user.username, user.role.value, user.tenant_id)
    raw_refresh = issue_raw_refresh_token()
    await create_refresh_token(db, user.id, raw_refresh)
    await db.commit()
    return TokenResponse(
        access_token=token,
        refresh_token=raw_refresh,
        user=UserResponse.model_validate(user),
    )


@router.get("/setup", response_model=SetupStatusResponse)
async def setup_status(db: AsyncSession = Depends(get_db)):
    """Public: report whether the system still needs its first admin (no users).

    Safe against the startup race where the request lands before init_db() has
    created the users table yet (e.g. right after a dev reload). Rather than
    500 on "no such table", we treat an unreadable user store as "not set up" --
    strictly safe: setup mode never destroys data and the first admin
    registration will itself trigger table creation.
    """
    try:
        total = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    except Exception:
        # Most likely sqlalchemy.exc.OperationalError: no such table: users
        # during early startup. Fail safe to setup mode.
        total = 0
    return SetupStatusResponse(needs_setup=total == 0)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access + refresh token pair.

    The presented refresh token is rotated (revoked and replaced) on every use,
    so a leaked token has a very short useful window and replays are detected.
    """
    rt = await verify_refresh_token(db, data.refresh_token)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="REFRESH_TOKEN_INVALID",
        )

    # Load the user to (re)issue an access token with current claims.
    user = await db.get(User, rt.user_id)
    if user is None or not user.is_active:
        rt.is_revoked = True
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="REFRESH_TOKEN_USER_GONE",
        )

    new_rt, new_raw = await rotate_refresh_token(db, rt, device=data.device)
    new_access = create_access_token(
        user.id, user.username, user.role.value, user.tenant_id
    )
    await db.commit()
    return RefreshResponse(
        access_token=new_access,
        refresh_token=new_raw,
    )


@router.post("/logout")
async def logout(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Revoke a single refresh token (current session)."""
    revoked = await revoke_refresh_token(db, data.refresh_token)
    await db.commit()
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="REFRESH_TOKEN_INVALID",
        )
    return {"detail": "LOGGED_OUT"}


@router.post("/logout-all")
async def logout_all(current_user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    """Revoke every refresh token for the current user (all sessions)."""
    count = await revoke_all_user_refresh_tokens(db, current_user.id)
    await db.commit()
    return {"detail": "LOGGED_OUT_ALL", "revoked": count}


@router.post("/register", response_model=TokenResponse)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Bootstrap the first user as the super admin.

    Public, but only succeeds while the user table is empty. Once any account
    exists, self-registration is closed (admins create further users via
    ``/api/users``). The first registrant is always granted the ADMIN role and
    the reserved bootstrap REPL UID, matching the design contract that the
    bootstrap admin owns ``repl_uid_range_min``.
    """
    total = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    if total > 0:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="SETUP_ALREADY_COMPLETE")

    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="USER_NAME_EXISTS")

    # Deterministic UUID + reserved bootstrap UID keep the first admin stable
    # across restarts and consistent with the previously auto-seeded admin.
    admin_user_id = str(uuid.UUID(hashlib.md5(b"ragclaw-default-admin-user").hexdigest()))
    user = User(
        id=admin_user_id,
        username=data.username,
        hashed_password=hash_password(data.password),
        display_name=data.display_name or data.username,
        email=data.email,
        role=UserRole.ADMIN,
        is_active=True,
        tenant_id=data.tenant_id or settings.default_tenant_id,
        repl_uid=settings.repl_uid_range_min,  # reserved bootstrap admin UID
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(user.id, user.username, user.role.value, user.tenant_id)
    raw_refresh = issue_raw_refresh_token()
    await create_refresh_token(db, user.id, raw_refresh)
    await db.commit()
    return TokenResponse(
        access_token=token,
        refresh_token=raw_refresh,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return UserResponse.model_validate(current_user)


@router.put("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user profile."""
    warnings: list[dict] = []
    if data.display_name is not None:
        current_user.display_name = data.display_name
    if data.email is not None:
        current_user.email = data.email
    if data.memory is not None:
        current_user.memory = data.memory
        # Config-time budget check: profile memory ships in the system prefix on
        # every turn, so an oversized one permanently shrinks the room left for
        # history and retrieval. Non-blocking -- the save stands, we only report.
        warnings = check_field_budget(current_user.memory, "user_memory")
    if data.timezone is not None:
        # Persist the user's locale so code execution / scheduling stamp times
        # in their timezone instead of the sandbox container's default UTC.
        current_user.timezone = _validate_timezone(data.timezone)
    if data.password is not None:
        current_user.hashed_password = hash_password(data.password)
    await db.commit()
    await db.refresh(current_user)
    resp = UserResponse.model_validate(current_user)
    resp.warnings = warnings
    return resp


MAX_AVATAR_SIZE = 1 * 1024 * 1024  # 1MB


def _avatar_dir() -> Path:
    from app.config import settings
    d = settings.project_root / "frontend" / "dist" / "avatar"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/me/avatar", response_model=UserResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom avatar image (max 1MB, image types only)."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="AVATAR_MUST_BE_IMAGE")

    contents = await file.read()
    if len(contents) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=400, detail=f"AVATAR_TOO_LARGE_MAX_MB_{MAX_AVATAR_SIZE // 1024 // 1024}")

    # Delete old avatar file if exists
    if current_user.avatar_url:
        old_path = _avatar_dir() / Path(current_user.avatar_url).name
        try:
            os.remove(old_path)
        except OSError:
            pass

    # Determine extension from content type
    ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
    ext = ext_map.get(file.content_type, ".jpg")
    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = _avatar_dir() / filename

    with open(filepath, "wb") as f:
        f.write(contents)

    current_user.avatar_url = f"/avatar/{filename}"
    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


@router.delete("/me/avatar", response_model=UserResponse)
async def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove custom avatar, fall back to emoji."""
    if current_user.avatar_url:
        old_path = _avatar_dir() / Path(current_user.avatar_url).name
        try:
            os.remove(old_path)
        except OSError:
            pass
        current_user.avatar_url = None
        await db.commit()
        await db.refresh(current_user)
    return UserResponse.model_validate(current_user)

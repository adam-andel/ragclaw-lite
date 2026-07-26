"""User management routes (admin/moderator)."""

import uuid
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, allocate_repl_uid
from app.models.user import User, UserRole
from app.schemas.user import UserResponse, UserListResponse, UserCreateRequest, UserUpdateRequest
from app.services.auth import hash_password, get_current_staff, can_manage_user
from app.services.config_manager import config_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["Users"])


async def _delete_repl_user_dir(repl_uid: int) -> None:
    """Delete a user's sandbox base directory on the mcp-repl service.

    Best-effort-strict: called BEFORE the caller releases the UID so the UID
    cannot be reused while stale files linger (cleanup-before-reuse guarantee).
    Raises on failure so the caller can block the deletion. Auth + per-user
    isolation are always on, so the sandbox directory is always cleaned up.
    """
    secret = config_manager.repl_auth_secret
    url = f"{settings.mcp_repl_internal_url.rstrip('/')}/user/{int(repl_uid)}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(url, headers={"X-Repl-Auth": secret})
        resp.raise_for_status()


@router.get("", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    search: str | None = None,
    is_active: bool | None = Query(None),
    role: str | None = Query(None),
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """List users (paginated). ADMIN and MODERATOR can both see all users
    (including admin/moderator roles). Modifying/deleting remains restricted by
    can_manage_user + update_user/delete_user role boundaries.
    Optional `search` filters by username or display_name (case-insensitive).
    Optional `is_active` filters by account enabled/disabled state.
    Optional `role` filters by role (admin/moderator/user).
    Returns `{items, total, page, size}`."""
    conditions = []
    if search:
        like = f"%{search}%"
        conditions.append(or_(User.username.ilike(like), User.display_name.ilike(like)))
    if is_active is not None:
        conditions.append(User.is_active == is_active)
    if role:
        if role not in (r.value for r in UserRole):
            raise HTTPException(400, "Invalid role")
        conditions.append(User.role == UserRole(role))

    count_q = select(func.count()).select_from(User)
    if conditions:
        count_q = count_q.where(*conditions)
    total = (await db.execute(count_q)).scalar() or 0

    items_q = select(User).order_by(User.created_at.desc())
    if conditions:
        items_q = items_q.where(*conditions)
    items_q = items_q.offset((page - 1) * size).limit(size)
    result = await db.execute(items_q)
    items = [UserResponse.model_validate(u) for u in result.scalars().all()]
    return UserListResponse(items=items, total=total, page=page, size=size)


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    data: UserCreateRequest,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Create user. MODERATOR can only create USER role."""
    if data.role and data.role not in ("user", "admin", "moderator"):
        raise HTTPException(400, "Invalid role")
    target_role = UserRole(data.role) if data.role else UserRole.USER
    if current_user.role == UserRole.MODERATOR and target_role != UserRole.USER:
        raise HTTPException(403, "USER_CREATE_ROLE")

    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "USER_NAME_EXISTS")

    # Allocate a dedicated REPL sandbox UID and commit the whole user row.
    # Retry the commit on a (rare) unique-constraint collision; if all retries
    # fail the pool is exhausted and we surface a clear 503.
    created = None
    last_err: Exception | None = None
    for _ in range(settings.repl_uid_alloc_retries):
        try:
            taken = (await db.execute(select(User.repl_uid))).scalars().all()
            taken_set = {u for u in taken if u is not None}
            cand = allocate_repl_uid(taken_set)
            # Regression guard: REPL_UID_RANGE_MIN is permanently reserved for the
            # bootstrap admin (see app/database.py _seed_admin_user, which uses this
            # fixed UID). A normal user must NEVER receive it, or it would collide
            # with the admin's sandbox. allocate_repl_uid's contract only returns
            # [MIN+1, MAX); if someone changes the range to include MIN, this
            # assertion fails immediately.
            assert cand != settings.repl_uid_range_min, (
                f"allocate_repl_uid returned the admin-reserved UID "
                f"({settings.repl_uid_range_min}); it must only hand out [MIN+1, MAX)"
            )
            user = User(
                id=str(uuid.uuid4()),
                username=data.username,
                hashed_password=hash_password(data.password),
                display_name=data.display_name or data.username,
                email=data.email,
                role=target_role,
                tenant_id=str(uuid.uuid4()),
                repl_uid=cand,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            created = user
            break
        except IntegrityError:
            await db.rollback()
            last_err = "repl_uid collision on commit"
            continue

    if created is None:
        raise HTTPException(
            503,
            f"USER_UID_POOL_EXHAUSTED: 无法为新建用户分配沙盒隔离 UID：UID 池可能已耗尽，请扩大 REPL_UID_RANGE_MAX。",
        )
    return UserResponse.model_validate(created)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "USER_NOT_FOUND")
    if not can_manage_user(current_user, user):
        raise HTTPException(403, "USER_NO_MANAGE_PERM")
    return UserResponse.model_validate(user)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    data: UserUpdateRequest,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "USER_NOT_FOUND")
    if not can_manage_user(current_user, user):
        raise HTTPException(403, "USER_NO_MANAGE_PERM")

    if data.display_name is not None:
        user.display_name = data.display_name
    if data.email is not None:
        user.email = data.email
    if data.role is not None:
        new_role = UserRole(data.role)
        if current_user.role == UserRole.MODERATOR and new_role != UserRole.USER:
            raise HTTPException(403, "USER_ROLE_PERMISSION")
        user.role = new_role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.hashed_password = hash_password(data.password)

    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    if user_id == current_user.id:
        raise HTTPException(400, "USER_CANNOT_DELETE_SELF")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "USER_NOT_FOUND")
    if not can_manage_user(current_user, user):
        raise HTTPException(403, "USER_NO_DELETE_PERM")

    # Capture the UID BEFORE releasing it. Delete the user's sandbox base
    # directory first (cleanup-before-reuse): if the sandbox is unreachable the
    # deletion is blocked so the UID cannot be reassigned to a new user while
    # stale files linger. Every user has a dedicated UID, so there is always a
    # directory to clean up.
    repl_uid = user.repl_uid
    if repl_uid is not None:
        try:
            await _delete_repl_user_dir(repl_uid)
        except Exception as e:
            logger.warning("repl_dir_cleanup_failed user=%s uid=%s err=%s", user_id, repl_uid, e)
            raise HTTPException(
                502,
                f"USER_SANDBOX_CLEANUP_FAILED: 无法清理用户 {user_id} 的沙盒目录（mcp-repl 可能不可用），用户未删除。"
                f"请排查 mcp-repl 服务后重试。原始错误: {e}",
            )

    await db.delete(user)
    await db.commit()
    return {"status": "deleted"}

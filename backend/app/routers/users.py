"""User management routes (admin/moderator)."""

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.user import UserResponse, UserCreateRequest, UserUpdateRequest
from app.services.auth import hash_password, get_current_staff, can_manage_user

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("", response_model=list[UserResponse])
async def list_users(
    search: str | None = None,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """List users. ADMIN sees all, MODERATOR sees only USER role.
    Optional `search` filters by username or display_name (case-insensitive)."""
    query = select(User)
    if current_user.role == UserRole.ADMIN:
        pass
    else:
        query = query.where(User.role == UserRole.USER)
    if search:
        like = f"%{search}%"
        query = query.where(or_(User.username.ilike(like), User.display_name.ilike(like)))
    query = query.order_by(User.created_at.desc())
    result = await db.execute(query)
    return [UserResponse.model_validate(u) for u in result.scalars().all()]


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
        raise HTTPException(403, "普通管理员只能创建普通用户")

    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "用户名已存在")

    user = User(
        id=str(uuid.uuid4()),
        username=data.username,
        hashed_password=hash_password(data.password),
        display_name=data.display_name or data.username,
        email=data.email,
        role=target_role,
        tenant_id=str(uuid.uuid4()),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "用户不存在")
    if not can_manage_user(current_user, user):
        raise HTTPException(403, "无权管理该用户")
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
        raise HTTPException(404, "用户不存在")
    if not can_manage_user(current_user, user):
        raise HTTPException(403, "无权管理该用户")

    if data.display_name is not None:
        user.display_name = data.display_name
    if data.email is not None:
        user.email = data.email
    if data.role is not None:
        new_role = UserRole(data.role)
        if current_user.role == UserRole.MODERATOR and new_role != UserRole.USER:
            raise HTTPException(403, "普通管理员只能设置普通用户角色")
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
        raise HTTPException(400, "不能删除自己")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "用户不存在")
    if not can_manage_user(current_user, user):
        raise HTTPException(403, "无权删除该用户")
    await db.delete(user)
    await db.commit()
    return {"status": "deleted"}

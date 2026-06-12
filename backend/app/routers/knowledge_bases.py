"""Knowledge Base CRUD API — with sharing support."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.kb_access import KBUserAccess
from app.models.user import User
from app.schemas.document import KBResponse, KBCreate
from app.schemas.user import UserResponse
from app.services.auth import get_current_user, get_current_admin
from app.services.vector_store import vector_store

router = APIRouter(prefix="/api/kb", tags=["Knowledge Bases"])


def _check_kb_access(user: User, kb: KnowledgeBase):
    """Admin always has access; owner always has access; shared users checked via DB."""
    if user.role.value == "admin" or kb.owner_id == user.id:
        return


async def _user_has_kb_access(user_id: str, kb_id: str, db: AsyncSession) -> bool:
    """Check if a user has access to a KB (owner or shared)."""
    result = await db.execute(
        select(KBUserAccess).where(
            and_(KBUserAccess.kb_id == kb_id, KBUserAccess.user_id == user_id)
        )
    )
    return result.scalar_one_or_none() is not None


@router.post("", response_model=KBResponse, status_code=201)
async def create_kb(data: KBCreate, current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    kb = KnowledgeBase(
        id=str(uuid.uuid4()), name=data.name, description=data.description,
        tenant_id=current_user.tenant_id, owner_id=current_user.id,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return kb


@router.get("", response_model=list[KBResponse])
async def list_kbs(current_user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    """List KBs — admin: all; user: owned + shared."""
    if current_user.role.value == "admin":
        result = await db.execute(
            select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
        )
        return result.scalars().all()

    # User: owned KBs
    owned_result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.owner_id == current_user.id)
    )
    owned = list(owned_result.scalars().all())

    # User: shared KBs
    shared_result = await db.execute(
        select(KnowledgeBase).join(KBUserAccess).where(
            KBUserAccess.user_id == current_user.id
        )
    )
    shared = list(shared_result.scalars().all())

    # Merge, deduplicate by id
    seen = set()
    merged = []
    for kb in owned + shared:
        if kb.id not in seen:
            seen.add(kb.id)
            merged.append(kb)
    return sorted(merged, key=lambda k: k.created_at, reverse=True)


@router.get("/{kb_id}", response_model=KBResponse)
async def get_kb(kb_id: str, current_user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        if not await _user_has_kb_access(current_user.id, kb_id, db):
            raise HTTPException(403, "无权访问")
    return kb


@router.delete("/{kb_id}")
async def delete_kb(kb_id: str, current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        raise HTTPException(403, "只能删除自己创建的知识库")

    vector_store.delete_collection(kb_id)
    await db.delete(kb)
    await db.commit()
    return {"status": "deleted"}


# ===== Sharing (admin only) =====

@router.get("/{kb_id}/users", response_model=list[UserResponse])
async def list_kb_users(kb_id: str, current_user: User = Depends(get_current_admin),
                        db: AsyncSession = Depends(get_db)):
    """List users who have access to this KB (admin only)."""
    result = await db.execute(
        select(User).join(KBUserAccess).where(KBUserAccess.kb_id == kb_id)
    )
    return [UserResponse.model_validate(u) for u in result.scalars().all()]


@router.post("/{kb_id}/users/{user_id}")
async def add_kb_user(kb_id: str, user_id: str,
                      current_user: User = Depends(get_current_admin),
                      db: AsyncSession = Depends(get_db)):
    """Grant a user access to this KB (admin only)."""
    # Check KB exists
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    # Check user exists
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    # Check not already added
    existing = await db.execute(
        select(KBUserAccess).where(
            and_(KBUserAccess.kb_id == kb_id, KBUserAccess.user_id == user_id)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "该用户已有访问权限")

    access = KBUserAccess(id=str(uuid.uuid4()), kb_id=kb_id, user_id=user_id)
    db.add(access)
    await db.commit()
    return {"status": "granted", "kb_id": kb_id, "user_id": user_id}


@router.delete("/{kb_id}/users/{user_id}")
async def remove_kb_user(kb_id: str, user_id: str,
                         current_user: User = Depends(get_current_admin),
                         db: AsyncSession = Depends(get_db)):
    """Revoke a user's access to this KB (admin only)."""
    result = await db.execute(
        select(KBUserAccess).where(
            and_(KBUserAccess.kb_id == kb_id, KBUserAccess.user_id == user_id)
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise HTTPException(404, "该用户没有访问权限")
    await db.delete(access)
    await db.commit()
    return {"status": "revoked"}

"""Knowledge Base CRUD API — protected with auth & tenant isolation."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.document import KBResponse, KBCreate
from app.services.auth import get_current_user
from app.services.vector_store import vector_store

router = APIRouter(prefix="/api/kb", tags=["Knowledge Bases"])


@router.post("", response_model=KBResponse, status_code=201)
async def create_kb(
    data: KBCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = KnowledgeBase(
        id=str(uuid.uuid4()),
        name=data.name,
        description=data.description,
        tenant_id=current_user.tenant_id,
        owner_id=current_user.id,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return kb


@router.get("", response_model=list[KBResponse])
async def list_kbs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List KBs — admin sees all, user sees own tenant only."""
    if current_user.role.value == "admin":
        result = await db.execute(
            select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
        )
    else:
        result = await db.execute(
            select(KnowledgeBase)
            .where(
                or_(
                    KnowledgeBase.tenant_id == current_user.tenant_id,
                    KnowledgeBase.owner_id == current_user.id,
                )
            )
            .order_by(KnowledgeBase.created_at.desc())
        )
    return result.scalars().all()


@router.get("/{kb_id}", response_model=KBResponse)
async def get_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "知识库不存在")
    # Tenant check
    if current_user.role.value != "admin" and kb.tenant_id != current_user.tenant_id:
        raise HTTPException(403, "无权访问")
    return kb


@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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

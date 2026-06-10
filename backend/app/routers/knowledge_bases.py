"""Knowledge Base CRUD API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.schemas.document import KBResponse, KBCreate
from app.services.vector_store import vector_store

router = APIRouter(prefix="/api/kb", tags=["Knowledge Bases"])


@router.post("", response_model=KBResponse, status_code=201)
async def create_kb(
    data: KBCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new knowledge base."""
    kb = KnowledgeBase(
        id=str(uuid.uuid4()),
        name=data.name,
        description=data.description,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return kb


@router.get("", response_model=list[KBResponse])
async def list_kbs(db: AsyncSession = Depends(get_db)):
    """List all knowledge bases."""
    result = await db.execute(
        select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{kb_id}", response_model=KBResponse)
async def get_kb(kb_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific knowledge base."""
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "Knowledge base not found")
    return kb


@router.delete("/{kb_id}")
async def delete_kb(kb_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a knowledge base and all associated data."""
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "Knowledge base not found")

    # Delete vector collection
    vector_store.delete_collection(kb_id)

    # Delete from DB (cascade deletes documents and chunks)
    await db.delete(kb)
    await db.commit()

    return {"status": "deleted"}

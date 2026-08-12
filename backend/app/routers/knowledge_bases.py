"""Knowledge Base CRUD API — with sharing support and m2m documents."""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.kb_access import KBUserAccess
from app.models.document import Document, Chunk, KBDocument
from app.models.user import User
from app.schemas.document import (
    KBResponse, KBCreate, KBUpdate,
    DocKBLinkRequest, DocKBLinkResponse,
    DocumentResponse,
)
from app.schemas.user import UserResponse
from app.services.auth import get_current_user, get_current_staff
from app.services.context_budget import check_field_budget
from app.services.vector_store import vector_store
from app.services.cache import answer_cache

router = APIRouter(prefix="/api/kb", tags=["Knowledge Bases"])


def _gen_id() -> str:
    return str(uuid.uuid4())


async def _user_has_kb_access(user_id: str, kb_id: str, db: AsyncSession) -> bool:
    result = await db.execute(
        select(KBUserAccess).where(
            and_(KBUserAccess.kb_id == kb_id, KBUserAccess.user_id == user_id)
        )
    )
    return result.scalar_one_or_none() is not None


async def _get_kb_stats(kb_id: str, db: AsyncSession) -> tuple[int, int]:
    """Return (doc_count, vector_count) for a knowledge base."""
    doc_count_q = select(func.count()).select_from(KBDocument).where(KBDocument.kb_id == kb_id)
    doc_count = (await db.execute(doc_count_q)).scalar() or 0
    try:
        vec_count = vector_store.count(kb_id)
    except Exception:
        vec_count = 0
    return doc_count, vec_count


async def _kb_to_response(
    kb: KnowledgeBase, db: AsyncSession, warnings: list[dict] | None = None
) -> KBResponse:
    doc_count, vec_count = await _get_kb_stats(kb.id, db)
    return KBResponse(
        id=kb.id, name=kb.name, description=kb.description, prompt=kb.prompt,
        doc_count=doc_count, vector_count=vec_count,
        created_at=kb.created_at, updated_at=kb.updated_at,
        warnings=warnings or [],
    )

async def _rebuild_kb_bm25(db: AsyncSession, kb_id: str):
    """Full rebuild of a KB's BM25 index from every linked chunk.

    Includes both COMPLETED (has vectors) and CHUNKED (keyword-only) documents.
    This replaces the per-link partial rebuild that previously overwrote the
    index with only the most recently added document's chunks.
    """
    from sqlalchemy import select as _select, and_ as _and_
    from app.models.document import Chunk, Document, DocStatus, KBDocument
    from app.services.bm25_index import bm25_index

    chunks_result = await db.execute(
        _select(Chunk).join(Document, Chunk.doc_id == Document.id).join(
            KBDocument, _and_(KBDocument.doc_id == Document.id, KBDocument.kb_id == kb_id)
        ).where(
            Document.status.in_([DocStatus.COMPLETED, DocStatus.CHUNKED]),
            Chunk.content != "",
        )
    )
    chunks = chunks_result.scalars().all()
    if not chunks:
        bm25_index.delete_kb(kb_id)
        return
    doc_ids = {c.doc_id for c in chunks}
    doc_result = await db.execute(
        _select(Document.id, Document.filename).where(Document.id.in_(doc_ids))
    )
    doc_map = {row[0]: row[1] for row in doc_result.fetchall()}
    bm25_index.build(kb_id, [
        {
            "id": c.id, "content": c.content, "doc_id": c.doc_id,
            "heading": c.heading or "", "chunk_index": c.chunk_index,
            "page": c.page, "filename": doc_map.get(c.doc_id, ""),
        }
        for c in chunks
    ])


@router.post("", response_model=KBResponse, status_code=201)
async def create_kb(
    data: KBCreate, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb = KnowledgeBase(
        id=_gen_id(), name=data.name, description=data.description, prompt=data.prompt,
        tenant_id=current_user.tenant_id, owner_id=current_user.id,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return await _kb_to_response(kb, db)


@router.get("", response_model=list[KBResponse])
async def list_kbs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role.value == "admin":
        result = await db.execute(
            select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
        )
        kbs = result.scalars().all()
    else:
        owned_result = await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.owner_id == current_user.id)
        )
        owned = list(owned_result.scalars().all())
        shared_result = await db.execute(
            select(KnowledgeBase).join(KBUserAccess).where(
                KBUserAccess.user_id == current_user.id
            )
        )
        shared = list(shared_result.scalars().all())
        seen = set()
        kbs = []
        for kb in owned + shared:
            if kb.id not in seen:
                seen.add(kb.id)
                kbs.append(kb)
        kbs.sort(key=lambda k: k.created_at, reverse=True)

    return [await _kb_to_response(kb, db) for kb in kbs]


@router.get("/{kb_id}", response_model=KBResponse)
async def get_kb(
    kb_id: str, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        if not await _user_has_kb_access(current_user.id, kb_id, db):
            raise HTTPException(403, "无权访问")
    return await _kb_to_response(kb, db)


@router.patch("/{kb_id}", response_model=KBResponse)
async def update_kb(
    kb_id: str, data: KBUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename knowledge base or update description."""
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        raise HTTPException(403, "只能修改自己创建的知识库")

    warnings: list[dict] = []
    if data.name is not None:
        kb.name = data.name
    if data.description is not None:
        kb.description = data.description
    if data.prompt is not None:
        kb.prompt = data.prompt
        # The instruction changed → cached answers were generated under the old
        # prompt and must not be served. Bust this KB's cache.
        answer_cache.invalidate(kb.id)
        # Config-time budget check: this instruction is prepended to the system
        # prefix on every turn that hits this KB, so an oversized one silently
        # shrinks the room left for history and retrieval. Non-blocking -- the
        # save has already happened, we only report.
        warnings = check_field_budget(kb.prompt, "kb_prompt")
    await db.commit()
    await db.refresh(kb)
    return await _kb_to_response(kb, db, warnings)


@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: str, current_user: User = Depends(get_current_user),
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

# ---- Document linking (m2m) ----

@router.get("/{kb_id}/documents", response_model=list[DocumentResponse])
async def list_kb_documents(
    kb_id: str, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List documents in this knowledge base."""
    from app.routers.documents import _model_to_response, _get_doc_kb_ids
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    # gid: admin / KB owner / KB members may list (mirrors get_kb idiom)
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        if not await _user_has_kb_access(current_user.id, kb_id, db):
            raise HTTPException(403, "无权访问")

    result = await db.execute(
        select(KBDocument).where(KBDocument.kb_id == kb_id).order_by(KBDocument.added_at.desc())
    )
    links = result.scalars().all()
    items = []
    for link in links:
        doc = await db.get(Document, link.doc_id)
        if doc:
            kb_ids = await _get_doc_kb_ids(doc.id, db)
            items.append(_model_to_response(doc, kb_ids))
    return items


@router.post("/{kb_id}/documents", response_model=DocKBLinkResponse)
async def add_documents_to_kb(
    kb_id: str, body: DocKBLinkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Select existing documents to add to this knowledge base.
    Uses cached embeddings from Chunk table to avoid recomputation."""
    from app.services.vector_store import vector_store
    from app.services.bm25_index import bm25_index
    import struct

    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        raise HTTPException(403, "只能修改自己的知识库")

    added = 0
    skipped = 0
    loop = asyncio.get_running_loop()

    for doc_id in body.doc_ids:
        doc = await db.get(Document, doc_id)
        if not doc or doc.status.value not in ("completed", "chunked"):
            skipped += 1
            continue

        existing = await db.execute(
            select(KBDocument).where(
                and_(KBDocument.kb_id == kb_id, KBDocument.doc_id == doc_id)
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        link = KBDocument(id=_gen_id(), kb_id=kb_id, doc_id=doc_id)
        db.add(link)

        # Load chunks to (optionally) push vectors to KB collection
        chunks_result = await db.execute(
            select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.chunk_index)
        )
        chunks = chunks_result.scalars().all()

        if chunks and chunks[0].embedding is not None:
            # Fast path: use cached embeddings (doc is COMPLETED)
            chunk_dicts = []
            for c in chunks:
                emb_bytes = c.embedding
                emb_list = list(struct.unpack(f"{len(emb_bytes)//4}f", emb_bytes))
                chunk_dicts.append({
                    "id": c.id, "content": c.content,
                    "embedding": emb_list,
                    "doc_id": c.doc_id, "chunk_index": c.chunk_index,
                    "heading": c.heading or "", "page": c.page,
                    "token_count": c.token_count, "filename": doc.filename,
                })
            await loop.run_in_executor(None, vector_store.add_chunks_cached, kb_id, chunk_dicts)
        # CHUNKED docs (embedding is None) are intentionally NOT pushed to the
        # vector store — they are retrievable via BM25/keyword search only, until
        # an embedding model is installed and the document is re-indexed.

        # Rebuild BM25 for the WHOLE KB (not just this doc). This fixes a latent
        # bug where each link overwrote the KB index with only the new doc's
        # chunks, and it naturally includes CHUNKED docs (keyword retrieval).
        try:
            await _rebuild_kb_bm25(db, kb_id)
        except Exception:
            pass

        added += 1

    await db.commit()
    return DocKBLinkResponse(added=added, skipped=skipped)


@router.delete("/{kb_id}/documents/{doc_id}")
async def remove_document_from_kb(
    kb_id: str, doc_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a document from this knowledge base (document itself is NOT deleted)."""
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        raise HTTPException(403, "只能修改自己的知识库")

    result = await db.execute(
        select(KBDocument).where(
            and_(KBDocument.kb_id == kb_id, KBDocument.doc_id == doc_id)
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(404, "文档不在该知识库中")

    from app.services.vector_store import vector_store
    from app.services.bm25_index import bm25_index
    vector_store.delete_by_doc(kb_id, doc_id)
    try:
        bm25_index.remove_doc(kb_id, doc_id)
    except Exception:
        pass

    await db.delete(link)
    await db.commit()
    return {"status": "removed"}


@router.post("/{kb_id}/documents/batch", response_model=DocKBLinkResponse)
async def batch_add_documents_to_kb(
    kb_id: str, body: DocKBLinkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Batch add documents to KB (same as single but with explicit batch name)."""
    return await add_documents_to_kb(kb_id, body, current_user, db)

# ===== Sharing (admin only) =====

@router.get("/{kb_id}/users", response_model=list[UserResponse])
async def list_kb_users(kb_id: str, current_user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """List users who have access to this KB (KB owner only)."""
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        raise HTTPException(403, "只能查看自己创建的知识库")
    result = await db.execute(
        select(User).join(KBUserAccess).where(KBUserAccess.kb_id == kb_id)
    )
    return [UserResponse.model_validate(u) for u in result.scalars().all()]


@router.post("/{kb_id}/users/{user_id}")
async def add_kb_user(kb_id: str, user_id: str,
                      current_user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """Grant a user access to this KB (KB owner only)."""
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        raise HTTPException(403, "只能管理自己创建的知识库")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    existing = await db.execute(
        select(KBUserAccess).where(
            and_(KBUserAccess.kb_id == kb_id, KBUserAccess.user_id == user_id)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "该用户已有访问权限")
    access = KBUserAccess(id=_gen_id(), kb_id=kb_id, user_id=user_id)
    db.add(access)
    await db.commit()
    return {"status": "granted", "kb_id": kb_id, "user_id": user_id}


@router.delete("/{kb_id}/users/{user_id}")
async def remove_kb_user(kb_id: str, user_id: str,
                         current_user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """Revoke a user's access to this KB (KB owner only)."""
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if current_user.role.value != "admin" and kb.owner_id != current_user.id:
        raise HTTPException(403, "只能管理自己创建的知识库")
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
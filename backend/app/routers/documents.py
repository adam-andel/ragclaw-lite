"""Document upload & management API routes — protected with auth."""

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.models.document import Document, Chunk, DocStatus
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.document import DocumentResponse, DocumentStatusResponse, ChunkResponse
from app.services.auth import get_current_user, get_current_staff
from app.services.parser import parser_service
from app.services.chunker import chunker_service
from app.services.vector_store import vector_store
from app.services.bm25_index import bm25_index

router = APIRouter(prefix="/api/documents", tags=["Documents"])


def _gen_id() -> str:
    return str(uuid.uuid4())


def _check_kb_access(user: User, kb: KnowledgeBase):
    if user.role.value not in ("admin", "moderator") and kb.owner_id != user.id:
        raise HTTPException(403, "无权操作该知识库")


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(...),
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in parser_service.supported_types():
        raise HTTPException(400, f"Unsupported: .{ext}")

    # Verify KB ownership
    kb_result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = kb_result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "知识库不存在")
    _check_kb_access(current_user, kb)

    doc_id = _gen_id()
    safe_filename = Path(filename).name
    saved_path = settings.upload_dir / f"{doc_id}_{safe_filename}"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    saved_path.write_bytes(content)
    file_size = len(content)

    doc = Document(
        id=doc_id, kb_id=kb_id, filename=filename, file_type=ext,
        file_size=file_size, file_path=str(saved_path), status=DocStatus.PARSING,
    )
    db.add(doc)
    await db.commit()

    error_step = ""
    chunk_objs = []

    try:
        error_step = "parse"
        parsed = parser_service.parse(saved_path, ext)

        error_step = "chunk"
        doc.status = DocStatus.CHUNKING
        await db.commit()
        raw_chunks = chunker_service.chunk(parsed)

        error_step = "save_chunks"
        chunk_dicts_for_vector = []
        for i, rc in enumerate(raw_chunks):
            cid = _gen_id()
            chunk_obj = Chunk(
                id=cid, doc_id=doc_id, chunk_index=i,
                content=rc["content"], token_count=rc.get("token_count", 0),
                heading=rc.get("heading"), page=rc.get("page"),
            )
            chunk_objs.append(chunk_obj)
            chunk_dicts_for_vector.append({
                "id": cid, "content": rc["content"],
                "token_count": rc.get("token_count", 0),
                "heading": rc.get("heading", ""), "page": rc.get("page"),
                "chunk_index": i, "doc_id": doc_id,
                "filename": filename,
            })

        for co in chunk_objs:
            db.add(co)
        await db.commit()

        error_step = "embedding"
        doc.status = DocStatus.EMBEDDING
        await db.commit()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, vector_store.add_chunks, kb_id, chunk_dicts_for_vector)

        error_step = "bm25"
        try:
            bm25_index.build(kb_id, [
                {"id": c.id, "content": c.content, "doc_id": c.doc_id,
                 "heading": c.heading or "", "page": c.page}
                for c in chunk_objs
            ])
        except Exception:
            pass

        doc.status = DocStatus.COMPLETED
        doc.chunk_count = len(chunk_objs)
        await db.commit()
        await db.refresh(doc)

    except Exception as e:
        doc.status = DocStatus.FAILED
        doc.error_message = f"[{error_step}] {e}"[:500]
        doc.chunk_count = len(chunk_objs)
        await db.commit()
        await db.refresh(doc)

    return doc


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    kb_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    kb_result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = kb_result.scalar_one_or_none()
    if not kb:
        raise HTTPException(404, "知识库不存在")
    _check_kb_access(current_user, kb)

    result = await db.execute(
        select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("/{doc_id}/chunks", response_model=list[ChunkResponse])
async def get_document_chunks(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.chunk_index)
    )
    return result.scalars().all()


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    # Check KB ownership
    kb_result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == doc.kb_id))
    kb = kb_result.scalar_one_or_none()
    if kb:
        _check_kb_access(current_user, kb)

    vector_store.delete_by_doc(doc.kb_id, doc_id)
    try:
        Path(doc.file_path).unlink(missing_ok=True)
    except Exception:
        pass
    await db.delete(doc)
    await db.commit()
    return {"status": "deleted"}

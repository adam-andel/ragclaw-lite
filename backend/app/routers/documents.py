"""Document upload & management API routes — many-to-many refactor."""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session, serialize_writes
from app.config import settings
from app.models.document import Document, Chunk, DocStatus, KBDocument
from app.models.kb_access import KBUserAccess
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.document import (
    DocumentResponse, DocumentStatusResponse, ChunkResponse,
    DocumentListResponse, ChunkListResponse,
)
from app.services.auth import get_current_user, get_current_staff
from app.services.parser import parser_service

router = APIRouter(prefix="/api/documents", tags=["Documents"])


@router.get("/supported-types")
async def get_supported_types():
    """Return the list of file extensions this server can currently parse.

    Driven by the parser registry, so it auto-adapts when new parsers are
    added or when an admin disables a plugin. Frontend uses this to populate
    the upload <input accept> attribute dynamically.
    """
    return {"extensions": parser_service.supported_types()}


def _gen_id() -> str:
    return str(uuid.uuid4())


def _status_str(doc: Document) -> str:
    return doc.status.value if hasattr(doc.status, "value") else doc.status


def _model_to_response(doc: Document, kb_ids: list[str] | None = None) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id, kb_id=doc.kb_id, filename=doc.filename,
        file_type=doc.file_type, file_size=doc.file_size,
        status=_status_str(doc), error_message=doc.error_message,
        chunk_count=doc.chunk_count or 0, progress=doc.progress or 0,
        owner_id=doc.owner_id, kb_ids=kb_ids or [],
        created_at=doc.created_at or datetime.utcnow(),
        updated_at=doc.updated_at or datetime.utcnow(),
    )


async def _get_doc_kb_ids(doc_id: str, db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(KBDocument.kb_id).where(KBDocument.doc_id == doc_id)
    )
    return [row[0] for row in result.all()]


async def _load_doc_for_read(
    doc_id: str, current_user: User, db: AsyncSession
) -> Document:
    """Load a document and authorize the caller to READ it (gid / KB-group model).

    Readable if: admin (superuser) OR owner OR the user is a member of ANY KB
    that contains this document (KBUserAccess ∩ KBDocument != empty).
    Otherwise 403. Raises 404 if not found. Returns the Document (no re-query).
    """
    doc = (await db.execute(
        select(Document).where(Document.id == doc_id)
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")

    if current_user.role.value == "admin":
        return doc
    if doc.owner_id is not None and doc.owner_id == current_user.id:
        return doc

    # gid intersection: user's KB groups JOIN doc's KB groups
    shared = (await db.execute(
        select(KBUserAccess.id)
        .join(KBDocument, KBDocument.kb_id == KBUserAccess.kb_id)
        .where(
            KBDocument.doc_id == doc_id,
            KBUserAccess.user_id == current_user.id,
        )
        .limit(1)
    )).first()
    if shared is not None:
        return doc

    raise HTTPException(403, "DOCUMENT_ACCESS_DENIED")


# ---- Upload (single file, no KB binding) ----

@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    _: None = Depends(serialize_writes),
    db: AsyncSession = Depends(get_db),
):
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in parser_service.supported_types():
        raise HTTPException(400, f"UNSUPPORTED_FILE_TYPE: .{ext}")

    doc_id = _gen_id()
    saved_path = settings.upload_dir / f"{doc_id}_{Path(filename).name}"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    saved_path.write_bytes(content)

    doc = Document(
        id=doc_id, filename=filename, file_type=ext,
        file_size=len(content), file_path=str(saved_path),
        status=DocStatus.PENDING, progress=0,
        owner_id=current_user.id, tenant_id=current_user.tenant_id,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    if kb_id:
        kb_result = await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        kb = kb_result.scalar_one_or_none()
        if not kb:
            raise HTTPException(404, "KNOWLEDGE_BASE_NOT_FOUND")
        if current_user.role.value != "admin" and kb.owner_id != current_user.id:
            from app.routers.knowledge_bases import _user_has_kb_access
            if not await _user_has_kb_access(current_user.id, kb_id, db):
                raise HTTPException(403, "ACCESS_DENIED")
        db.add(KBDocument(kb_id=kb_id, doc_id=doc.id))

    await db.commit()

    # Auto-trigger async processing
    from app.services.doc_processor import process_document
    asyncio.create_task(process_document(doc.id))

    return _model_to_response(doc)
# ---- Upload (batch) ----

@router.post("/upload/batch", response_model=list[DocumentResponse])
async def upload_documents_batch(
    files: list[UploadFile] = File(...),
    kb_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    _: None = Depends(serialize_writes),
    db: AsyncSession = Depends(get_db),
):
    if not files:
        raise HTTPException(400, "AT_LEAST_ONE_FILE_REQUIRED")
    if len(files) > 20:
        raise HTTPException(400, "MAX_20_FILES_PER_UPLOAD")

    results = []
    for file in files:
        filename = file.filename or "unknown"
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext not in parser_service.supported_types():
            results.append(DocumentResponse(
                id="", filename=filename, file_type=ext, file_size=0,
                status="skipped", progress=0, error_message=f"UNSUPPORTED_TYPE: .{ext}",
                chunk_count=0, kb_ids=[], kb_id=None,
                created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                owner_id=None,
            ))
            continue

        doc_id = _gen_id()
        saved_path = settings.upload_dir / f"{doc_id}_{Path(filename).name}"
        content = await file.read()
        saved_path.write_bytes(content)

        doc = Document(
            id=doc_id, filename=filename, file_type=ext,
            file_size=len(content), file_path=str(saved_path),
            status=DocStatus.PENDING, progress=0,
            owner_id=current_user.id, tenant_id=current_user.tenant_id,
        )
        db.add(doc)
        await db.flush()
        await db.refresh(doc)

        if kb_id:
            kb_result = await db.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )
            kb = kb_result.scalar_one_or_none()
            if not kb:
                raise HTTPException(404, "KNOWLEDGE_BASE_NOT_FOUND")
            if current_user.role.value != "admin" and kb.owner_id != current_user.id:
                from app.routers.knowledge_bases import _user_has_kb_access
                if not await _user_has_kb_access(current_user.id, kb_id, db):
                    raise HTTPException(403, "ACCESS_DENIED")
            db.add(KBDocument(kb_id=kb_id, doc_id=doc.id))
            await db.flush()

        results.append(_model_to_response(doc))

    await db.commit()

    # Auto-trigger async processing for each uploaded document
    from app.services.doc_processor import process_document
    for r in results:
        if r.id:  # skip skipped
            asyncio.create_task(process_document(r.id))

    return results

# ---- List all documents (paginated, filterable) ----

@router.get("", response_model=DocumentListResponse)
async def list_all_documents(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    file_type: str | None = Query(None),
    search: str | None = Query(None),
    kb_id: str | None = Query(None),
    unlinked: bool | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if current_user.role.value != "admin":
        conditions.append(Document.owner_id == current_user.id)
    if status:
        conditions.append(Document.status == status)
    if file_type:
        conditions.append(Document.file_type == file_type)
    if search:
        conditions.append(Document.filename.ilike(f"%{search}%"))
    if unlinked:
        conditions.append(~Document.kb_links.any())

    if kb_id:
        count_q = (
            select(func.count())
            .select_from(Document)
            .join(KBDocument, Document.id == KBDocument.doc_id)
            .where(KBDocument.kb_id == kb_id)
        )
        items_q = (
            select(Document)
            .join(KBDocument, Document.id == KBDocument.doc_id)
            .where(KBDocument.kb_id == kb_id)
            .order_by(Document.created_at.desc())
        )
    else:
        count_q = select(func.count()).select_from(Document)
        items_q = select(Document).order_by(Document.created_at.desc())

    if conditions:
        count_q = count_q.where(*conditions)
        items_q = items_q.where(*conditions)

    total = (await db.execute(count_q)).scalar() or 0
    items_q = items_q.offset((page - 1) * size).limit(size)
    docs = (await db.execute(items_q)).scalars().all()

    items = []
    for doc in docs:
        doc_kb_ids = await _get_doc_kb_ids(doc.id, db)
        items.append(_model_to_response(doc, doc_kb_ids))
    return DocumentListResponse(items=items, total=total, page=page, size=size)


# ---- Re-index all documents against the current embedding model ----

@router.post("/reindex")
async def reindex_all_documents(
    current_user: User = Depends(get_current_staff),
    _: None = Depends(serialize_writes),
):
    """Re-embed every completed document with the active model and rebuild KB indexes.

    Used after an embedding-model switch (different dimension) or to repair stale
    vectors. Requires the target model to be installed.
    """
    from app.services.model_manager import model_manager
    from app.services.reindex_service import reindex_service
    if not model_manager.is_installed():
        raise HTTPException(400, "Embedding model not installed - cannot re-embed")
    if reindex_service.is_running():
        return {"started": False, "reason": "already_running"}
    return reindex_service.start()


@router.get("/reindex/status")
async def reindex_status(
    current_user: User = Depends(get_current_staff),
):
    """Return progress of the background re-index job."""
    from app.services.reindex_service import reindex_service
    return reindex_service.get_state()


# ---- Document download ----

@router.get("/{doc_id}/download")
async def download_document(
    doc_id: str, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await _load_doc_for_read(doc_id, current_user, db)
    path = Path(doc.file_path)
    if not path.exists():
        raise HTTPException(404, "FILE_NOT_FOUND_OR_PURGED")
    return FileResponse(
        path=path,
        filename=doc.filename,
        media_type="application/octet-stream",
    )


# ---- Document detail ----

@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: str, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await _load_doc_for_read(doc_id, current_user, db)
    kb_ids = await _get_doc_kb_ids(doc_id, db)
    return _model_to_response(doc, kb_ids)


# ---- Document status / progress ----

@router.get("/{doc_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    doc_id: str, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await _load_doc_for_read(doc_id, current_user, db)
    return DocumentStatusResponse(
        id=doc.id, status=_status_str(doc),
        error_message=doc.error_message,
        chunk_count=doc.chunk_count, progress=doc.progress,
    )


# ---- Document chunks ----

@router.get("/{doc_id}/chunks", response_model=ChunkListResponse)
async def get_document_chunks(
    doc_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Case-insensitive substring filter on chunk content"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _load_doc_for_read(doc_id, current_user, db)
    conditions = [Chunk.doc_id == doc_id]
    if search and search.strip():
        conditions.append(Chunk.content.ilike(f"%{search.strip()}%"))

    total = (await db.execute(select(func.count()).select_from(Chunk).where(*conditions))).scalar() or 0
    result = await db.execute(
        select(Chunk)
        .where(*conditions)
        .order_by(Chunk.chunk_index)
        .offset((page - 1) * size)
        .limit(size)
    )
    items = result.scalars().all()
    return ChunkListResponse(items=items, total=total, page=page, size=size)


@router.get("/{doc_id}/chunks/{chunk_index}", response_model=ChunkResponse)
async def get_document_chunk(
    doc_id: str, chunk_index: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _load_doc_for_read(doc_id, current_user, db)
    result = await db.execute(
        select(Chunk).where(Chunk.doc_id == doc_id, Chunk.chunk_index == chunk_index)
    )
    chunk = result.scalar_one_or_none()
    if not chunk:
        raise HTTPException(404, "CHUNK_NOT_FOUND")
    return chunk


# ---- Document KBs ----

@router.get("/{doc_id}/kbs", response_model=list[str])
async def get_document_kbs(
    doc_id: str,     current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _load_doc_for_read(doc_id, current_user, db)
    return await _get_doc_kb_ids(doc_id, db)

# ---- Delete documents (cleans up vectors across all linked KBs) ----
#
# Deletion is fire-and-forget: the API authorizes synchronously and then
# returns immediately while a background task purges the vectors, BM25 index
# entries and the DB row. This keeps the UI snappy; the client may optimistically
# remove the document from its list as soon as the request succeeds.

async def _purge_document(doc_id: str) -> None:
    """Background cleanup: vectors + BM25 + DB row. Runs in its own session."""
    from app.services.vector_store import vector_store
    from app.services.bm25_index import bm25_index

    try:
        async with async_session() as db:
            doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
            if not doc:
                return
            kb_links = (await db.execute(
                select(KBDocument).where(KBDocument.doc_id == doc_id)
            )).scalars().all()
            for link in kb_links:
                vector_store.delete_by_doc(link.kb_id, doc_id)
                try:
                    bm25_index.remove_doc(link.kb_id, doc_id)
                except Exception:
                    pass
            # Note: uploaded file cleanup is done by a periodic maintenance task
            await db.delete(doc)
            await db.commit()
    except Exception as exc:  # never crash the event loop on purge failure
        print(f"[documents] purge failed for {doc_id}: {exc}")


# NOTE: register "/batch" BEFORE "/{doc_id}" so FastAPI matches the static path
# instead of treating "batch" as a doc_id path parameter.
@router.delete("/batch")
async def delete_documents_batch(
    payload: dict, current_user: User = Depends(get_current_user),
    _: None = Depends(serialize_writes),
    db: AsyncSession = Depends(get_db),
):
    doc_ids = payload.get("doc_ids") or []
    if not isinstance(doc_ids, list) or not doc_ids:
        raise HTTPException(400, "EMPTY_DOC_IDS")

    result = await db.execute(select(Document).where(Document.id.in_(doc_ids)))
    owned = result.scalars().all()
    if not owned:
        raise HTTPException(404, "NO_DOCUMENTS_FOUND")

    for doc in owned:
        if current_user.role.value not in ("admin", "moderator") and doc.owner_id != current_user.id:
            raise HTTPException(403, "FORBIDDEN_DOC_OWNER")
        asyncio.create_task(_purge_document(doc.id))

    return {"status": "deleting", "count": len(owned)}


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str, current_user: User = Depends(get_current_user),
    _: None = Depends(serialize_writes),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    if current_user.role.value not in ("admin", "moderator") and doc.owner_id != current_user.id:
        raise HTTPException(403, "DOCUMENT_DELETE_DENIED")

    asyncio.create_task(_purge_document(doc_id))
    return {"status": "deleting"}


# ---- Legacy: list documents by KB (backward compat) ----

@router.get("/by-kb/{kb_id}", response_model=list[DocumentResponse])
async def list_documents_by_kb(
    kb_id: str, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # gid: only admin / KB members may list a KB's documents
    if current_user.role.value != "admin":
        member = (await db.execute(
            select(KBUserAccess.id).where(
                KBUserAccess.kb_id == kb_id,
                KBUserAccess.user_id == current_user.id,
            ).limit(1)
        )).first()
        if member is None:
            raise HTTPException(403, "KB_ACCESS_DENIED")
    result = await db.execute(
        select(KBDocument).where(KBDocument.kb_id == kb_id).order_by(KBDocument.added_at.desc())
    )
    links = result.scalars().all()
    items = []
    for link in links:
        doc_result = await db.execute(select(Document).where(Document.id == link.doc_id))
        doc = doc_result.scalar_one_or_none()
        if doc:
            items.append(_model_to_response(doc, [kb_id]))
    return items
# ---- Trigger document processing ----

@router.post("/{doc_id}/process")
async def trigger_process_document(
    doc_id: str, current_user: User = Depends(get_current_user),
    _: None = Depends(serialize_writes),
    db: AsyncSession = Depends(get_db),
):
    """Trigger async processing for a single pending document."""
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "DOCUMENT_NOT_FOUND")
    if current_user.role.value != "admin" and doc.owner_id != current_user.id:
        raise HTTPException(403, "ONLY_OWN_UPLOADED_DOC")
    if doc.status not in (DocStatus.PENDING, DocStatus.UPLOADED, DocStatus.FAILED):
        raise HTTPException(400, f"DOC_STATUS_{_status_str(doc).upper()}_NO_ACTION_NEEDED")

    from app.services.doc_processor import process_document
    import asyncio
    asyncio.create_task(process_document(doc_id))
    return {"status": "processing", "doc_id": doc_id}


@router.post("/process-all")
async def trigger_process_all(
    current_user: User = Depends(get_current_user),
    _: None = Depends(serialize_writes),
):
    """Process all pending documents."""
    from app.services.doc_processor import process_pending_documents
    import asyncio
    asyncio.create_task(process_pending_documents())
    return {"status": "started"}



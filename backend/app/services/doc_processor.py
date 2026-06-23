"""Async document processing pipeline with progress tracking.

Runs parse → chunk → embed in a background thread, updating
Document.status and Document.progress as each stage completes.
Embeddings are cached in Chunk.embedding for reuse when adding
the document to multiple knowledge bases.
"""

import asyncio
import struct
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.database import async_session
from app.models.document import Document, Chunk, DocStatus
from app.services.parser import parser_service
from app.services.chunker import chunker_service
from app.services.embedder import embedder_service


def _gen_id() -> str:
    return str(uuid.uuid4())


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Pack list of floats into bytes for SQLite BLOB storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


async def process_document(doc_id: str):
    """Process a single document through the full pipeline (async-safe)."""

    async with async_session() as db:
        doc = await db.get(Document, doc_id)
        if not doc:
            return
        if doc.status in (DocStatus.COMPLETED,):
            return  # already done

        filename = doc.filename
        file_path = Path(doc.file_path)
        ext = doc.file_type
        chunk_objs = []

        try:
            # Step 1: Parse (10 -> 30)
            doc.status = DocStatus.PARSING
            doc.progress = 10
            await db.commit()
            loop = asyncio.get_running_loop()
            parsed = await loop.run_in_executor(None, parser_service.parse, file_path, ext)

            # Step 2: Chunk (30 -> 50)
            doc.status = DocStatus.CHUNKING
            doc.progress = 30
            await db.commit()
            raw_chunks = await loop.run_in_executor(None, chunker_service.chunk, parsed)

            # Step 3: Save chunks + compute embeddings (50 -> 90)
            doc.status = DocStatus.EMBEDDING
            doc.progress = 50
            await db.commit()

            texts = [rc["content"] for rc in raw_chunks]
            embeddings = embedder_service.embed(texts)

            for i, rc in enumerate(raw_chunks):
                cid = _gen_id()
                emb_bytes = _serialize_embedding(embeddings[i]) if i < len(embeddings) else None
                chunk_obj = Chunk(
                    id=cid, doc_id=doc_id, chunk_index=i,
                    content=rc["content"], token_count=rc.get("token_count", 0),
                    heading=rc.get("heading"), page=rc.get("page"),
                    embedding=emb_bytes,
                )
                chunk_objs.append(chunk_obj)
                db.add(chunk_obj)

            doc.progress = 90
            await db.commit()

            # Step 4: Complete (100)
            doc.status = DocStatus.COMPLETED
            doc.progress = 100
            doc.chunk_count = len(chunk_objs)
            await db.commit()

        except Exception as e:
            doc.status = DocStatus.FAILED
            doc.error_message = str(e)[:500]
            doc.chunk_count = len(chunk_objs)
            doc.progress = 0
            await db.commit()


async def process_pending_documents():
    """Process all documents with status=pending (called on startup or via trigger)."""
    async with async_session() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(Document).where(
                Document.status.in_([DocStatus.PENDING, DocStatus.UPLOADED])
            ).order_by(Document.created_at.asc())
        )
        pending = result.scalars().all()

    for doc in pending:
        try:
            await process_document(doc.id)
        except Exception:
            pass  # Individual failures are already recorded on the document

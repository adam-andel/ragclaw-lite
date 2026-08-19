# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
﻿"""Async document processing pipeline with progress tracking.

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

            # Step 3: Persist chunks FIRST (embedding-agnostic). This guarantees
            # chunks exist for BM25/keyword retrieval even when no embedding
            # model is installed.
            doc.status = DocStatus.CHUNKING
            doc.progress = 50
            await db.commit()
            for i, rc in enumerate(raw_chunks):
                cid = _gen_id()
                chunk_obj = Chunk(
                    id=cid, doc_id=doc_id, chunk_index=i,
                    content=rc["content"], token_count=rc.get("token_count", 0),
                    heading=rc.get("heading"), page=rc.get("page"),
                    embedding=None,
                )
                chunk_objs.append(chunk_obj)
                db.add(chunk_obj)
            doc.status = DocStatus.CHUNKED
            doc.chunk_count = len(chunk_objs)
            doc.progress = 60
            await db.commit()

            # Step 4: Embedding (optional — degrades to CHUNKED if no model)
            doc.status = DocStatus.EMBEDDING
            doc.progress = 70
            await db.commit()
            texts = [rc["content"] for rc in raw_chunks]
            try:
                embeddings = embedder_service.embed(texts)
            except RuntimeError as e:
                if str(e).startswith("EMBED_MODEL_NOT_INSTALLED"):
                    # Non-fatal: keep CHUNKED, record an informational hint so
                    # the UI can tell the user keyword retrieval is available.
                    doc.status = DocStatus.CHUNKED
                    doc.progress = 60
                    # Store a stable error CODE (not a baked string) so the
                    # frontend can localize it by language. Raw exception detail
                    # is not needed here - the UI points the user to install a
                    # model and re-index.
                    doc.error_message = "EMBED_MODEL_NOT_INSTALLED"
                    await db.commit()
                    return
                raise

            for chunk_obj, emb in zip(chunk_objs, embeddings):
                chunk_obj.embedding = _serialize_embedding(emb)

            # Step 5: Complete (100)
            doc.status = DocStatus.COMPLETED
            doc.progress = 100
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

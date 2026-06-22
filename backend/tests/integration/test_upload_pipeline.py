"""Integration test: end-to-end upload pipeline (parse → chunk → ChromaDB + BM25)."""

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import settings
from app.database import async_session
from app.models.document import Document, Chunk, DocStatus
from app.models.knowledge_base import KnowledgeBase
from app.services.parser import parser_service
from app.services.chunker import chunker_service
from app.services.bm25_index import bm25_index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kid() -> str:
    return str(uuid.uuid4())


def _model_available() -> bool:
    """Check if BGE embedder model can be loaded."""
    try:
        from app.services.embedder import embedder_service
        embedder_service.embed(["quick check"])
        return True
    except Exception:
        return False


_MODEL_OK = None


def _skip_if_no_model():
    global _MODEL_OK
    if _MODEL_OK is None:
        _MODEL_OK = _model_available()
    if not _MODEL_OK:
        pytest.skip("BGE embedding model not available")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def kb_id(test_db):
    """Create a KB and return its id."""
    kid = _kid()
    async with async_session() as db:
        kb = KnowledgeBase(id=kid, name="Integration Test KB", description="pipeline test")
        db.add(kb)
        await db.commit()
    return kid


async def _run_pipeline(kb_id: str, filename: str, content: str) -> Document:
    """Simulate the upload pipeline: write file → parse → chunk → embed → BM25."""
    from app.services.vector_store import vector_store

    file_path = settings.upload_dir / filename
    file_path.write_text(content, encoding="utf-8")

    doc_id = str(uuid.uuid4())
    ext = filename.rsplit(".", 1)[-1]

    # Parse
    parsed = parser_service.parse(file_path, ext)

    # Chunk
    raw_chunks = chunker_service.chunk(parsed)

    # Save Document + Chunks to SQLite
    async with async_session() as db:
        doc = Document(
            id=doc_id, kb_id=kb_id, filename=filename,
            file_type=ext, file_size=len(content),
            file_path=str(file_path), status=DocStatus.PARSING,
        )
        db.add(doc)
        await db.commit()

        chunk_objs = []
        chunk_dicts = []
        for i, rc in enumerate(raw_chunks):
            cid = str(uuid.uuid4())
            chunk_objs.append(Chunk(id=cid, doc_id=doc_id, chunk_index=i,
                                    content=rc["content"],
                                    token_count=rc.get("token_count", 0),
                                    heading=rc.get("heading"),
                                    page=rc.get("page")))
            chunk_dicts.append({
                "id": cid, "content": rc["content"],
                "token_count": rc.get("token_count", 0),
                "heading": rc.get("heading", ""), "page": rc.get("page"),
                "chunk_index": i, "doc_id": doc_id, "filename": filename,
            })

        for co in chunk_objs:
            db.add(co)
        await db.commit()

        # Embed + ChromaDB
        from app.services.vector_store import vector_store
        import asyncio
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, vector_store.add_chunks, kb_id, chunk_dicts)

        # BM25
        bm25_index.build(kb_id, [
            {"id": c.id, "content": c.content, "doc_id": c.doc_id,
             "heading": c.heading or "", "page": c.page}
            for c in chunk_objs
        ])

        doc.status = DocStatus.COMPLETED
        doc.chunk_count = len(chunk_objs)
        await db.commit()
        await db.refresh(doc)

    return doc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUploadPipeline:
    """End-to-end upload pipeline with data consistency checks."""

    @pytest.mark.asyncio
    async def test_pipeline_document_status_completed(self, kb_id):
        _skip_if_no_model()
        content = "# ERAG\n\n## Overview\n\nERAG is an enterprise RAG platform.\n\n## Features\n\nHybrid search, BM25, RRF fusion.\n"
        doc = await _run_pipeline(kb_id, "test_pipeline.md", content)
        assert doc.status == DocStatus.COMPLETED
        assert doc.chunk_count > 0

    @pytest.mark.asyncio
    async def test_sqlite_has_document_record(self, kb_id):
        _skip_if_no_model()
        content = "# Test Doc\n\nSome content here.\n"
        doc = await _run_pipeline(kb_id, "sqlite_test.md", content)

        async with async_session() as db:
            from sqlalchemy import select
            result = await db.execute(select(Document).where(Document.id == doc.id))
            found = result.scalar_one_or_none()
            assert found is not None
            assert found.filename == "sqlite_test.md"
            assert found.status == DocStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_chromadb_collection_has_data(self, kb_id):
        _skip_if_no_model()
        from app.services.vector_store import vector_store

        content = "# ChromaDB Test\n\nContent for vector store.\n" * 10
        await _run_pipeline(kb_id, "chroma_test.md", content)

        count = vector_store.count(kb_id)
        assert count > 0

    @pytest.mark.asyncio
    async def test_bm25_index_built(self, kb_id):
        _skip_if_no_model()
        content = "# BM25 Test\n\nERAG platform with keyword search capability.\n" * 10
        await _run_pipeline(kb_id, "bm25_test.md", content)

        assert bm25_index.has_index(kb_id) is True

    @pytest.mark.asyncio
    async def test_delete_document_cascades(self, kb_id):
        _skip_if_no_model()
        from app.services.vector_store import vector_store
        from sqlalchemy import select

        content = "# To Delete\n\nThis document will be deleted.\n" * 10
        doc = await _run_pipeline(kb_id, "delete_me.md", content)

        # Delete via SQLAlchemy (cascade should remove chunks)
        async with async_session() as db:
            d = await db.get(Document, doc.id)
            await db.delete(d)
            await db.commit()

        # Verify SQLite chunks gone
        async with async_session() as db:
            result = await db.execute(select(Chunk).where(Chunk.doc_id == doc.id))
            remaining = result.scalars().all()
            assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_delete_doc_clears_vector_store(self, kb_id):
        _skip_if_no_model()
        from app.services.vector_store import vector_store

        content = "# Vector Cleanup\n\nContent to be cleaned from vectors.\n" * 10
        doc = await _run_pipeline(kb_id, "vec_cleanup.md", content)

        before = vector_store.count(kb_id)
        vector_store.delete_by_doc(kb_id, doc.id)
        after = vector_store.count(kb_id)
        assert after < before

    @pytest.mark.asyncio
    async def test_delete_kb_clears_collection(self, kb_id):
        _skip_if_no_model()
        from app.services.vector_store import vector_store
        from sqlalchemy import select

        content = "# KB Cleanup\n\nKB level cleanup test.\n" * 10
        await _run_pipeline(kb_id, "kb_cleanup.md", content)

        # Verify data exists
        assert vector_store.count(kb_id) > 0

        # Delete KB
        async with async_session() as db:
            kb = await db.get(KnowledgeBase, kb_id)
            await db.delete(kb)
            await db.commit()

        vector_store.delete_collection(kb_id)
        assert vector_store.count(kb_id) == 0

    @pytest.mark.asyncio
    async def test_upload_unsupported_format_fails(self, kb_id):
        """Uploading an unsupported format should raise ValueError."""
        with pytest.raises(Exception):
            await _run_pipeline(kb_id, "bad.exe", "not valid content")

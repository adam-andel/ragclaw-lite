"""EnterpriseRAG-Lite FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    # Rebuild BM25 indexes from DB
    try:
        from app.models.document import Chunk, Document, DocStatus
        from app.services.bm25_index import bm25_index
        from app.database import async_session
        from sqlalchemy import select
        async with async_session() as db:
            docs_result = await db.execute(select(Document).where(Document.status == DocStatus.COMPLETED))
            kb_ids = set()
            for doc in docs_result.scalars().all():
                kb_ids.add(doc.kb_id)
            for kb_id in kb_ids:
                chunks_result = await db.execute(
                    select(Chunk).where(
                        Chunk.doc_id.in_(select(Document.id).where(Document.kb_id == kb_id))
                    )
                )
                chunks = chunks_result.scalars().all()
                if chunks:
                    bm25_index.build(kb_id, [
                        {"id": c.id, "content": c.content, "doc_id": c.doc_id,
                         "heading": c.heading or "", "page": c.page}
                        for c in chunks
                    ])
            print(f"BM25 rebuilt for {len(kb_ids)} knowledge bases")
    except Exception as e:
        print(f"BM25 rebuild warning: {e}")
    yield
    # Shutdown


app = FastAPI(
    title="EnterpriseRAG-Lite",
    version="0.2.0",
    description="企业级 RAG 知识中台 · 精简版（多租户）",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API Routers ---
from app.routers import auth, users, documents, knowledge_bases, retrieval, chat, stats
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(knowledge_bases.router)
app.include_router(retrieval.router)
app.include_router(chat.router)
app.include_router(stats.router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "EnterpriseRAG-Lite", "version": "0.2.0"}


# --- Static Frontend (Vue3 dist) ---
frontend_dist = settings.project_root / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

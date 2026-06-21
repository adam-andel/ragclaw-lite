"""EnterpriseRAG-Lite FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, async_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    # Seed default admin user on first launch (empty users table)
    try:
        from app.models.user import User
        from app.services.auth import hash_password
        from sqlalchemy import func, select
        async with async_session() as db:
            count = await db.scalar(select(func.count()).select_from(User))
            if count == 0:
                default_user = User(
                    username="admin",
                    hashed_password=hash_password("admin123"),
                    display_name="Administrator",
                    role="admin",
                    is_active=True,
                )
                db.add(default_user)
                await db.commit()
                print(f"[seed] Default admin user created (admin / admin123)")
    except Exception as e:
        print(f"[seed] warning: {e}")
    # Pre-warm BGE model to avoid cold-start on first request
    try:
        import asyncio
        from app.services.embedder import embedder_service
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, embedder_service.embed, ["warmup"])
        print("BGE model pre-warmed")
    except Exception as e:
        print(f"BGE warmup warning: {e}")
    # Ensure all models are loaded for create_all
    from app.models import kb_access  # noqa: F401
    # Rebuild BM25 indexes from DB
    try:
        from app.models.document import Chunk, Document, DocStatus
        from app.services.bm25_index import bm25_index
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
from app.routers import auth, users, documents, knowledge_bases, retrieval, chat, stats, memory
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(knowledge_bases.router)
app.include_router(retrieval.router)
app.include_router(chat.router)
app.include_router(stats.router)
app.include_router(memory.router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "EnterpriseRAG-Lite", "version": "0.2.0"}


# --- Static Frontend (Vue3 dist) ---
frontend_dist = settings.project_root / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

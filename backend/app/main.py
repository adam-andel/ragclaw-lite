"""EnterpriseRAG-Lite FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, async_session
from app.models.system_setting import SystemSetting  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    # Init runtime config manager (API keys from encrypted file, other settings from DB)
    from app.services.config_manager import config_manager
    await config_manager.init()
    # Init LLM concurrency limiter from saved config
    from app.services.llm_semaphore import llm_limiter
    await llm_limiter.update_max(config_manager.concurrency)
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
    from app.models import skill as _skill_models  # noqa: F401
    from app.models import cron_job as _cron_job_models  # noqa: F401
    from app.models import notification as _notification_models  # noqa: F401
    # Rebuild BM25 indexes from DB (using new kb_documents junction table)
    try:
        from app.models.document import Chunk, Document, DocStatus, KBDocument
        from app.services.bm25_index import bm25_index
        from sqlalchemy import select, and_
        async with async_session() as db:
            # Collect all kb_ids from the junction table
            kb_result = await db.execute(select(KBDocument.kb_id).distinct())
            kb_ids = {row[0] for row in kb_result.fetchall()}
            for kb_id in kb_ids:
                chunks_result = await db.execute(
                    select(Chunk).join(Document, Chunk.doc_id == Document.id).join(
                        KBDocument, and_(KBDocument.doc_id == Document.id, KBDocument.kb_id == kb_id)
                    ).where(Document.status == DocStatus.COMPLETED)
                )
                chunks = chunks_result.scalars().all()
                if chunks:
                    doc_ids = {c.doc_id for c in chunks}
                    doc_result = await db.execute(
                        select(Document.id, Document.filename).where(Document.id.in_(doc_ids))
                    )
                    doc_map = {row[0]: row[1] for row in doc_result.fetchall()}
                    bm25_index.build(kb_id, [
                        {"id": c.id, "content": c.content, "doc_id": c.doc_id,
                         "heading": c.heading or "", "chunk_index": c.chunk_index,
                         "page": c.page, "filename": doc_map.get(c.doc_id, "")}
                        for c in chunks
                    ])
            print(f"BM25 rebuilt for {len(kb_ids)} knowledge bases")
    except Exception as e:
        print(f"BM25 rebuild warning: {e}")
    # Process any pending documents on startup
    try:
        from app.services.doc_processor import process_pending_documents
        import asyncio as _asyncio
        _asyncio.create_task(process_pending_documents())
        print("Document processor started for pending documents")
    except Exception as e:
        print(f"Doc processor startup warning: {e}")
    # Initialize MCP tool registry
    try:
        from app.services.tool_registry import tool_registry
        _asyncio.create_task(tool_registry.refresh())
        print("Tool registry refresh scheduled")
    except Exception as e:
        print(f"Tool registry init warning: {e}")
    # Sync skill filesystem to DB index
    try:
        from app.services.skill_manager import sync_skills_to_db
        async with async_session() as db:
            result = await sync_skills_to_db(db)
            print(f"Skill sync: +{result['added']} ~{result['updated']} -{result['deactivated']}")
    except Exception as e:
        print(f"Skill sync warning: {e}")
    # Refresh parser plugin state cache on startup
    try:
        from app.services.parser import parser_service
        await parser_service._refresh_disabled_cache()
        print("Parser plugin state loaded")
    except Exception as e:
        print(f"Parser plugin state init warning: {e}")

    # Start cron scheduler background task
    try:
        from app.services.cron_scheduler import scheduler_loop
        import asyncio as _asyncio
        _asyncio.create_task(scheduler_loop())
        print("Cron scheduler started")
    except Exception as e:
        print(f"Cron scheduler startup warning: {e}")

    yield
    # Shutdown


app = FastAPI(
    title="EnterpriseRAG-Lite",
    version="0.5.0",
    description="企业级 Agentic RAG 知识中台 · 精简版（多租户 · SKILL + MCP）",
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
from app.routers import auth, users, documents, knowledge_bases, retrieval, chat, stats, memory, config, skills, mcp_servers, plugins, cron_jobs, notifications
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(knowledge_bases.router)
app.include_router(retrieval.router)
app.include_router(chat.router)
app.include_router(stats.router)
app.include_router(memory.router)
app.include_router(config.router)
app.include_router(skills.router)
app.include_router(mcp_servers.router)
app.include_router(plugins.router)
app.include_router(cron_jobs.router)
app.include_router(notifications.router)


@app.get("/api/health")
async def health_check():
    from app.services.config_manager import config_manager
    from app.database import engine
    return {
        "status": "ok",
        "service": "EnterpriseRAG-Lite",
        "version": "0.5.0",
        "llm_configured": config_manager.is_configured,
    }


# --- Download proxy: relay MCP-generated files through ERAG ---
@app.get("/api/download/{uuid}/{filename}")
async def download_mcp_file(uuid: str, filename: str):
    """Proxy file download from MCP REPL server.
    
    Fetches the file from the MCP server's internal /files/ endpoint and
    streams it back to the client. This keeps the MCP server isolated — no
    host port mapping needed, and no localhost-dependent URLs.
    """
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    import httpx
    import mimetypes

    # Sanitize inputs to prevent path traversal
    safe_uuid = uuid.replace("/", "").replace("\\", "").replace("..", "")
    safe_filename = filename.replace("..", "").replace("/", "").replace("\\", "")

    mcp_base = settings.mcp_repl_internal_url.rstrip("/")
    mcp_url = f"{mcp_base}/files/{safe_uuid}/{safe_filename}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(mcp_url)
            resp.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(503, detail="MCP REPL server unreachable")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(404, detail="File not found or expired")
        raise HTTPException(502, detail=f"MCP server error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(502, detail=f"Download proxy error: {str(e)}")

    mime_type, _ = mimetypes.guess_type(safe_filename)
    media_type = mime_type or "application/octet-stream"

    return StreamingResponse(
        content=resp.aiter_bytes(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "Content-Length": resp.headers.get("Content-Length", ""),
        },
    )


# --- Workspace static files (user-accessible download) ---
workspace_dir = settings.project_root / "data" / "workspace"
workspace_dir.mkdir(parents=True, exist_ok=True)
app.mount("/data/workspace", StaticFiles(directory=str(workspace_dir)), name="workspace")

# --- Static Frontend (Vue3 dist) ---
# Vue Router uses HTML5 history mode, so non-root paths like /chat must fall
# back to index.html for client-side routing to take over.
frontend_dist = settings.project_root / "frontend" / "dist"
if frontend_dist.exists():
    # Avatar upload directory
    avatar_dir = frontend_dist / "avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/avatar",
        StaticFiles(directory=str(avatar_dir)),
        name="frontend-avatar",
    )

    app.mount(
        "/assets",
        StaticFiles(directory=str(frontend_dist / "assets")),
        name="frontend-assets",
    )

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        from fastapi.responses import FileResponse

        candidate = (frontend_dist / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and frontend_dist in candidate.parents
        ):
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")

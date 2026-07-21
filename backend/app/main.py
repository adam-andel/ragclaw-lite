"""RAGClaw-Lite FastAPI application entry point."""

import asyncio as _asyncio
import faulthandler
import threading
import time
import traceback as _tb
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, async_session
from app.models.system_setting import SystemSetting  # noqa: F401


# ───────────────────────────────────────────────────────────────────────────
# Loop-stall watchdog (diagnostic).
# A frozen event loop (0% CPU, all endpoints including /health hanging) is
# almost always a deadlock or a sync blocking call on the loop thread. This
# thread pings the loop every few seconds; if the loop stops responding we
# dump every thread + asyncio coroutine stack to /tmp/loop_stall.txt so the
# root cause can be read with `docker exec ragclaw-lite cat /tmp/loop_stall.txt`.
# ───────────────────────────────────────────────────────────────────────────
_LOOP_STALL_PATH = "/tmp/loop_stall.txt"


def _dump_asyncio_stacks(f, loop) -> None:
    f.write("\n=== asyncio tasks (loop thread) ===\n")
    try:
        for t in _asyncio.all_tasks(loop):
            f.write(f"\n--- task {t.get_name()} {t} ---\n")
            for frame in t.get_stack(limit=50):
                f.write("".join(_tb.format_stack([frame], limit=1)))
    except Exception as e:  # pragma: no cover - best effort
        f.write(f"  (failed to read asyncio stacks: {e})\n")


def _loop_watchdog(loop) -> None:
    while True:
        time.sleep(8)
        try:
            fut = _asyncio.run_coroutine_threadsafe(_asyncio.sleep(0), loop)
            fut.result(timeout=15)
        except Exception:
            try:
                with open(_LOOP_STALL_PATH, "w") as f:
                    f.write("LOOP STALLED at " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                    faulthandler.dump_traceback(f, all_threads=True)
                    _dump_asyncio_stacks(f, loop)
                print(f"[watchdog] Loop stall dumped -> {_LOOP_STALL_PATH}")
            except Exception:
                pass
            time.sleep(30)  # avoid dumping in a tight loop


def _start_loop_watchdog() -> None:
    try:
        loop = _asyncio.get_running_loop()
        threading.Thread(
            target=_loop_watchdog, args=(loop,), daemon=True, name="loop-watchdog"
        ).start()
        print("[watchdog] loop-stall watchdog started")
    except Exception as e:  # pragma: no cover
        print(f"[watchdog] failed to start: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    _start_loop_watchdog()
    await init_db()
    # Init runtime config manager (API keys from encrypted file, other settings from DB)
    from app.services.config_manager import config_manager
    await config_manager.init()

    # Push the (auto-generated) REPL identity secret to the MCP REPL container so
    # per-user isolation is active out of the box. Retry because containers may
    # start in any order; on failure a self-heal loop retries until success, then
    # stops (no perpetual heartbeat) and is re-triggered by the next save.
    try:
        from app.routers import config as _cfg_router
        pushed = await _cfg_router.ensure_mcp_auth_secret_pushed()
        if not pushed:
            print("[startup] WARNING: could not push REPL_AUTH_SECRET to MCP — REPL "
                  "isolation may be inactive until you save it in Settings or restart mcp-repl")

        # If MCP was not reachable at startup, start a self-healing retry loop that
        # stops once the push succeeds (no perpetual heartbeat); the next Settings
        # save re-triggers it if needed.
        if not pushed:
            await _cfg_router.ensure_auth_secret_retry_running()

        # Push the sandbox network policy on startup so an already-saved policy
        # reaches MCP even if the backend starts first. If MCP is not reachable
        # yet, start a self-healing retry loop that stops once the push succeeds
        # (no perpetual heartbeat); the next Settings save re-triggers it if needed.
        pushed_policy = await _cfg_router.ensure_mcp_policy_pushed()
        if not pushed_policy:
            print("[startup] WARNING: could not push sandbox network policy to MCP — "
                  "outbound policy in mcp-repl may be stale until you save it in Settings")
            await _cfg_router.ensure_network_policy_retry_running()
    except Exception as e:
        print(f"[startup] push MCP config warning: {e}")
    # Init LLM concurrency limiter from saved config
    from app.services.llm_semaphore import llm_limiter
    await llm_limiter.update_max(config_manager.concurrency)
    # Default admin user is now seeded in database._seed_db (fixed REPL UID + idempotent upsert).
    # Pre-warm BGE model to avoid cold-start on first request.
    # Only if the local model is already installed (do NOT auto-download at boot).
    try:
        import asyncio
        from app.services.model_manager import model_manager
        from app.services.config_manager import config_manager
        if model_manager.is_installed(config_manager.embedding_model):
            from app.services.embedder import embedder_service
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, embedder_service.embed, ["warmup"])
            print("BGE model pre-warmed")
        else:
            print("BGE model not installed yet — skipping warmup (install via Settings UI)")
    except Exception as e:
        print(f"BGE warmup warning: {e}")
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
                    ).where(Document.status.in_([DocStatus.COMPLETED, DocStatus.CHUNKED]))
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
    title="RAGClaw-Lite",
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
from app.routers import auth, users, documents, knowledge_bases, retrieval, chat, stats, memory, config, skills, mcp_servers, plugins, cron_jobs, notifications, embedding_model, workspace
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
app.include_router(embedding_model.router)
app.include_router(workspace.router)


@app.get("/api/health")
async def health_check():
    from app.services.config_manager import config_manager
    from app.database import engine
    return {
        "status": "ok",
        "service": "RAGClaw-Lite",
        "version": "0.5.0",
        "llm_configured": config_manager.is_configured,
        "context_window": config_manager.context_window,
    }


# --- Download proxy: relay MCP-generated files through RAGClaw ---
@app.get("/api/download/{file_path:path}")
async def download_mcp_file(file_path: str):
    """Proxy file download from MCP REPL server.

    Fetches the file from the MCP server's internal /files/ endpoint and
    returns it to the client. This keeps the MCP server isolated — no
    host port mapping needed, and no localhost-dependent URLs.

    The ``file_path`` carries the nested allow-dir-relative path, which may
    include a per-user prefix (e.g. ``user_u2001/<ws>/<file>``) when sandbox
    isolation is active. Slashes are preserved; path traversal is blocked.
    """
    from fastapi import HTTPException
    from fastapi.responses import Response
    import httpx
    import mimetypes
    import posixpath

    # Reconstruct the nested allow-dir-relative path and block traversal.
    # Slashes inside file_path are kept so per-user isolation paths survive.
    norm = posixpath.normpath(file_path.replace("\\", "/").strip("/"))
    if not norm or ".." in norm.split("/"):
        raise HTTPException(400, detail="Invalid download path")
    parts = norm.split("/")
    if len(parts) < 2:
        raise HTTPException(400, detail="Invalid download path")
    safe_uuid = "/".join(parts[:-1])
    safe_filename = parts[-1]
    if not safe_filename or "/" in safe_filename or ".." in safe_filename:
        raise HTTPException(400, detail="Invalid filename")

    mcp_base = settings.mcp_repl_internal_url.rstrip("/")
    mcp_url = f"{mcp_base}/files/{safe_uuid}/{safe_filename}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(mcp_url)
            resp.raise_for_status()
            body = resp.content  # read full body before client exits
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

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
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

    class _ImmutableStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):  # type: ignore[override]
            response = await super().get_response(path, scope)
            response.headers.update(
                {"Cache-Control": "public, max-age=31536000, immutable"}
            )
            return response

    app.mount(
        "/assets",
        _ImmutableStaticFiles(directory=str(frontend_dist / "assets")),
        name="frontend-assets",
    )

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        from fastapi.responses import FileResponse

        # The HTML entry must always be revalidated so a fresh build
        # (new hashed assets) is picked up immediately after redeploy.
        no_cache = {"Cache-Control": "no-cache"}
        candidate = (frontend_dist / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and frontend_dist in candidate.parents
        ):
            return FileResponse(candidate, headers=no_cache)
        return FileResponse(frontend_dist / "index.html", headers=no_cache)

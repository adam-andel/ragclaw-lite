"""RAGClaw-Lite FastAPI application entry point."""

import asyncio as _asyncio
import faulthandler
import logging
import threading
import time
import traceback as _tb
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, async_session
from app.models.system_setting import SystemSetting  # noqa: F401
from app.logging_config import setup_logging

# Apply RAGClaw logging config as early as possible (defensive — the lifespan
# re-applies it after uvicorn's own config is installed at startup).
setup_logging()

# Keep strong references to fire-and-forget background tasks (e.g. the BGE
# warmup). asyncio.create_task() only holds a weak reference until the task
# first runs; without a kept reference the task can be garbage-collected and
# silently cancelled before it ever starts.
_BACKGROUND_TASKS: "set[_asyncio.Task]" = set()


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


async def _prewarm_embedding_model() -> None:
    """Best-effort background warmup of the BGE embedding model.

    Spawned as a fire-and-forget ``asyncio.create_task`` from the lifespan (see
    the caller) rather than awaited. Rationale:

    - A slow model load must NEVER block server startup or the first request.
      Running it as a background task lets startup complete immediately; if the
      load stalls, the first request simply lazy-loads the model on the request
      path (which is reliable in practice).
    - Loaded via run_in_executor off the event loop so the loop stays
      responsive. Only model construction (``_ensure_model``) is done, not
      ``encode()``: the encode forward pass is cheap and runs on the request
      path anyway.
    - Idempotent: ``_ensure_model()`` is a no-op if the model is already
      resident, so a worker reload (uvicorn ``--reload``) re-running this task
      is safe.
    - Covers "warm up after reload": each fresh worker re-runs the lifespan and
      re-spawns this task, with no extra machinery.
    - Best-effort only: any exception is logged, never propagated.
    """
    try:
        import asyncio
        from app.services.config_manager import config_manager
        from app.services.model_manager import model_manager
        if not model_manager.is_installed(config_manager.embedding_model):
            print("BGE model not installed yet — skipping warmup (install via Settings UI)", flush=True)
            return
        # Brief pause so the load runs after the freshly-forked worker has
        # settled (avoids the early-startup window where the model load can
        # stall). Warmup is concurrent with the MCP config push below, so the
        # push cannot delay it either.
        await asyncio.sleep(2)
        from app.services.embedder import embedder_service
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, embedder_service._ensure_model)
        print("BGE model pre-warmed", flush=True)
    except Exception as e:  # best-effort — never let warmup break startup
        print(f"BGE warmup warning: {e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Re-apply RAGClaw logging now that uvicorn has installed its own config.
    # Fixes ragclaw.* INFO logs being silently dropped (P0 observability).
    setup_logging()
    import logging as _logging
    _logging.getLogger("ragclaw").info("RAGClaw logging initialized")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    _start_loop_watchdog()
    await init_db()
    # Init runtime config manager (API keys from encrypted file, other settings from DB)
    from app.services.config_manager import config_manager
    await config_manager.init()
    # Config-time sanity check: warn (non-blocking) if the context window is too
    # small to hold the fixed overhead plus a usable content room. Runtime
    # Oversized queries are rejected at the API entry point and residual overflow
    # surfaces as an upstream 400, so this only makes the risk visible.
    _cfg_logger = _logging.getLogger("ragclaw")
    for _w in config_manager.validate_compression_budget():
        _p = _w.get("params", {})
        _cfg_logger.warning(
            "[config] context-window sanity: %s (window=%s, max_tokens=%s, "
            "fixed_overhead=%s, content_room=%s) — long conversations may be "
            "auto-truncated; increase the context window or lower max_tokens.",
            _w.get("code"), _p.get("cw"), _p.get("mt"), _p.get("ov"), _p.get("left"),
        )
    # Materialize TLS material (cert/key + nginx conf) into the shared volume so
    # the nginx reverse proxy can serve HTTPS. No-op if the TLS volume is not
    # mounted (e.g. dev stack).
    config_manager.ensure_tls_config()

    # Best-effort, NON-BLOCKING warmup of the BGE embedding model. Spawned as a
    # background task (not awaited) so a slow or (rare) hung model load can
    # never delay or block startup, and so it cannot be delayed by the MCP
    # config push below (which self-heals/retries for minutes if MCP is down).
    # Re-runs after every uvicorn --reload worker restart (fresh lifespan).
    try:
        import asyncio as _asyncio
        _task = _asyncio.create_task(_prewarm_embedding_model())
        # Retain a reference so the task is not GC'd/cancelled before it runs.
        _BACKGROUND_TASKS.add(_task)
        _task.add_done_callback(_BACKGROUND_TASKS.discard)
    except Exception as _e:  # pragma: no cover - defensive
        print(f"[startup] warmup task spawn skipped: {_e}")

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
    # Rebuild memory BM25 indexes + best-effort embed pending memory chunks
    try:
        from app.services import memory_archive
        _asyncio.create_task(memory_archive.process_pending_memory())
        print("Memory archive startup task scheduled")
    except Exception as e:
        print(f"Memory archive startup warning: {e}")
    # Conversation deletion drops the parent row inline and purges the child rows
    # in a throttled background task. A process that died between the two leaves
    # orphans behind, so sweep them on the way up (batched, so a large backlog
    # cannot slow startup down).
    try:
        from app.services import conversation_purge
        _asyncio.create_task(conversation_purge.sweep_orphans())
        print("Conversation orphan sweep scheduled")
    except Exception as e:
        print(f"Conversation orphan sweep warning: {e}")
    # Initialize MCP tool registry
    try:
        from app.services.tool_registry import tool_registry
        _asyncio.create_task(tool_registry.refresh())
        print("Tool registry refresh scheduled")
    except Exception as e:
        print(f"Tool registry init warning: {e}")
    # Load Python Executor tools as always-available meta tools (claw's native
    # file/code execution). Fetches the tool list from the Python Executor MCP
    # server and caches it for injection into every conversation.
    try:
        from app.services.agent_nodes import _refresh_meta_python_tools
        await _refresh_meta_python_tools()
        print("Meta python tools (Python Executor) loaded at startup")
    except Exception as e:
        print(f"Meta python tools init warning: {e}")
    # One-time migration: legacy data/skills/* -> shared store/* (preserves
    # enabled state). Copy-only by default so nothing is lost if the shared
    # skills volume is not yet mounted; runs idempotently each boot.
    try:
        from app.services.skill_manager import migrate_legacy_skills
        _mig = migrate_legacy_skills()
        if _mig["migrated"] or _mig["skipped"]:
            print(
                f"Skill migration (legacy data/skills -> store): "
                f"+{_mig['migrated']} migrated, {_mig['skipped']} already present, "
                f"{_mig['enabled']} enabled"
            )
    except Exception as e:
        print(f"Skill migration warning: {e}")
    # Seed preset (factory-default) skills baked into the image. Idempotent:
    # copies into store/ + enables only when absent, so user edits persist.
    try:
        from app.services.skill_manager import seed_preset_skills
        _seed = seed_preset_skills()
        if _seed["seeded"] or _seed["skipped"]:
            print(
                f"Skill preset seeding (skill_seed_dir -> store): "
                f"+{_seed['seeded']} seeded, {_seed['skipped']} already present, "
                f"{_seed['enabled']} enabled"
            )
    except Exception as e:
        print(f"Skill preset seeding warning: {e}")
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
    description="Enterprise-grade Agentic RAG platform - Lite edition (multi-tenant, SKILL + MCP)",
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
from app.routers import auth, users, documents, knowledge_bases, retrieval, chat, stats, config, skills, mcp_servers, plugins, cron_jobs, notifications, embedding_model, workspace
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(knowledge_bases.router)
app.include_router(retrieval.router)
app.include_router(chat.router)
app.include_router(stats.router)
app.include_router(config.router)
app.include_router(skills.router)
app.include_router(mcp_servers.router)
app.include_router(plugins.router)
app.include_router(cron_jobs.router)
app.include_router(notifications.router)
app.include_router(embedding_model.router)
app.include_router(workspace.router)


# ───────────────────────────────────────────────────────────────────────────
# Global exception handler (observability — P0).
# In `uvicorn --reload` dev mode, Starlette's ServerErrorMiddleware logs
# unhandled tracebacks to the `uvicorn.error` logger, which frequently does NOT
# reach `docker logs`. Register a handler on the base Exception class so the
# full traceback is emitted via the `ragclaw` logger — logging_config.setup_logging()
# guarantees it reaches stderr and survives reload's disable_existing_loggers.
# This ensures EVERY 500 is visible in the container logs.
# NOTE: FastAPI's HTTPException (→ 4xx) and RequestValidationError (→ 422) keep
# their dedicated handlers and are matched first by Starlette's ExceptionMiddleware,
# so they are intentionally NOT intercepted here.
# ───────────────────────────────────────────────────────────────────────────
_logger = logging.getLogger("ragclaw")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    _logger.error(
        "Unhandled exception: %s %s\n%s",
        request.method,
        request.url.path,
        _tb.format_exc(),
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


@app.middleware("http")
async def _ensure_logging_middleware(request: Request, call_next):
    # uvicorn --reload re-applies its own log_config AFTER our lifespan
    # setup_logging(), which sets disable_existing_loggers=True (disabling
    # ragclaw.* loggers), resets root.level to WARNING, and replaces our root
    # handler with uvicorn's. That left request-time ragclaw.* INFO silently
    # dropped. Re-applying setup_logging() on every request guarantees
    # ragclaw.* INFO reaches docker logs. setup_logging() is idempotent + cheap.
    setup_logging()
    return await call_next(request)


@app.get("/api/health")
async def health_check():
    from app.services.config_manager import config_manager
    # llm_reachable reflects whether the configured LLM API has been verified
    # reachable with a real request. If configured but not yet verified, probe
    # once now (and cache the result) so the chat input state is accurate.
    llm_reachable = config_manager.is_reachable
    if config_manager.is_configured and not llm_reachable:
        llm_reachable = await config_manager.test_reachability()
    return {
        "status": "ok",
        "service": "RAGClaw-Lite",
        "version": "0.5.0",
        "llm_reachable": llm_reachable,
        "context_window": config_manager.context_window,
    }


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

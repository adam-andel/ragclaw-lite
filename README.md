# RAGClaw

> **EN** — An agent that *does* more than it *answers*. RAGClaw fuses retrieval-augmented grounding (**RAG**) with a native execution engine (**Claw**) so the agent can act on your files and code, not just talk.

> Deployment image / container name `RAGClaw-Lite` · v0.5.0 · FastAPI + LangGraph + ChromaDB + SKILL + MCP

> **Default credentials** — username `admin` · password `admin123` (change it after first login)

---

## What is RAGClaw

RAGClaw is an **agentic platform** — not a Q&A bot, and not merely a knowledge-base middle tier. Its name is its thesis: **RAG + Claw**, two engines working together.

- **RAG — the "knowing" engine.** When a question touches your knowledge base or documents, RAGClaw retrieves the relevant references (hybrid: vector + BM25) and injects them into the conversation context, grounding the agent's reasoning in *your own* data.
- **Claw — the "doing" engine.** The agent is born with native **file-management** and **code-execution** abilities. It can operate a workspace like a terminal — run Python / Shell / Node.js, process data, generate files — through the built-in REPL sandbox MCP Server (`run_python` / `run_shell` / `run_javascript`) and the workspace file-management API.

The two are **co-equal**: RAG supplies reliable context; Claw turns that context into action. The result is an agent that **retrieves → reasons → acts → produces artifacts**, rather than one that only chats or only looks things up.

In one line: **RAG "knows" (retrieval-augmented on your knowledge base); Claw "does" (operates the workspace and produces results)** — together they form an agent that actually ships.

---

## 🔑 Core Features

1. **Claw native execution**
   - **EN** — run Python / Shell / Node.js directly via the REPL sandbox MCP Server, plus workspace file management (list / read-write / upload / download / zip). Operate files, run code, and produce outputs like you would in a terminal.

2. **Hardened execution sandbox**
   - **EN** — Claw's code runs in a separate `mcp-repl` container on an internal-only network, with per-user UID isolation, a brokered network-egress policy, and container hardening (`read_only` + `seccomp` + `cap_drop`) — safe to "let it act". *(deep dive: 🧱 The Sandbox below)*

3. **Shared, co-managed workspace**
   - **EN** — a local-folder-like file space you and the agent manage together: breadcrumb nav, list/grid views, drag-drop upload, batch zip, and recursive search, all backed by the same volume the sandbox writes to. *(deep dive: 📁 The Workspace below)*

4. **SKILL system**
   - **EN** — intent routing, dedicated System Prompt, tool binding; skills live as folders (`data/skills/<name>/`, with `SKILL.md` + `references/` + `assets/` + `scripts/`), managed by DB + UI.

5. **MCP tool integration**
   - **EN** — HTTP/stdio dual transport, multi-turn tool calls, independent timeouts + error degradation.

6. **Hybrid RAG retrieval** (retrieval-augmented + structural chunking)
   - **EN** — when a question touches the knowledge base / documents, a hybrid retriever recalls **reference docs** and injects them into the context for the agent to cite, rather than standing as an independent knowledge middle tier. The hybrid search runs **vector search and BM25 in parallel** (scores then fused by `hybrid_search`), and the whole retrieval step is fanned out to run **in parallel with skill routing** — so retrieval never blocks on the router's LLM call. Chunking is structural: split by the heading tree, not fixed length.

---

## 🚀 Quick Start

Two ways to run: **Production** (code baked into the image), **Development** (Docker hot-reload, recommended for daily dev). A pure-local (no-Docker) mode is not supported yet.

> **⚠️ Single-worker hard constraint.** The backend runs uvicorn with exactly **one worker** (`--workers 1`, pinned in the Dockerfile; dev uses `--reload`, which also forces one). RAGClaw relies on process-local singletons — the LLM concurrency semaphore, the in-memory BM25 index, the answer cache, and the per-process Chroma client — that must **not** be forked across multiple workers. Never override the command with `--workers N`. To scale, run more containers (horizontal), not more workers per container (vertical).

### Method 1 — Production (code baked into the image)

```bash
# 1. Configure environment (optional)
# This step is optional: if the defaults already work for you, you can skip it and start with built-in defaults.
# Check .env.example to see all the default values.
cp .env.example .env

# 2. Build & start (uses docker-compose.yml only; source is baked into the image)
docker compose -f docker-compose.yml up -d

# 3. Access
# In production ALL traffic goes through the nginx reverse proxy — the only
# published entry point. nginx publishes :80 (HTTP) and :443 (HTTPS, when
# enabled), pinned by RAGCLAW_HTTP_PORT / RAGCLAW_HTTPS_PORT, or random if unset.
# The startup script auto-detects the real port and prints the URL.
# You can also query the actual host port(s) anytime:
docker compose -f docker-compose.yml port nginx 80
# docker compose -f docker-compose.yml port nginx 443   # only when HTTPS is enabled
```

> Production mode reads only `docker-compose.yml`: backend is loaded via `PYTHONPATH=/app/backend`, frontend is built into a static `dist` served by the backend, and the **nginx** reverse proxy (TLS optional, toggled on the Settings → HTTPS page) is the only published entry point — all traffic is proxied to the backend, whose container port is not exposed on the host. Everything is packaged into the image, suited for demos and production.

### Method 2 — Development (Docker hot-reload, recommended) ⭐

> **Prerequisite: run the project inside the WSL2 filesystem** (e.g. `//wsl$/Ubuntu/home/adam/ragclaw`).
> Windows host mounts are forwarded via 9P/gRPC-FUSE and are extremely slow on I/O; WSL2's native ext4 gives the best bind-mount performance.
>
> **macOS / Linux**: no WSL2 step needed — just open a native terminal at the project root. On macOS the mcp-repl hot-reload watcher uses `fswatch` (install via `brew install fswatch`); on Linux it uses `inotify-tools` (`sudo apt-get install -y inotify-tools`). The browser auto-open falls back to `open` on macOS and `xdg-open` on Linux.

```bash
# In a WSL2 terminal, from the project root
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

- **Backend (in-container `:8000`, NOT published to host)** — local `./backend` is bind-mounted into the container; `uvicorn --reload` watches for changes and restarts the worker automatically, **no image rebuild needed**. The backend is reachable only inside the compose network (the Vite dev server and nginx proxy to it via `ragclaw:8000`); its port is not published, so `RAGCLAW_PORT` is no longer used.
- **Frontend (in-container `:5173`, random host port)** — a separate `frontend-dev` container runs Vite HMR; `/api` is proxied to the backend via `VITE_PROXY_TARGET=http://ragclaw:8000` (over the compose network, not `localhost`). Set `RAGCLAW_FRONTEND_PORT` to pin the host port.
- The daily access URL is printed by the startup script (host port is random — use `docker compose ... port frontend-dev 5173` or the script output; **don't assume `http://localhost:5173`**).

> `docker-compose.dev.yml` is an overlay only; the `ragclaw` / `mcp-repl` / `ragclaw-egress` services from `docker-compose.yml` still start. See "🛠️ Development Mode (hot-reload)" below.

---

## 📐 Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend: Vue3 + TypeScript + NaiveUI + UnoCSS              │
│  (Chat / Workspace / Skills / Knowledge Base management UI)  │
├──────────────────────────────────────────────────────────────┤
│  FastAPI monolith                                             │
│  ├─ LangGraph Agent (the agent's brain)                      │
│  │   ├─ SKILL routing (LLM intent recognition / skill pick)  │
│  │   ├─ tool decision (call Claw's "hands" or external MCP)  │
│  │   ├─ tool execution (incl. Claw native abilities ↓)       │
│  │   └─ context building (fold RAG references into the prompt)│
│  ├─【Engine: Claw】REPL sandbox (Python / Shell / Node.js)    │
│  │   └─ workspace file management (list / read-write / upload / download / zip)  │  ← also the user-facing Workspace UI (shared volume)
│  ├─【Engine: RAG】doc parsing + structural chunking + embed + hybrid retrieval │
│  ├─ streaming chat (SSE)                                      │
│  └─ LRU result cache                                          │
├──────────────────────────────────────────────────────────────┤
│  Storage layer (zero external runtime deps)                  │
│  ├─ SQLite: metadata + SKILL/MCP config                      │
│  ├─ ChromaDB: vector store + Mem0 memory                      │
│  └─ local filesystem: raw docs + workspace files             │
└──────────────────────────────────────────────────────────────┘
```

> **RAG ↔ Claw.** Claw's native file/code abilities (REPL sandbox + workspace API) are always available — the agent's "hands". RAG retrieval fires only when a conversation touches the knowledge base / documents, feeding "reference docs" into context as one of its knowledge sources. Both are first-class engines of the same agent.

> **Deployment entry point (nginx).** In production, all inbound traffic reaches the app through the **nginx** reverse proxy — the only published entry point. The backend listens on `:8000` only inside the internal `ragclaw` network (not published to the host). nginx terminates TLS when HTTPS is enabled (toggle it on **Settings → HTTPS**, paste a cert + key, and nginx hot-reloads), and otherwise serves plain HTTP. See the `nginx/` directory and `RAGCLAW_HTTP_PORT` / `RAGCLAW_HTTPS_PORT` in `.env.example`.

---

## 🧱 The Sandbox (REPL Sandbox)

**EN**

- **Three execution modes.** `run_python` runs each session in an isolated Python sub-interpreter (`interpreters`, PEP 734) with a private `dict` namespace and a curated builtins allowlist — `__builtins__` and dangerous builtins (`exec`/`eval`/`compile`/`open`/`__import__`/`input`/…) are blocked. It runs within its workspace directory, and its **network access is policy-controlled**: blocked by default (`deny`), opened only when you choose `allow`/`allowlist` (see below). `run_shell` runs `/bin/bash` as an unprivileged per-user `user_u<uid>` (via `setuid`), cwd = the user's workspace, with `HTTP(S)_PROXY` pointed at the egress broker. `run_javascript` runs Node.js `vm` modules in a sandboxed context, with `fetch` available and routed through the proxy.
- **Per-user workspace isolation.** Files live under `/app/workspace/user_u<uid>/<ws>/` on a dedicated *persistent* volume (`ragclaw_workspace`) — isolated per user and surviving `mcp-repl` restarts. Any tool output that should leave the sandbox is served to the user via the backend's `/api/download/{file_path}` proxy under the same per-user path.
- **Network egress is policy-controlled, not absent.** The sandbox container has **no direct internet route** — every outbound connection is forced through `ragclaw-egress` via `HTTP(S)_PROXY`. The broker then enforces the **policy you select**: `deny` (default — all egress blocked), `allowlist` (only the domains you configure may connect), or `allow` (fully open, for debugging). So the sandbox is never silently online — *you* decide how much network it gets. The policy lives in `/repl-policy/repl_network_policy.json` and hot-reloads on `PUT /policy`.
- **Authenticated identity & least privilege.** The backend signs every request with an HMAC `REPL_AUTH_SECRET`; `mcp-repl` allocates a pool of unprivileged UIDs (default 100000–110000), `setuid`s children down to them and `chown`s their workdirs. Auth is mandatory — no anonymous execution.
- **Container hardening.** `read_only: true` rootfs, `tmpfs /tmp` for shell/node scratch, `no-new-privileges`, a custom `seccomp.json` blocking dangerous syscalls, `cap_drop: ALL` (only `SETUID`/`SETGID`/`CHOWN` re-added for isolation), plus memory / pids limits. No host port is published — the MCP server is internal-only.

---

## 📁 The Workspace

> A shared, local-folder-like file space that **you and the agent manage together**.

Most agents hand you nothing but a download link. RAGClaw gives you a **real workspace** — a file browser that feels like your OS file manager, and, crucially, **it is the very same workspace the Claw writes to**. The agent can create, run, and produce files there; you can open, rename, move, edit, or delete them — and the agent sees your changes too. Two-way, full control, no export-and-import dance.

**Why it feels like a local folder**

**EN**

- **Native browsing.** Breadcrumb navigation, **list *and* grid (card) views**, type filters (Office / PDF / images / archives / JSON…), and eight sort options (name / time / size / type). Recursive filename search reaches into subfolders and tells you when results are truncated.
- **Everything a file manager does.** Create folders, upload via **drag-and-drop with a concurrent pool** that shows per-file progress and supports pause / resume / cancel, download single files or a whole selection as one ZIP, rename, move (a directory picker that refuses to drop a folder into itself), and batch delete.

**Co-managed with the agent**

**EN**

Because the workspace is shared with the sandbox, what you drop in becomes instantly available to the agent, and what the agent generates shows up right in your folder — ready to grab, tweak, or ship. It's the connective tissue between "the agent did something" and "you can actually use the result."

> Backed by the same `ragclaw_workspace` volume the sandbox uses, so your files **survive restarts** and stay **isolated per user**. The frontend `WorkspaceView.vue` is the UI; the `/api/workspace/*` endpoints (backend `routers/workspace.py`) are the same API the agent calls.

---

## 📂 Project Structure

```
ragclaw/
├── docker-compose.yml          # Production deploy (code baked into image; nginx entry point)
├── docker-compose.dev.yml      # Dev overlay: bind mount + hot-reload (used with the above)
├── Dockerfile                  # Multi-stage build (production image)
├── nginx/                      # nginx reverse proxy: TLS termination, the only published entry point
│   ├── Dockerfile
│   └── tls-entrypoint.sh       # renders conf from shared volume + inotify hot-reload
├── frontend/Dockerfile.dev     # Frontend dev image (Vite HMR)
├── .env.example                # Environment variable template
│
├── backend/                    # Python FastAPI
│   ├── requirements.txt         # Floating dev deps (dev image source of truth)
│   ├── requirements.lock         # Pinned deps (production; generated by dev freeze)
│   ├── requirements-dev.txt      # Dev/test deps (pytest) — not baked into images
│   ├── pytest.ini                # pytest config (moved from pyproject.toml)
│   └── app/
│       ├── main.py             # Entry point
│       ├── config.py           # Config
│       ├── database.py         # SQLite entry (init_db: create_all + patches + drift check + seed)
│       ├── schema_patches.py   # Idempotent schema patches (add/drop column, type change, ...)
│       ├── models/             # ORM models (incl. Skill/MCPServer)
│       ├── schemas/            # Pydantic (incl. skill/mcp schema)
│       ├── routers/            # API routes
│       │   ├── workspace.py    # 【Claw】workspace file-management API (/api/workspace/*)
│       │   ├── chat.py         # Streaming chat API
│       │   ├── skills.py        # Skill management
│       │   ├── mcp_servers.py  # MCP Server management
│       │   └── …               # KB / docs / users / memory / notifications / cron etc.
│       ├── services/           # Business logic
│       │   ├── agent_state.py  # LangGraph state definition
│       │   ├── agent_nodes.py  # LangGraph graph nodes (entry/branch/join/skill-route/skill-load/tool-decision/tool-exec/context-build …)
│       │   ├── agent_graph.py  # StateGraph orchestration
│       │   ├── mcp_client.py   # MCP client + tool aggregation
│       │   ├── tool_registry.py # Tool registry (MCP tools + meta-tools)
│       │   ├── skill_script_loader.py # Skill script loader (AST-parse scripts/*.py → tools)
│       │   ├── rag_pipeline.py # RAG pipeline (retrieval + rerank)
│       │   └── …               # llm_factory / retrieval_service / ws_manager / skill_manager …
│       └── parsers/            # Document parsers
│
├── mcp/                        # 【Claw execution engine】REPL sandbox (mcp-repl + ragclaw-egress)
│   ├── repl_mcp_server.py       # Configurable multi-language executor (Python/Shell/Node.js) + workspace file mgmt
│   ├── python_repl_mcp_server.py# Isolated Python sub-interpreter data-science runtime (pandas/docx/pptx/…)
│   ├── egress_proxy.py          # Network egress broker (HTTP(S) proxy + allowlist-aware DNS)
│   ├── seccomp.json             # Custom seccomp profile (blocks dangerous syscalls)
│   ├── Dockerfile / Dockerfile.egress / entrypoint.sh  # Sandbox + egress images
│   └── requirements.txt         # Data-science runtime deps
│
├── frontend/                   # Vue3 + Vite
│   ├── package.json
│   ├── vite.config.ts          # Vite config (vite.config.js takes priority, see below)
│   ├── vite.config.js          # /api proxy target reads VITE_PROXY_TARGET, falls back to localhost:8000
│   └── src/
│       ├── views/              # Pages (Chat / Workspace / Skills / McpServers / KB …)
│       ├── components/         # Components
│       ├── api/                # API wrappers (incl. skills.ts, mcp.ts)
│       ├── stores/             # Pinia
│       └── types/              # TS types
│
└── data/                       # Runtime data
    ├── chroma/                 # Vector store
    ├── sqlite/                 # Database
    ├── uploads/                # Documents
    └── skills/                 # Skill folders (<name>/ with SKILL.md + scripts/)
```

---

## 🛠️ Development Mode (hot-reload)

For daily development, use the Docker dev mode (`docker-compose.dev.yml` overlay) — source changes take effect instantly, no repeated image rebuilds.

### Why run it inside WSL2

Docker Desktop uses the WSL2 backend. If the project lives on the Windows host, bind mounts are forwarded to the Linux VM via 9P/gRPC-FUSE, making file I/O noticeably slower. Placing the project inside a WSL2 distro's filesystem (native ext4) mounts directly, with near-local performance and the best hot-reload experience.

> Example path: `//wsl$/Ubuntu/home/adam/ragclaw`

### Start

```bash
# In a WSL2 terminal, from the project root
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

- **Backend (in-container `:8000`, NOT published to host)** — `./backend` is bind-mounted into `/app/backend`; `uvicorn --reload --reload-dir backend` watches for changes and restarts the worker. Since the image sets `PYTHONPATH=/app/backend` ahead of `site-packages`, no editable install is needed. The backend is reachable only inside the compose network (the Vite dev server proxies `/api` to `ragclaw:8000`); its port is not published, so `RAGCLAW_PORT` is no longer used.
- **Frontend (in-container `:5173`, random host port)** — the `frontend-dev` container runs Vite; `/api` is proxied over the compose network via `VITE_PROXY_TARGET=http://ragclaw:8000` (in-container `localhost` points to itself, so the service name `ragclaw` is required). Set `RAGCLAW_FRONTEND_PORT` to pin the host port.

### Hot-reload behavior

- **Backend**: editing any `.py` under `backend/` restarts a single worker (debounced — batched edits won't cascade).
- **Frontend**: editing files under `frontend/src/` triggers Vite HMR, module-level hot swap, almost no full-page refresh.

### After adding dependencies

- **Backend**: Python deps are installed in the image's `site-packages` (not in the mount dir). After editing `backend/requirements.txt`, rebuild the image:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build ragclaw
  ```
- **Frontend**: after editing `frontend/package.json`, run `pnpm install` inside the `frontend-dev` container, or just rebuild the service:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build frontend-dev
  ```

### File responsibilities

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Production deploy; source baked into image |
| `docker-compose.dev.yml` | Dev overlay: backend bind mount + hot-reload, `frontend-dev` service |
| `frontend/Dockerfile.dev` | Frontend dev image (corepack + pnpm + Vite HMR) |
| `vite.config.js` | `/api` proxy target reads `VITE_PROXY_TARGET`, falls back to `localhost:8000` |

> Local-only (Docker-free) is not yet supported — use Method 1 / 2 above.

---

## 🛠️ Tech Stack

| Layer | Tech | Notes |
|-------|------|-------|
| Backend framework | FastAPI | Async-native, auto OpenAPI |
| Agent orchestration | LangGraph | Declarative state graph, conditional routing + multi-turn tool calls |
| Execution engine (Claw) | REPL sandbox (Python / Shell / Node.js) | Multi-language code exec + workspace file management (the agent's "hands") |
| Vector DB | ChromaDB | Embedded, zero-config (RAG vector store) |
| Meta DB | SQLite + SQLAlchemy (declarative) | ORM models are schema source of truth; auto-create on startup |
| Embedding | BGE-small-zh-v1.5 | 384-dim Chinese vectors |
| LLM | OpenAI / Qwen / Ollama | Swappable, supports tool calling |
| Memory | Mem0 (optional) | Cross-session memory, loaded in parallel without adding latency |
| Tool protocol | MCP (HTTP + stdio) | External tool integration |
| Frontend | Vue3 + TS + NaiveUI | Enterprise admin UI (workspace / skills / KB) |
| Build | Vite + UnoCSS | Sub-second HMR |
| Deploy | Docker Compose + nginx | Production bakes code into image; **nginx** is the single entry point (TLS optional, hot-reload on cert change); dev mode overlays hot-reload |

---

## 🗄️ Database

The ORM models in `backend/app/models/` are the schema source of truth. There are
no migration files and no revision chain. Every startup runs three idempotent
stages:

1. **`create_all(checkfirst=True)`** — creates whole missing tables from the models.
   It never modifies a table that already exists.
2. **Idempotent patches** (`backend/app/schema_patches.py`) — everything step 1
   cannot do: add/drop a column, change a type, rebuild an index, backfill data.
   Each patch guards itself on *live schema state*, so it is safe to re-run forever
   and there is no ordering constraint between stacks.
3. **Drift detection** — compares the models against the live database and logs an
   error listing anything still missing, so a model changed without a matching
   patch is caught at startup instead of failing mid-request later.

### Fresh install

Delete `data/sqlite/ragclaw.db` and start the backend — the DB and seed data rebuild automatically.

### Evolve schema

**Adding a whole new table** — define the model, restart. Done; `create_all` picks
it up.

**Any change to an existing table** (add/drop column, change type, add index,
backfill) — edit the model *and* append a patch to `backend/app/schema_patches.py`:

```python
Patch(
    name="users.avatar_url",
    applied=lambda insp: has_column(insp, "users", "avatar_url"),
    apply=["ALTER TABLE users ADD COLUMN avatar_url TEXT"],
)
```

The model change makes fresh installs correct; the patch brings existing databases
to the same shape. Forget the patch and startup logs a loud `DRIFT DETECTED` error
naming the missing column.

Rules: guard on **live schema state**, never on a version number — a sibling stack
may have applied a different subset. Patches are **append-only**; deleting one
breaks any database that has not applied it yet. For a drop, invert the predicate
(`applied` means "desired end state reached", so a drop is applied once the column
is gone). Pass a callable instead of a SQL list for multi-step work such as
SQLite's table-rebuild dance.

Multi-stack is safe by construction: every stack runs the same self-guarding
patches in any order, with no shared version counter to conflict over.

---

## 📝 API Docs

After starting, visit Swagger UI at the nginx entry point (host port is randomly assigned by Docker by default, unless `RAGCLAW_HTTP_PORT` / `RAGCLAW_HTTPS_PORT` is set in `.env`):

- The startup script prints the real URL, e.g. `http://localhost:<actual-port>/docs` (or `https://localhost:<actual-port>/docs` when HTTPS is enabled)
- Or query manually: `docker compose -f docker-compose.yml port nginx 80` (and `... port nginx 443` when HTTPS is enabled)

**New endpoints (v0.5.0)**:

| Endpoint | Notes |
|----------|-------|
| `GET/POST/DELETE /api/workspace/*` | 【Claw】workspace file management (list / read-write / upload / download / zip) |
| `POST/GET /api/skills` | SKILL management |
| `PATCH/DELETE /api/skills/{id}` | SKILL edit/delete |
| `POST/DELETE /api/skills/{id}/tools` | Tool bind/unbind |
| `POST/GET /api/mcp/servers` | MCP Server management |
| `PATCH/DELETE /api/mcp/servers/{id}` | MCP Server edit/delete |
| `POST /api/mcp/servers/{id}/test` | MCP connection test |
| `POST /api/chat/stream` | New `skill_id` param |
| `GET / PUT /api/config/https` | HTTPS settings (enable + cert/key); nginx hot-reloads on change |

---

## 🤝 Contributing

Contributions are welcome. Please open an issue to discuss substantial changes first, then submit a PR against `main`. (Guidelines to be expanded.)

## 📄 License

TODO — choose a license (e.g. Apache-2.0 / MIT) and add the `LICENSE` file.

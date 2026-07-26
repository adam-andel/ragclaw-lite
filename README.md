# RAGClaw

> **EN** — An agent that *does* more than it *answers*. RAGClaw fuses retrieval-augmented grounding (**RAG**) with a native execution engine (**Claw**) so the agent can act on your files and code, not just talk.

> **中文** — 不止于"问答"。RAGClaw 将「检索增强（RAG）」与「原生执行引擎（Claw）」双引擎合一，让智能体既能基于你的知识库获得可靠上下文，也能直接在工作区里动手操作文件、运行代码、产出结果。

> 部署镜像 / 容器名 `RAGClaw-Lite` · v0.5.0 · FastAPI + LangGraph + ChromaDB + SKILL + MCP

> **初始账号 / Default credentials** — 用户名 `admin` · 密码 `admin123`（首次登录后请尽快修改）
> **Default credentials** — username `admin` · password `admin123` (change it after first login)

---

## What is RAGClaw / 它是什么

RAGClaw is an **agentic platform** — not a Q&A bot, and not merely a knowledge-base middle tier. Its name is its thesis: **RAG + Claw**, two engines working together.

- **RAG — the "knowing" engine.** When a question touches your knowledge base or documents, RAGClaw retrieves the relevant references (hybrid: vector + BM25) and injects them into the conversation context, grounding the agent's reasoning in *your own* data.
- **Claw — the "doing" engine.** The agent is born with native **file-management** and **code-execution** abilities. It can operate a workspace like a terminal — run Python / Shell / Node.js, process data, generate files — through the built-in REPL sandbox MCP Server (`run_python` / `run_shell` / `run_javascript`) and the workspace file-management API.

The two are **co-equal**: RAG supplies reliable context; Claw turns that context into action. The result is an agent that **retrieves → reasons → acts → produces artifacts**, rather than one that only chats or only looks things up.

---

RAGClaw 是一个**智能体（Agent）平台**，而不是单纯的问答机器人，也不仅是一个知识库中台。它的名字就是定位——**RAG + Claw**，两个引擎协同工作：

- **RAG（检索增强）——「懂」的引擎**：当问题涉及知识库或文档时，RAGClaw 通过混合检索（向量 + BM25）召回相关「参考文档」并注入对话上下文，让智能体的推理建立在你自己的数据之上。
- **Claw——「做」的引擎**：智能体天生拥有原生的**文件管理**与**代码执行**能力，可以像在终端里一样直接操作工作区、跑 Python / Shell / Node.js、处理数据、生成文件，落地于内置的 REPL 沙箱 MCP Server 与工作区文件管理 API。

二者**并重**：RAG 提供可靠上下文，Claw 把上下文变成实际行动。于是智能体能够**检索 → 推理 → 执行 → 产出制品**，而非只能"对话"或只能"查资料"。

一句话总结 / In one line: **RAG 负责「懂」（基于知识库检索增强），Claw 负责「做」（在工作区执行操作、产出结果）**，二者共同构成能真正落地的智能体。

---

## 🔑 Core Features / 核心亮点

1. **Claw native execution · Claw 原生执行**
   - **EN** — run Python / Shell / Node.js directly via the REPL sandbox MCP Server, plus workspace file management (list / read-write / upload / download / zip). Operate files, run code, and produce outputs like you would in a terminal.
   - **中文** — 通过 REPL 沙箱 MCP Server 直接运行 Python / Shell / Node.js，并提供工作区文件管理（列举 / 读写 / 上传 / 下载 / 压缩）。像在终端里一样操作文件、跑代码、产出结果。

2. **Hardened execution sandbox · 加固的执行沙盒**
   - **EN** — Claw's code runs in a separate `mcp-repl` container on an internal-only network, with per-user UID isolation, a brokered network-egress policy, and container hardening (`read_only` + `seccomp` + `cap_drop`) — safe to "let it act". *(deep dive: 🧱 The Sandbox below)*
   - **中文** — Claw 的代码运行在独立的 `mcp-repl` 容器里，接入无外网路由的内部网络，按用户隔离 UID、出站流量由代理统一把关，并做容器级加固（`read_only` + `seccomp` + `cap_drop`）——放心让它"动手"。

3. **Shared, co-managed workspace · 共享、共管的工作空间**
   - **EN** — a local-folder-like file space you and the agent manage together: breadcrumb nav, list/grid views, drag-drop upload, batch zip, and recursive search, all backed by the same volume the sandbox writes to. *(deep dive: 📁 The Workspace below)*
   - **中文** — 一片你和智能体共同打理、用起来像本机文件夹的工作区：面包屑导航、列表/卡片双视图、拖拽上传、批量打包、递归搜索，底层与沙盒写入的是同一卷。

4. **SKILL system · SKILL 体系**
   - **EN** — intent routing, dedicated System Prompt, tool binding; skills live as folders (`data/skills/<name>/`, with `SKILL.md` + `references/` + `assets/` + `scripts/`), managed by DB + UI.
   - **中文** — 意图路由、专属 System Prompt、工具绑定；技能以文件夹形式（`data/skills/<name>/`，含 `SKILL.md` + `references/` + `assets/` + `scripts/`）存放，由 DB + UI 管理。

5. **MCP tool integration · MCP 工具集成**
   - **EN** — HTTP/stdio dual transport, multi-turn tool calls, independent timeouts + error degradation.
   - **中文** — 支持 HTTP/stdio 双传输，多轮工具调用，独立超时 + 错误降级。

6. **Hybrid RAG retrieval · 混合 RAG 检索**（检索增强 + 结构分块）
   - **EN** — when a question touches the knowledge base / documents, a hybrid retriever recalls **reference docs** and injects them into the context for the agent to cite, rather than standing as an independent knowledge middle tier. The hybrid search runs **vector search and BM25 in parallel** (scores then fused by `hybrid_search`), and the whole retrieval step is fanned out to run **in parallel with skill routing** — so retrieval never blocks on the router's LLM call. Chunking is structural: split by the heading tree, not fixed length.
   - **中文** — 当问题涉及知识库 / 文档时，混合检索召回**参考文档**并注入对话上下文供智能体引用，而非独立的知识中台。混合检索中**向量搜索与 BM25 并行**执行（分数再由 `hybrid_search` 融合），且**整个检索阶段与技能路由并行**扇出——检索不再等待路由的 LLM 调用。分块采用结构化方式：按标题树切分，而非固定长度。

---

## 🚀 Quick Start / 快速开始

Two ways to run: **Production** (code baked into the image), **Development** (Docker hot-reload, recommended for daily dev). A pure-local (no-Docker) mode is not supported yet.

提供两种运行方式：**生产部署**（代码打包进镜像）、**开发模式**（Docker 热重载，推荐日常开发）。暂不支持纯本地（不依赖 Docker）模式。

### Method 1 — Production (code baked into the image) / 方式一：生产部署

```bash
# 1. Configure environment / 配置环境变量
# This step is optional: if the defaults already work for you, you can skip it and start with built-in defaults.
# Check .env.example to see all the default values.
# 这一步是可选的：若默认值已满足需求，可直接跳过。
# 不创建 .env 也能用内置默认值启动，可查看 .env.example 了解各项默认值。
cp .env.example .env

# 2. Build & start (uses docker-compose.yml only; source is baked into the image)
docker compose -f docker-compose.yml up -d

# 3. Access / 访问
# In production ALL traffic goes through the nginx reverse proxy — the only
# published entry point. nginx publishes :80 (HTTP) and :443 (HTTPS, when
# enabled), pinned by RAGCLAW_HTTP_PORT / RAGCLAW_HTTPS_PORT, or random if unset.
# The startup script auto-detects the real port and prints the URL.
# You can also query the actual host port(s) anytime:
docker compose -f docker-compose.yml port nginx 80
# docker compose -f docker-compose.yml port nginx 443   # only when HTTPS is enabled
```

> Production mode reads only `docker-compose.yml`: backend is loaded via `PYTHONPATH=/app/backend`, frontend is built into a static `dist` served by the backend, and the **nginx** reverse proxy (TLS optional, toggled on the Settings → HTTPS page) is the only published entry point — all traffic is proxied to the backend, whose container port is not exposed on the host. Everything is packaged into the image, suited for demos and production.
> 生产模式只读取 `docker-compose.yml`：backend 通过 `PYTHONPATH=/app/backend` 加载、frontend 构建为静态 `dist` 由后端托管，全部打包进镜像，适合演示与生产环境。

### Method 2 — Development (Docker hot-reload, recommended) ⭐ / 方式二：开发模式（热重载，推荐）

> **Prerequisite: run the project inside the WSL2 filesystem** (e.g. `//wsl$/Ubuntu/home/adam/ragclaw`).
> Windows host mounts are forwarded via 9P/gRPC-FUSE and are extremely slow on I/O; WSL2's native ext4 gives the best bind-mount performance.
> **前提：把项目放在 WSL2 文件系统内运行**。Windows 宿主机直接挂载经 9P/gRPC-FUSE 转发，I/O 极慢；WSL2 内为原生 ext4，bind mount 性能最佳。
>
> **macOS / Linux**: no WSL2 step needed — just open a native terminal at the project root. On macOS the mcp-repl hot-reload watcher uses `fswatch` (install via `brew install fswatch`); on Linux it uses `inotify-tools` (`sudo apt-get install -y inotify-tools`). The browser auto-open falls back to `open` on macOS and `xdg-open` on Linux.
> **macOS / Linux**：无需 WSL2，直接在原生终端进入项目根目录即可。macOS 的热重载监视器使用 `fswatch`（`brew install fswatch`），Linux 使用 `inotify-tools`；浏览器自动打开在 macOS 用 `open`、Linux 用 `xdg-open`。

```bash
# In a WSL2 terminal, from the project root / 在 WSL2 终端进入项目根目录
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

- **Backend (in-container `:8000`, NOT published to host)** — local `./backend` is bind-mounted into the container; `uvicorn --reload` watches for changes and restarts the worker automatically, **no image rebuild needed**. The backend is reachable only inside the compose network (the Vite dev server and nginx proxy to it via `ragclaw:8000`); its port is not published, so `RAGCLAW_PORT` is no longer used.
- **Frontend (in-container `:5173`, random host port)** — a separate `frontend-dev` container runs Vite HMR; `/api` is proxied to the backend via `VITE_PROXY_TARGET=http://ragclaw:8000` (over the compose network, not `localhost`). Set `RAGCLAW_FRONTEND_PORT` to pin the host port.
- The daily access URL is printed by the startup script (host port is random — use `docker compose ... port frontend-dev 5173` or the script output; **don't assume `http://localhost:5173`**).

> `docker-compose.dev.yml` is an overlay only; the `ragclaw` / `mcp-repl` / `ragclaw-egress` services from `docker-compose.yml` still start. See "🛠️ Development Mode (hot-reload)" below.
> `docker-compose.dev.yml` 仅作为叠加层；`docker-compose.yml` 中的 `ragclaw` / `mcp-repl` / `ragclaw-egress` 服务照常启动。详见下方「🛠️ 开发模式（热重载）」。

---

## 📐 Architecture / 技术架构

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
> **RAG 与 Claw 并重。** Claw 的原生文件 / 代码能力（REPL 沙箱 + 工作区 API）始终可用，是智能体的「手」；RAG 检索只在对话涉及知识库 / 文档时触发，把「参考文档」作为上下文喂给智能体，是其「知识来源」之一。二者同为这个智能体的一等公民。

> **Deployment entry point (nginx).** In production, all inbound traffic reaches the app through the **nginx** reverse proxy — the only published entry point. The backend listens on `:8000` only inside the internal `ragclaw` network (not published to the host). nginx terminates TLS when HTTPS is enabled (toggle it on **Settings → HTTPS**, paste a cert + key, and nginx hot-reloads), and otherwise serves plain HTTP. See the `nginx/` directory and `RAGCLAW_HTTP_PORT` / `RAGCLAW_HTTPS_PORT` in `.env.example`.
> **部署入口（nginx）。** 生产环境所有入站流量都经过 **nginx** 反向代理——它是唯一的对外入口。后端只在内部 `ragclaw` 网络监听 `:8000`（不发布到宿主）。开启 HTTPS 后 nginx 负责终结 TLS（在「设置 → HTTPS」里开启、粘贴证书 + 私钥，nginx 即热重载），否则以明文 HTTP 提供服务。详见 `nginx/` 目录与 `.env.example` 中的 `RAGCLAW_HTTP_PORT` / `RAGCLAW_HTTPS_PORT`。

---

## 🧱 The Sandbox (REPL Sandbox) / 沙盒（REPL 沙箱）

Claw 的「动手」能力运行在一个**独立的、安全加固的沙盒**里——一个专门的 `mcp-repl` 容器加一个 `ragclaw-egress` 代理容器，二者接入一张**无外网路由的内部网络**（`ragclaw-internal`，`internal: true`）。它不只是个代码执行器，而是一套刻意隔离的执行环境，是 RAGClaw 的核心亮点之一。

**EN**

- **Three execution modes.** `run_python` runs each session in an isolated Python sub-interpreter (`interpreters`, PEP 734) with a private `dict` namespace and a curated builtins allowlist — `__builtins__` and dangerous builtins (`exec`/`eval`/`compile`/`open`/`__import__`/`input`/…) are blocked. It runs within its workspace directory, and its **network access is policy-controlled**: blocked by default (`deny`), opened only when you choose `allow`/`allowlist` (see below). `run_shell` runs `/bin/bash` as an unprivileged per-user `user_u<uid>` (via `setuid`), cwd = the user's workspace, with `HTTP(S)_PROXY` pointed at the egress broker. `run_javascript` runs Node.js `vm` modules in a sandboxed context, with `fetch` available and routed through the proxy.
- **Per-user workspace isolation.** Files live under `/app/workspace/user_u<uid>/<ws>/` on a dedicated *persistent* volume (`ragclaw_workspace`) — isolated per user and surviving `mcp-repl` restarts. Any tool output that should leave the sandbox is served to the user via the backend's `/api/download/{file_path}` proxy under the same per-user path.
- **Network egress is policy-controlled, not absent.** The sandbox container has **no direct internet route** — every outbound connection is forced through `ragclaw-egress` via `HTTP(S)_PROXY`. The broker then enforces the **policy you select**: `deny` (default — all egress blocked), `allowlist` (only the domains you configure may connect), or `allow` (fully open, for debugging). So the sandbox is never silently online — *you* decide how much network it gets. The policy lives in `/repl-policy/repl_network_policy.json` and hot-reloads on `PUT /policy`.
- **Authenticated identity & least privilege.** The backend signs every request with an HMAC `REPL_AUTH_SECRET`; `mcp-repl` allocates a pool of unprivileged UIDs (default 100000–110000), `setuid`s children down to them and `chown`s their workdirs. Auth is mandatory — no anonymous execution.
- **Container hardening.** `read_only: true` rootfs, `tmpfs /tmp` for shell/node scratch, `no-new-privileges`, a custom `seccomp.json` blocking dangerous syscalls, `cap_drop: ALL` (only `SETUID`/`SETGID`/`CHOWN` re-added for isolation), plus memory / pids limits. No host port is published — the MCP server is internal-only.

**中文**

- **三种执行模式**：`run_python` 每个会话跑在独立的 Python 子解释器（`interpreters`，PEP 734）中，拥有私有 `dict` 命名空间与受控内建白名单——`__builtins__` 及 `exec`/`eval`/`compile`/`open`/`__import__`/`input` 等危险内建均被屏蔽；在自身工作目录内运行，其**网络访问由策略管控**：默认拦截（`deny`），仅在你选择 `allow`/`allowlist` 时才放开（见下）。`run_shell` 以降权的每用户 `user_u<uid>`（经 `setuid`）运行 `/bin/bash`，工作目录为用户的 workspace，并把 `HTTP(S)_PROXY` 指向出站代理。`run_javascript` 在 Node.js `vm` 沙箱上下文里执行模块，`fetch` 可用且经代理出网。
- **按用户的隔离工作区**：文件存放于 `/app/workspace/user_u<uid>/`，位于专属**持久化**卷 `ragclaw_workspace`，按用户隔离且 `mcp-repl` 重启后仍在；任何需要离开沙盒的产出都经由后端的 `/api/download/{file_path}` 代理、按同一用户路径交付。
- **出站流量由代理把关，且策略可调**：沙盒容器本身**没有直连外网的路由**——所有出站连接被强制经 `HTTP(S)_PROXY` 走 `ragclaw-egress`，由代理执行你选定的策略：`deny`（默认，全部拦截）、`allowlist`（仅放行你配置的域名）或 `allow`（完全放开，调试用）。也就是说沙盒从不会"悄悄联网"，开多少网由你决定。策略存于 `/repl-policy/repl_network_policy.json`，`PUT /policy` 即热更新。
- **真实身份 + 最小权限**：后端用 HMAC `REPL_AUTH_SECRET` 为每个请求签名；`mcp-repl` 分配一批降权 UID（默认 100000–110000），把子进程 `setuid` 到对应 UID 并 `chown` 其工作目录。鉴权强制开启——不存在匿名执行。
- **容器级加固**：根文件系统 `read_only: true`，`tmpfs /tmp` 供 shell/node 临时使用，`no-new-privileges`，自定义 `seccomp.json` 屏蔽危险系统调用，`cap_drop: ALL`（仅补回隔离所需的 `SETUID`/`SETGID`/`CHOWN`），并设内存 / 进程数上限。MCP 服务仅内部可达，不映射宿主端口。

> 沙盒代码位于 `mcp/`：`repl_mcp_server.py`（可配置的多语言执行引擎）、`python_repl_mcp_server.py`（pandas/docx/pptx/matplotlib 等数据科学运行时）、`egress_proxy.py`（出站代理 / DNS broker）、`seccomp.json`、`Dockerfile` / `Dockerfile.egress` / `entrypoint.sh`。

---

## 📁 The Workspace / 工作空间

> A shared, local-folder-like file space that **you and the agent manage together**.
> 一片「你和智能体共同打理」的共享工作区——用起来就像本机文件夹。

Most agents hand you nothing but a download link. RAGClaw gives you a **real workspace** — a file browser that feels like your OS file manager, and, crucially, **it is the very same workspace the Claw writes to**. The agent can create, run, and produce files there; you can open, rename, move, edit, or delete them — and the agent sees your changes too. Two-way, full control, no export-and-import dance.

多数智能体只丢给你一个下载链接。RAGClaw 给你的是一片**真正的工作区**——界面像操作系统里的文件管理器，而且**它正是 Claw 写入文件的同一片空间**。智能体能在此创建、运行、产出文件；你能打开、改名、移动、修改、删除——智能体也会立刻看到你的改动。双向、完全对等，没有"导出再导入"的折腾。

**Why it feels like a local folder / 为什么像本机文件夹**

**EN**

- **Native browsing.** Breadcrumb navigation, **list *and* grid (card) views**, type filters (Office / PDF / images / archives / JSON…), and eight sort options (name / time / size / type). Recursive filename search reaches into subfolders and tells you when results are truncated.
- **Everything a file manager does.** Create folders, upload via **drag-and-drop with a concurrent pool** that shows per-file progress and supports pause / resume / cancel, download single files or a whole selection as one ZIP, rename, move (a directory picker that refuses to drop a folder into itself), and batch delete.

**中文**

- **原生浏览体验**：面包屑导航，列表 / 卡片双视图，按类型筛选（Office / PDF / 图片 / 压缩包 / JSON…），八种排序（名称 / 时间 / 大小 / 类型）；文件名搜索递归子目录，结果超量还会主动提示已截断。
- **文件管理器该有的都有**：建文件夹、拖拽上传（并发队列 + 单文件进度 + 暂停 / 继续 / 取消）、单文件下载或把一批打包成 ZIP、重命名、移动（目录选择器会阻止"把文件夹移进自己"），以及批量删除。

**Co-managed with the agent / 与智能体共管**

**EN**

Because the workspace is shared with the sandbox, what you drop in becomes instantly available to the agent, and what the agent generates shows up right in your folder — ready to grab, tweak, or ship. It's the connective tissue between "the agent did something" and "you can actually use the result."

**中文**

工作区与沙盒共享，你放进去的东西智能体立即可用，智能体产出的文件也直接出现在你的文件夹里，随手就能取走、改一改或交付。它把"智能体做了点东西"和"你真正能用上这个结果"连了起来。

> Backed by the same `ragclaw_workspace` volume the sandbox uses, so your files **survive restarts** and stay **isolated per user**. The frontend `WorkspaceView.vue` is the UI; the `/api/workspace/*` endpoints (backend `routers/workspace.py`) are the same API the agent calls.
> 底层就是沙盒用的 `ragclaw_workspace` 卷，因此文件**重启不丢**且**按用户隔离**。前端 `WorkspaceView.vue` 是界面；`/api/workspace/*` 端点（后端 `routers/workspace.py`）正是智能体调用的同一套 API。

---

## 📂 Project Structure / 项目结构

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
│   ├── pyproject.toml
│   └── app/
│       ├── main.py             # Entry point
│       ├── config.py           # Config
│       ├── database.py         # SQLite entry (init_db: alembic upgrade + seed)
│       ├── migrations/         # Alembic migrations (incl. single initial-schema baseline)
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

## 🛠️ Development Mode (hot-reload) / 开发模式（热重载）

For daily development, use the Docker dev mode (`docker-compose.dev.yml` overlay) — source changes take effect instantly, no repeated image rebuilds.

日常开发推荐用 Docker 开发模式（`docker-compose.dev.yml` 叠加层），源码改动即时生效，无需反复重建镜像。

### Why run it inside WSL2 / 为什么要在 WSL2 里跑

Docker Desktop uses the WSL2 backend. If the project lives on the Windows host, bind mounts are forwarded to the Linux VM via 9P/gRPC-FUSE, making file I/O noticeably slower. Placing the project inside a WSL2 distro's filesystem (native ext4) mounts directly, with near-local performance and the best hot-reload experience.

Docker Desktop 使用 WSL2 后端。若项目位于 Windows 宿主机，bind mount 需经 9P/gRPC-FUSE 协议转发到 Linux 虚拟机，文件 I/O 明显变慢；而把项目放在 WSL2 发行版的文件系统内（原生 ext4）可直接挂载，性能接近本地，热重载体验最佳。

> Example path: `//wsl$/Ubuntu/home/adam/ragclaw`

### Start / 启动

```bash
# In a WSL2 terminal, from the project root / 在 WSL2 终端进入项目根目录
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

- **Backend (in-container `:8000`, NOT published to host)** — `./backend` is bind-mounted into `/app/backend`; `uvicorn --reload --reload-dir backend` watches for changes and restarts the worker. Since the image sets `PYTHONPATH=/app/backend` ahead of `site-packages`, no editable install is needed. The backend is reachable only inside the compose network (the Vite dev server proxies `/api` to `ragclaw:8000`); its port is not published, so `RAGCLAW_PORT` is no longer used.
- **Frontend (in-container `:5173`, random host port)** — the `frontend-dev` container runs Vite; `/api` is proxied over the compose network via `VITE_PROXY_TARGET=http://ragclaw:8000` (in-container `localhost` points to itself, so the service name `ragclaw` is required). Set `RAGCLAW_FRONTEND_PORT` to pin the host port.

### Hot-reload behavior / 热重载行为

- **Backend**: editing any `.py` under `backend/` restarts a single worker (debounced — batched edits won't cascade).
- **Frontend**: editing files under `frontend/src/` triggers Vite HMR, module-level hot swap, almost no full-page refresh.

### After adding dependencies / 新增依赖后如何处理

- **Backend**: Python deps are installed in the image's `site-packages` (not in the mount dir). After editing `backend/pyproject.toml`, rebuild the image:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build ragclaw
  ```
- **Frontend**: after editing `frontend/package.json`, run `pnpm install` inside the `frontend-dev` container, or just rebuild the service:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build frontend-dev
  ```

### File responsibilities / 文件职责对照

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Production deploy; source baked into image |
| `docker-compose.dev.yml` | Dev overlay: backend bind mount + hot-reload, `frontend-dev` service |
| `frontend/Dockerfile.dev` | Frontend dev image (corepack + pnpm + Vite HMR) |
| `vite.config.js` | `/api` proxy target reads `VITE_PROXY_TARGET`, falls back to `localhost:8000` |

> Local-only (Docker-free) is not yet supported — use Method 1 / 2 above.
> 纯本地（不依赖 Docker）方式暂不支持，请使用「快速开始 · 方式一 / 方式二」的 Docker 启动。

---

## 🛠️ Tech Stack / 技术栈

| Layer | Tech | Notes |
|-------|------|-------|
| Backend framework | FastAPI | Async-native, auto OpenAPI |
| Agent orchestration | LangGraph | Declarative state graph, conditional routing + multi-turn tool calls |
| Execution engine (Claw) | REPL sandbox (Python / Shell / Node.js) | Multi-language code exec + workspace file management (the agent's "hands") |
| Vector DB | ChromaDB | Embedded, zero-config (RAG vector store) |
| Meta DB | SQLite + SQLAlchemy + Alembic | Single-file store + versioned migrations (baseline + incremental) |
| Embedding | BGE-small-zh-v1.5 | 384-dim Chinese vectors |
| LLM | OpenAI / Qwen / Ollama | Swappable, supports tool calling |
| Memory | Mem0 (optional) | Cross-session memory, loaded in parallel without adding latency |
| Tool protocol | MCP (HTTP + stdio) | External tool integration |
| Frontend | Vue3 + TS + NaiveUI | Enterprise admin UI (workspace / skills / KB) |
| Build | Vite + UnoCSS | Sub-second HMR |
| Deploy | Docker Compose + nginx | Production bakes code into image; **nginx** is the single entry point (TLS optional, hot-reload on cert change); dev mode overlays hot-reload |

---

## 🗄️ Database / 数据库构建

Metadata uses a single-file SQLite; the schema is managed by **Alembic** versioned migrations (no more hand-rolled incremental patch scripts).

元数据使用单文件 SQLite，schema 由 **Alembic** 版本化迁移统一管理，不再依赖自研的增量补丁脚本。

- **Schema source**: `migrations/versions/2624081b4b65_initial_schema.py` (a single *initial schema* baseline that creates all 16 business tables + constraints/indexes at once). All later schema evolution is expressed via new migration files.
- **Build entry**: `app/database.py`'s `init_db()` runs, in order:
  1. `alembic upgrade head` (applies all migrations; no-op if already current);
  2. idempotent seed (writes default admin `admin`, doc-management skill `doc-manager`, Python-executor MCP Server).
- **Dependency**: `alembic` is added to `backend/pyproject.toml`.

### Fresh install / 全新安装

Delete `data/sqlite/ragclaw.db` and start the backend — the DB and seed data rebuild automatically (fits the open-source "fresh project" posture).

删除 `data/sqlite/ragclaw.db` 后启动后端，数据库与种子数据会自动重建（契合开源「全新项目」姿态）。

### Evolve schema (standard flow) / 演进 schema（标准流程）

1. Edit the ORM models under `app/models/`;
2. Generate a migration (you can isolate-test against an empty DB via `ALEMBIC_DB_URL`, leaving the real DB untouched):
   ```bash
   cd backend
   ALEMBIC_DB_URL="sqlite+aiosqlite:////tmp/test.db" \
     python -m alembic revision --autogenerate -m "add column xxx"
   ```
3. **Always review** the generated migration to confirm it only contains the intended create/alter operations before committing.

### Upgrade an existing dev DB to this baseline / 既有开发库升级到本基线

An old DB built by the legacy mechanism will error on `alembic upgrade head` because subtables already exist. Two options:

- **Keep data**: first bring the old schema up to the baseline (missing tables/columns), then `alembic stamp head` to mark it as baselined;
- **Start over**: simply delete `data/sqlite/ragclaw.db` and rebuild.
> The legacy `_migrations` table can stay (harmless); the new mechanism uses `alembic_version`.

---

## 📝 API Docs / API 文档

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

## 🤝 Contributing / 贡献

Contributions are welcome. Please open an issue to discuss substantial changes first, then submit a PR against `main`. (Guidelines to be expanded.)

欢迎贡献。涉及较大改动请先开 issue 讨论，再向 `main` 提交 PR。（细则待补充。）

## 📄 License / 许可证

TODO — choose a license (e.g. Apache-2.0 / MIT) and add the `LICENSE` file.

待补充 —— 请选择合适的开源协议（如 Apache-2.0 / MIT）并添加 `LICENSE` 文件。

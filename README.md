# RAGClaw-Lite（RAGClaw）

> 企业级 Agentic RAG 知识中台 · 精简版  
> v0.5.0 · FastAPI + LangGraph + ChromaDB + SKILL + MCP

## 🚀 快速开始

本项目提供三种运行方式：**生产部署**（代码打包进镜像）、**开发模式**（Docker 热重载，推荐日常开发）、**纯本地**（不依赖 Docker）。

### 方式一：生产部署（代码打包进镜像）

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 LLM_API_KEY

# 2. 构建并启动（仅用 docker-compose.yml，源码随镜像构建打包）
docker compose -f docker-compose.yml up -d

# 3. 访问
open http://localhost:8000
```

> 生产模式只读取 `docker-compose.yml`：backend 通过 `PYTHONPATH=/app/backend` 加载、`frontend` 构建为静态 `dist` 由后端托管，全部打包进镜像，适合演示与生产环境。

### 方式二：开发模式（Docker 热重载，推荐）⭐

> **前提：把项目放在 WSL2 文件系统内运行**（如 `//wsl$/Ubuntu/home/adam/erag`）。
> Windows 宿主机直接挂载会经 9P/gRPC-FUSE 转发，I/O 极慢；WSL2 内为原生 ext4，bind mount 性能最佳。

```bash
# 在 WSL2 终端进入项目根目录
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

- **后端 `:8000`** — 本地 `./backend` 以 bind mount 挂入容器，`uvicorn --reload` 监听改动自动重启 worker，**无需重建镜像**。
- **前端 `:5173`** — 独立的 `frontend-dev` 容器运行 Vite HMR；`/api` 经 `VITE_PROXY_TARGET=http://ragclaw:8000` 代理到后端（走 compose 网络，不是 `localhost`）。
- 日常访问前端开发服务器：**http://localhost:5173**

> `docker-compose.dev.yml` 仅作为叠加层；`docker-compose.yml` 中的 `ragclaw` / `mcp-repl` / `ragclaw-egress` 服务照常启动。详见下方「🛠️ 开发模式（热重载）」。

### 方式三：纯本地（不依赖 Docker）

```bash
# 后端
cd backend
pip install -e .
uvicorn app.main:app --reload

# 前端（新终端）
cd frontend
pnpm install
pnpm dev
# 访问 http://localhost:5173
```

**新增依赖**：`langgraph`, `langchain-core`（Agent 编排引擎）。`mem0` 为可选依赖（记忆系统）。

## 📐 技术架构

```
┌──────────────────────────────────────────────────────────────┐
│  前端：Vue3 + TypeScript + NaiveUI + UnoCSS                   │
├──────────────────────────────────────────────────────────────┤
│  FastAPI 单体应用                                             │
│  ├─ LangGraph Agent 状态图                                    │
│  │   ├─ SKILL 路由（LLM 意图识别）                            │
│  │   ├─ 并行检索（混合检索 ‖ Mem0 记忆召回）                  │
│  │   ├─ 工具决策（MCP 工具调用判断）                          │
│  │   └─ 工具执行（HTTP/stdio MCP Client）                    │
│  ├─ 文档上传解析 + 结构分块 + 向量化                          │
│  ├─ 混合检索（向量+BM25+加权融合）                            │
│  ├─ RAG 对话（SSE 流式，前端零改动）                          │
│  └─ LRU 结果缓存                                              │
├──────────────────────────────────────────────────────────────┤
│  存储层（零外部运行时依赖）                                   │
│  ├─ SQLite：元数据 + SKILL/MCP 配置                           │
│  ├─ ChromaDB：向量存储 + Mem0 记忆                            │
│  └─ 本地文件系统：原始文档                                    │
└──────────────────────────────────────────────────────────────┘
```

## 📂 项目结构

```
ragclaw/
├── docker-compose.yml          # 生产部署（代码打包进镜像）
├── docker-compose.dev.yml      # 开发叠加：bind mount + 热重载（与上面叠加使用）
├── Dockerfile                  # 多阶段构建（生产镜像）
├── frontend/Dockerfile.dev     # 前端开发镜像（Vite HMR）
├── .env.example                # 环境变量模板
│
├── backend/                    # Python FastAPI
│   ├── pyproject.toml
│   └── app/
│       ├── main.py             # 入口
│       ├── config.py           # 配置
│       ├── database.py         # SQLite 入口（init_db：alembic upgrade + seed）
│       ├── migrations/         # Alembic 迁移（含 initial schema 单条基线）
│       ├── models/             # ORM 模型（含 Skill/MCPServer）
│       ├── schemas/            # Pydantic（含 skill/mcp schema）
│       ├── routers/            # API 路由（含 skills/mcp_servers）
│       ├── services/           # 业务逻辑
│       │   ├── agent_state.py  # LangGraph 状态定义
│       │   ├── agent_nodes.py  # 5 个图节点
│       │   ├── agent_graph.py  # StateGraph 编排
│       │   ├── mcp_client.py   # MCP 客户端
│       │   └── tool_registry.py # 工具注册表
│       └── parsers/            # 文档解析器
│
├── frontend/                   # Vue3 + Vite
│   ├── package.json
│   ├── vite.config.ts          # Vite 配置（vite.config.js 优先，见下）
│   ├── vite.config.js          # /api 代理目标读 VITE_PROXY_TARGET，回退 localhost:8000
│   └── src/
│       ├── views/              # 页面（含 SkillsView, McpServersView）
│       ├── components/         # 组件
│       ├── api/                # API 封装（含 skills.ts, mcp.ts）
│       ├── stores/             # Pinia
│       └── types/              # TS 类型
│
├── docs/                       # 文档
│   ├── 项目架构说明.html        # 架构设计文档
│   ├── Agentic-RAG升级实施方案.md # 升级方案
│   ├── SKILL开发指南.md         # SKILL 开发指南
│   ├── MCP集成指南.md           # MCP 集成指南
│   └── 部署注意事项.md          # 部署与安全注意事项（密钥轮换、UID 池）
│
└── data/                       # 运行时数据
    ├── chroma/                 # 向量存储
    ├── sqlite/                 # 数据库
    └── uploads/                # 文档
```

## 🛠️ 开发模式（热重载）

日常开发推荐用 Docker 开发模式（`docker-compose.dev.yml` 叠加层），源码改动即时生效，无需反复重建镜像。

### 为什么要在 WSL2 里跑

Docker Desktop 使用 WSL2 后端。若项目位于 Windows 宿主机，bind mount 需经 9P/gRPC-FUSE 协议转发到 Linux 虚拟机，文件 I/O 明显变慢；而把项目放在 WSL2 发行版的文件系统内（原生 ext4）可直接挂载，性能接近本地，热重载体验最佳。

> 推荐路径示例：`//wsl$/Ubuntu/home/adam/erag`

### 启动

```bash
# 在 WSL2 终端进入项目根目录
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

- **后端 `:8000`** — `./backend` 以 bind mount 挂入 `/app/backend`，`uvicorn --reload --reload-dir backend` 监听改动并自动重启 worker。由于镜像中 `PYTHONPATH=/app/backend` 已优先于 `site-packages`，无需 editable 安装。
- **前端 `:5173`** — `frontend-dev` 容器运行 Vite，`/api` 经 `VITE_PROXY_TARGET=http://ragclaw:8000` 走 compose 网络代理到后端（容器内 `localhost` 指向自身，故必须用服务名 `ragclaw`）。

### 热重载行为

- **后端**：修改 `backend/` 下任意 `.py` 文件，uvicorn 重启单个 worker（带去抖，批量改动不会连环重启）。
- **前端**：修改 `frontend/src/` 下文件触发 Vite HMR，模块级热替换，整页刷新极少。

### 新增依赖后如何处理

- **后端**：Python 依赖装在镜像的 `site-packages`（不在挂载目录），改完 `backend/pyproject.toml` 后需重建镜像：
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build ragclaw
  ```
- **前端**：改完 `frontend/package.json` 后，在 `frontend-dev` 容器内执行 `pnpm install`，或直接重建该服务：
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build frontend-dev
  ```

### 文件职责对照

| 文件 | 用途 |
|------|------|
| `docker-compose.yml` | 生产部署，源码随镜像构建打包 |
| `docker-compose.dev.yml` | 开发叠加层：后端 bind mount + 热重载、前端 `frontend-dev` 服务 |
| `frontend/Dockerfile.dev` | 前端开发镜像（corepack + pnpm + Vite HMR） |
| `vite.config.js` | `/api` 代理目标读 `VITE_PROXY_TARGET`，回退 `localhost:8000` |

> 纯本地（不依赖 Docker）的开发方式见「快速开始 · 方式三」。

## 🛠️ 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI | 异步原生、自动 OpenAPI |
| Agent 编排 | LangGraph | 声明式状态图，条件路由 + 多轮工具调用 |
| 向量数据库 | ChromaDB | 嵌入式运行、零配置 |
| 元数据库 | SQLite + SQLAlchemy + Alembic | 单文件存储 + 版本化迁移（基线 + 增量） |
| Embedding | BGE-small-zh-v1.5 | 384维中文向量 |
| LLM | OpenAI / 通义千问 / Ollama | 可切换，支持 tool calling |
| 记忆系统 | Mem0（可选） | 跨会话记忆，并行加载不增加延迟 |
| 工具协议 | MCP（HTTP + stdio） | 外部工具集成 |
| 前端 | Vue3 + TS + NaiveUI | 企业级管理后台 |
| 构建 | Vite + UnoCSS | 秒级 HMR |
| 部署 | Docker Compose | 生产打包进镜像；开发模式 `docker-compose.dev.yml` 叠加热重载 |

## 🗄️ 数据库构建（Database）

元数据使用单文件 SQLite，schema 由 **Alembic** 版本化迁移统一管理，不再依赖自研的增量补丁脚本。

- **Schema 来源**：`migrations/versions/2624081b4b65_initial_schema.py`（单条 *initial schema* 基线，一次性建出全部 16 张业务表 + 约束/索引）。后续所有 schema 演进都通过新的迁移文件表达。
- **构建入口**：`app/database.py` 的 `init_db()` 依次执行
  1. `alembic upgrade head`（应用全部迁移，已是最新则为空操作）；
  2. 幂等 seed（写入默认管理员 `admin`、文档管理技能 `doc-manager`、Python 执行器 MCP Server）。
- **依赖**：`alembic` 已加入 `backend/pyproject.toml`。

### 全新安装
删除 `data/sqlite/ragclaw.db` 后启动后端，数据库与种子数据会自动重建（契合开源「全新项目」姿态）。

### 演进 schema（标准流程）
1. 修改 `app/models/` 下的 ORM 模型；
2. 生成迁移（可对空库设 `ALEMBIC_DB_URL` 隔离测试，不影响真实库）：
   ```bash
   cd backend
   ALEMBIC_DB_URL="sqlite+aiosqlite:////tmp/test.db" \
     python -m alembic revision --autogenerate -m "add column xxx"
   ```
3. **务必核对**生成的迁移文件，确认只包含预期的建表/改表操作，再提交。

### 既有开发库升级到本基线
旧库若仍按历史机制构建，直接用 `alembic upgrade head` 会因子表已存在而报错。两种处理：
- **保留数据**：先把旧库 schema 补齐到基线（缺失的表/列），再 `alembic stamp head` 标记为已到基线；
- **重新开始**：直接删除 `data/sqlite/ragclaw.db` 重建。
> 历史 `_migrations` 记录表可保留（无害），新机制改用 `alembic_version`。

## 🔑 核心亮点

1. **Agentic RAG** — LangGraph 状态图，SKILL 路由 + MCP 工具调用，非简单线性 RAG
2. **SKILL 体系** — 意图路由、专属 System Prompt、工具绑定，一次对话一个知识库
3. **MCP 工具集成** — 支持 HTTP/stdio 双传输，多轮工具调用，独立超时 + 错误降级
4. **记忆激活** — Mem0 并行读取，不影响首字延迟
5. **混合检索 + 加权融合** — 向量检索 + BM25 关键词，互补短板
6. **结构分块** — 基于标题树的结构化切分，非固定长度
7. **流式 SSE 输出** — 毫秒级首字延迟，Agent 链路前端零改动
8. **零外部运行时依赖** — SQLite + ChromaDB 全嵌入式，docker compose up 即用

## 📝 API 文档

启动后访问 `http://localhost:8000/docs` 查看 Swagger UI。

**新增端点**（v0.5.0）：

| 端点 | 说明 |
|------|------|
| `POST/GET /api/skills` | SKILL 管理 |
| `PATCH/DELETE /api/skills/{id}` | SKILL 编辑/删除 |
| `POST/DELETE /api/skills/{id}/tools` | 工具绑定/解绑 |
| `POST/GET /api/mcp/servers` | MCP Server 管理 |
| `PATCH/DELETE /api/mcp/servers/{id}` | MCP Server 编辑/删除 |
| `POST /api/mcp/servers/{id}/test` | MCP 连接测试 |
| `POST /api/chat/stream` | 新增 `skill_id` 参数 |

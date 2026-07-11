# EnterpriseRAG-Lite（ERAG）

> 企业级 Agentic RAG 知识中台 · 精简版  
> v0.5.0 · FastAPI + LangGraph + ChromaDB + SKILL + MCP

## 🚀 快速开始

### 方式一：Docker 一键部署（推荐）

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 LLM_API_KEY

# 2. 启动
docker compose up -d

# 3. 访问
open http://localhost:8000
```

### 方式二：本地开发

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
erag/
├── docker-compose.yml          # 一键部署
├── Dockerfile                  # 多阶段构建
├── .env.example                # 环境变量模板
│
├── backend/                    # Python FastAPI
│   ├── pyproject.toml
│   └── app/
│       ├── main.py             # 入口
│       ├── config.py           # 配置
│       ├── database.py         # SQLite + 自动迁移
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
│   ├── vite.config.ts
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
│   └── MCP集成指南.md           # MCP 集成指南
│
└── data/                       # 运行时数据
    ├── chroma/                 # 向量存储
    ├── sqlite/                 # 数据库
    └── uploads/                # 文档
```

## 🛠️ 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI | 异步原生、自动 OpenAPI |
| Agent 编排 | LangGraph | 声明式状态图，条件路由 + 多轮工具调用 |
| 向量数据库 | ChromaDB | 嵌入式运行、零配置 |
| 元数据库 | SQLite + SQLAlchemy | 单文件存储 + 自动迁移 |
| Embedding | BGE-small-zh-v1.5 | 384维中文向量 |
| LLM | OpenAI / 通义千问 / Ollama | 可切换，支持 tool calling |
| 记忆系统 | Mem0（可选） | 跨会话记忆，并行加载不增加延迟 |
| 工具协议 | MCP（HTTP + stdio） | 外部工具集成 |
| 前端 | Vue3 + TS + NaiveUI | 企业级管理后台 |
| 构建 | Vite + UnoCSS | 秒级 HMR |
| 部署 | Docker Compose | 一键启动 |

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

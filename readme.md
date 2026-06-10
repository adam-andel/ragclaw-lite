# EnterpriseRAG-Lite

> 企业级 RAG 知识中台 · 精简版  
> 三天开发 · Vue3 + FastAPI + ChromaDB

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

## 📐 技术架构

```
┌───────────────────────────────────────────────┐
│  前端：Vue3 + TypeScript + NaiveUI + UnoCSS    │
├───────────────────────────────────────────────┤
│  FastAPI 单体应用                              │
│  ├─ 文档上传解析 API                           │
│  ├─ 知识库管理 API                             │
│  ├─ 结构分块引擎                               │
│  ├─ 混合检索（向量+BM25+RRF融合）              │
│  ├─ RAG 对话（SSE 流式）                       │
│  └─ LRU 结果缓存                               │
├───────────────────────────────────────────────┤
│  存储层（零外部依赖）                          │
│  ├─ SQLite：元数据                             │
│  ├─ ChromaDB：向量存储                         │
│  └─ 本地文件系统：原始文档                     │
└───────────────────────────────────────────────┘
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
│       ├── database.py         # SQLite
│       ├── models/             # ORM 模型
│       ├── schemas/            # Pydantic
│       ├── routers/            # API 路由
│       ├── services/           # 业务逻辑
│       └── parsers/            # 文档解析器
│
├── frontend/                   # Vue3 + Vite
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── views/              # 页面
│       ├── components/         # 组件
│       ├── api/                # API 封装
│       ├── stores/             # Pinia
│       └── types/              # TS 类型
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
| 向量数据库 | ChromaDB | 嵌入式运行、零配置 |
| 元数据库 | SQLite + SQLAlchemy | 单文件存储 |
| Embedding | BGE-small-zh-v1.5 | 384维中文向量 |
| LLM | OpenAI / 通义千问 / Ollama | 可切换 |
| 前端 | Vue3 + TS + NaiveUI | 企业级管理后台 |
| 构建 | Vite + UnoCSS | 秒级 HMR |
| 部署 | Docker Compose | 一键启动 |

## 🔑 核心亮点

1. **文档结构分块** — 基于标题树的结构化切分，非固定长度
2. **混合检索 + RRF 融合** — 向量检索 + BM25 关键词，互补短板
3. **流式 SSE 输出** — 毫秒级首字延迟
4. **Vue3 全栈前端** — 四个专业页面，非 Demo 级 UI
5. **零外部依赖部署** — SQLite + ChromaDB 嵌入式，docker compose up 即用

## 📝 API 文档

启动后访问 `http://localhost:8000/docs` 查看 Swagger UI。

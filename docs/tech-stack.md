# Tech Stack / 技术栈

| Layer | Tech | Notes |
|-------|------|-------|
| Backend framework | FastAPI | Async-native, auto OpenAPI |
| Agent orchestration | LangGraph | Declarative state graph, conditional routing + multi-turn tool calls |
| Execution engine (Claw) | REPL sandbox (Python / Shell / Node.js) | Multi-language code exec + workspace file management (the agent's "hands") |
| Vector DB | ChromaDB | Embedded, zero-config (RAG vector store) |
| Meta DB | SQLite + SQLAlchemy (declarative) | ORM models are schema source of truth; auto-create on startup |
| Embedding | BGE-small-zh-v1.5 | 384-dim Chinese vectors |
| LLM | OpenAI / Qwen / Ollama | Swappable, supports tool calling |
| Memory | Custom memory archive (own `memory_archive.py` on ChromaDB) | Persists `MemoryChunk` rows, embeds + indexes in ChromaDB, recalled via hybrid search — no external memory service |
| Tool protocol | MCP (HTTP + stdio) | External tool integration |
| Frontend | Vue3 + TS + NaiveUI | Enterprise admin UI (workspace / skills / KB) |
| Build | Vite + UnoCSS | Sub-second HMR |
| Deploy | Docker Compose + nginx | Production bakes code into image; **nginx** is the single entry point (TLS optional, hot-reload on cert change); dev mode overlays hot-reload |
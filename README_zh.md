# RAGClaw

> **中文** — 不止于"问答"。RAGClaw 将「检索增强（RAG）」与「原生执行引擎（Claw）」双引擎合一，让智能体既能基于你的知识库获得可靠上下文，也能直接在工作区里动手操作文件、运行代码、产出结果。

> 部署镜像 / 容器名 `RAGClaw-Lite` · v0.5.0 · FastAPI + LangGraph + ChromaDB + SKILL + MCP

> **初始账号 / Default credentials** — 用户名 `admin` · 密码 `admin123`（首次登录后请尽快修改）

---

## What is RAGClaw / 它是什么

RAGClaw 是一个**智能体（Agent）平台**，而不是单纯的问答机器人，也不仅是一个知识库中台。它的名字就是定位——**RAG + Claw**，两个引擎协同工作：

- **RAG（检索增强）——「懂」的引擎**：当问题涉及知识库或文档时，RAGClaw 通过混合检索（向量 + BM25）召回相关「参考文档」并注入对话上下文，让智能体的推理建立在你自己的数据之上。
- **Claw——「做」的引擎**：智能体天生拥有原生的**文件管理**与**代码执行**能力，可以像在终端里一样直接操作工作区、跑 Python / Shell / Node.js、处理数据、生成文件，落地于内置的 REPL 沙箱 MCP Server 与工作区文件管理 API。

二者**并重**：RAG 提供可靠上下文，Claw 把上下文变成实际行动。于是智能体能够**检索 → 推理 → 执行 → 产出制品**，而非只能"对话"或只能"查资料"。

一句话总结 / In one line: **RAG 负责「懂」（基于知识库检索增强），Claw 负责「做」（在工作区执行操作、产出结果）**，二者共同构成能真正落地的智能体。

---

## 🔑 Core Features / 核心亮点

1. **Claw native execution · Claw 原生执行**
   - **中文** — 通过 REPL 沙箱 MCP Server 直接运行 Python / Shell / Node.js，并提供工作区文件管理（列举 / 读写 / 上传 / 下载 / 压缩）。像在终端里一样操作文件、跑代码、产出结果。

2. **Hardened execution sandbox · 加固的执行沙盒**
   - **中文** — Claw 的代码运行在独立的 `mcp-repl` 容器里，接入无外网路由的内部网络，按用户隔离 UID、出站流量由代理统一把关，并做容器级加固（`read_only` + `seccomp` + `cap_drop`）——放心让它"动手"。

3. **Shared, co-managed workspace · 共享、共管的工作空间**
   - **中文** — 一片你和智能体共同打理、用起来像本机文件夹的工作区：面包屑导航、列表/卡片双视图、拖拽上传、批量打包、递归搜索，底层与沙盒写入的是同一卷。

4. **SKILL system · SKILL 体系**
   - **中文** — 意图路由、专属 System Prompt、工具绑定；技能以文件夹形式（`data/skills/<name>/`，含 `SKILL.md` + `references/` + `assets/` + `scripts/`）存放，由 DB + UI 管理。

5. **MCP tool integration · MCP 工具集成**
   - **中文** — 支持 HTTP/stdio 双传输，多轮工具调用，独立超时 + 错误降级。

6. **Hybrid RAG retrieval · 混合 RAG 检索**（检索增强 + 结构分块）
   - **中文** — 当问题涉及知识库 / 文档时，混合检索召回**参考文档**并注入对话上下文供智能体引用，而非独立的知识中台。混合检索中**向量搜索与 BM25 并行**执行（分数再由 `hybrid_search` 融合），且**整个检索阶段与技能路由并行**扇出——检索不再等待路由的 LLM 调用。分块采用结构化方式：按标题树切分，而非固定长度。

---

## 🚀 Quick Start / 快速开始

提供两种运行方式：**生产部署**（代码打包进镜像）、**开发模式**（Docker 热重载，推荐日常开发）。暂不支持纯本地（不依赖 Docker）模式。

> **⚠️ 单 worker 硬约束。** 后端以**且仅以 1 个** uvicorn worker 运行（`--workers 1`，已在 Dockerfile 中锁死；开发模式使用 `--reload`，同样强制单 worker）。RAGClaw 依赖若干**进程内单例**——LLM 并发信号量、内存中的 BM25 索引、答案缓存，以及每进程独立的 Chroma 客户端——它们**绝不可**被 fork 到多个 worker 中。切勿用 `--workers N` 覆盖启动命令。要扩容，请增加容器数量（横向），而非增加单容器内的 worker 数（纵向）。

### Method 1 — Production (code baked into the image) / 方式一：生产部署

```bash
# 1. Configure environment / 配置环境变量
# 这一步是可选的：若默认值已满足需求，可直接跳过。
# 不创建 .env 也能用内置默认值启动，可查看 .env.example 了解各项默认值。
cp .env.example .env
```

> 生产模式只读取 `docker-compose.yml`：backend 通过 `PYTHONPATH=/app/backend` 加载、frontend 构建为静态 `dist` 由后端托管，全部打包进镜像，适合演示与生产环境。

### Method 2 — Development (Docker hot-reload, recommended) ⭐ / 方式二：开发模式（热重载，推荐）

> **前提：把项目放在 WSL2 文件系统内运行**。Windows 宿主机直接挂载经 9P/gRPC-FUSE 转发，I/O 极慢；WSL2 内为原生 ext4，bind mount 性能最佳。
>
> **macOS / Linux**：无需 WSL2，直接在原生终端进入项目根目录即可。macOS 的热重载监视器使用 `fswatch`（`brew install fswatch`），Linux 使用 `inotify-tools`；浏览器自动打开在 macOS 用 `open`、Linux 用 `xdg-open`。

```bash
# In a WSL2 terminal, from the project root / 在 WSL2 终端进入项目根目录
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

> `docker-compose.dev.yml` 仅作为叠加层；`docker-compose.yml` 中的 `ragclaw` / `mcp-repl` / `ragclaw-egress` 服务照常启动。详见下方「🛠️ 开发模式（热重载）」。

---

## 📐 Architecture / 技术架构

> **RAG 与 Claw 并重。** Claw 的原生文件 / 代码能力（REPL 沙箱 + 工作区 API）始终可用，是智能体的「手」；RAG 检索只在对话涉及知识库 / 文档时触发，把「参考文档」作为上下文喂给智能体，是其「知识来源」之一。二者同为这个智能体的一等公民。

> **部署入口（nginx）。** 生产环境所有入站流量都经过 **nginx** 反向代理——它是唯一的对外入口。后端只在内部 `ragclaw` 网络监听 `:8000`（不发布到宿主）。开启 HTTPS 后 nginx 负责终结 TLS（在「设置 → HTTPS」里开启、粘贴证书 + 私钥，nginx 即热重载），否则以明文 HTTP 提供服务。详见 `nginx/` 目录与 `.env.example` 中的 `RAGCLAW_HTTP_PORT` / `RAGCLAW_HTTPS_PORT`。

---

## 🧱 The Sandbox (REPL Sandbox) / 沙盒（REPL 沙箱）

Claw 的「动手」能力运行在一个**独立的、安全加固的沙盒**里——一个专门的 `mcp-repl` 容器加一个 `ragclaw-egress` 代理容器，二者接入一张**无外网路由的内部网络**（`ragclaw-internal`，`internal: true`）。它不只是个代码执行器，而是一套刻意隔离的执行环境，是 RAGClaw 的核心亮点之一。

**中文**

- **三种执行模式**：`run_python` 每个会话跑在独立的 Python 子解释器（`interpreters`，PEP 734）中，拥有私有 `dict` 命名空间与受控内建白名单——`__builtins__` 及 `exec`/`eval`/`compile`/`open`/`__import__`/`input` 等危险内建均被屏蔽；在自身工作目录内运行，其**网络访问由策略管控**：默认拦截（`deny`），仅在你选择 `allow`/`allowlist` 时才放开（见下）。`run_shell` 以降权的每用户 `user_u<uid>`（经 `setuid`）运行 `/bin/bash`，工作目录为用户的 workspace，并把 `HTTP(S)_PROXY` 指向出站代理。`run_javascript` 在 Node.js `vm` 沙箱上下文里执行模块，`fetch` 可用且经代理出网。
- **按用户的隔离工作区**：文件存放于 `/app/workspace/user_u<uid>/`，位于专属**持久化**卷 `ragclaw_workspace`，按用户隔离且 `mcp-repl` 重启后仍在；任何需要离开沙盒的产出都经由后端的 `/api/download/{file_path}` 代理、按同一用户路径交付。
- **出站流量由代理把关，且策略可调**：沙盒容器本身**没有直连外网的路由**——所有出站连接被强制经 `HTTP(S)_PROXY` 走 `ragclaw-egress`，由代理执行你选定的策略：`deny`（默认，全部拦截）、`allowlist`（仅放行你配置的域名）或 `allow`（完全放开，调试用）。也就是说沙盒从不会"悄悄联网"，开多少网由你决定。策略存于 `/repl-policy/repl_network_policy.json`，`PUT /policy` 即热更新。
- **真实身份 + 最小权限**：后端用 HMAC `REPL_AUTH_SECRET` 为每个请求签名；`mcp-repl` 分配一批降权 UID（默认 100000–110000），把子进程 `setuid` 到对应 UID 并 `chown` 其工作目录。鉴权强制开启——不存在匿名执行。
- **容器级加固**：根文件系统 `read_only: true`，`tmpfs /tmp` 供 shell/node 临时使用，`no-new-privileges`，自定义 `seccomp.json` 屏蔽危险系统调用，`cap_drop: ALL`（仅补回隔离所需的 `SETUID`/`SETGID`/`CHOWN`），并设内存 / 进程数上限。MCP 服务仅内部可达，不映射宿主端口。

> 沙盒代码位于 `mcp/`：`repl_mcp_server.py`（可配置的多语言执行引擎）、`python_repl_mcp_server.py`（pandas/docx/pptx/matplotlib 等数据科学运行时）、`egress_proxy.py`（出站代理 / DNS broker）、`seccomp.json`、`Dockerfile` / `Dockerfile.egress` / `entrypoint.sh`。

---

## 📁 The Workspace / 工作空间

> 一片「你和智能体共同打理」的共享工作区——用起来就像本机文件夹。

多数智能体只丢给你一个下载链接。RAGClaw 给你的是一片**真正的工作区**——界面像操作系统里的文件管理器，而且**它正是 Claw 写入文件的同一片空间**。智能体能在此创建、运行、产出文件；你能打开、改名、移动、修改、删除——智能体也会立刻看到你的改动。双向、完全对等，没有"导出再导入"的折腾。

**Why it feels like a local folder / 为什么像本机文件夹**

**中文**

- **原生浏览体验**：面包屑导航，列表 / 卡片双视图，按类型筛选（Office / PDF / 图片 / 压缩包 / JSON…），八种排序（名称 / 时间 / 大小 / 类型）；文件名搜索递归子目录，结果超量还会主动提示已截断。
- **文件管理器该有的都有**：建文件夹、拖拽上传（并发队列 + 单文件进度 + 暂停 / 继续 / 取消）、单文件下载或把一批打包成 ZIP、重命名、移动（目录选择器会阻止"把文件夹移进自己"），以及批量删除。

**Co-managed with the agent / 与智能体共管**

**中文**

工作区与沙盒共享，你放进去的东西智能体立即可用，智能体产出的文件也直接出现在你的文件夹里，随手就能取走、改一改或交付。它把"智能体做了点东西"和"你真正能用上这个结果"连了起来。

> 底层就是沙盒用的 `ragclaw_workspace` 卷，因此文件**重启不丢**且**按用户隔离**。前端 `WorkspaceView.vue` 是界面；`/api/workspace/*` 端点（后端 `routers/workspace.py`）正是智能体调用的同一套 API。

---

## 📂 Project Structure / 项目结构

（项目结构树见 `README.md`，中英文共用同一份目录结构说明。）

---

## 🛠️ Development Mode (hot-reload) / 开发模式（热重载）

日常开发推荐用 Docker 开发模式（`docker-compose.dev.yml` 叠加层），源码改动即时生效，无需反复重建镜像。

### Why run it inside WSL2 / 为什么要在 WSL2 里跑

Docker Desktop 使用 WSL2 后端。若项目位于 Windows 宿主机，bind mount 需经 9P/gRPC-FUSE 协议转发到 Linux 虚拟机，文件 I/O 明显变慢；而把项目放在 WSL2 发行版的文件系统内（原生 ext4）可直接挂载，性能接近本地，热重载体验最佳。

> 示例路径：`//wsl$/Ubuntu/home/adam/ragclaw`

### Start / 启动

```bash
# In a WSL2 terminal, from the project root / 在 WSL2 终端进入项目根目录
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

---

## 🛠️ Tech Stack / 技术栈

（技术栈对照表见 `README.md`，中英文共用同一份表格。）

---

## 🗄️ Database / 数据库构建

Schema 以 `backend/app/models/` 下的 ORM 模型为唯一事实来源，无迁移文件、无版本链。
每次启动执行三个幂等阶段：`create_all` 补建缺失的表 → 幂等 patch 处理加列/删列/改类型
等 `create_all` 做不到的变更 → 漂移检测对比模型与实际库并报告差异。

### Fresh install / 全新安装

删除 `data/sqlite/ragclaw.db` 后启动后端，数据库与种子数据会自动重建。

### Evolve schema / 演进 schema

新增整张表：改模型后重启即可。**修改已有表**（加列/删列/改类型/加索引/回填）必须
同时在 `app/schema_patches.py` 追加一条幂等 patch —— 模型保证全新安装正确，patch 保证
存量库收敛到同一形状；忘写会在启动时被漂移检测报错。patch 只增不删，判断条件必须基于
实际库结构而非版本号。

---

## 📝 API Docs / API 文档

（API 文档说明与端点表见 `README.md`，中英文共用。）

---

## 🤝 Contributing / 贡献

欢迎贡献。涉及较大改动请先开 issue 讨论，再向 `main` 提交 PR。（细则待补充。）

## 📄 License / 许可证

待补充 —— 请选择合适的开源协议（如 Apache-2.0 / MIT）并添加 `LICENSE` 文件。

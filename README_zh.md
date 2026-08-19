# RAGClaw

> 面向中小组织私有化部署的共享智能体（Agent）平台

---

## 它是什么

RAGClaw 是面向中小组织私有化部署的共享智能体（Agent）平台，它主要有三大特性：

【易用性】 在已安装 Docker 的系统上，直接跑一条 `menu` 脚本即可在单机完成部署；

【安全性】 数据与密钥全程私有化，API 密钥集中管理、不落地不泄漏；

【可控性】 LLM 接入、SKILL、MCP Server 与沙盒网络策略全部由超级管理员统一掌管，能力边界由组织决定。

---

## 🔑 核心亮点

1. **精细的上下文管理**
   - **多级压缩**（滚动摘要 → RAG归档链）让长对话更多地留在记忆中；**异步压缩**不阻塞答案生成，减少首字延迟（TTFT）；记忆可经**混合检索召回**（向量 + BM25）；并仍可**手动修改或删除**，随时自行掌控。

2. **加固的执行沙盒**
   - Claw 的代码运行在独立的 `mcp-repl` 容器里，接入无外网路由的内部网络，按用户隔离 UID、出站流量由代理统一把关，并做容器级加固（`read_only` + `seccomp` + `cap_drop`）——放心让它"动手"。

3. **共享、共管的工作空间**
   - 一片你和智能体共同打理、用起来像本机文件夹的工作区：面包屑导航、列表/卡片双视图、拖拽上传、批量打包、递归搜索，底层与沙盒写入的是同一卷。

4. **高效又安全的共享 SKILL 体系**
   - 管理页**整包上传** skill 文件夹（或 zip），资源一次到位，方便易用。 可定制`.ragclaw/` 适配器，在第三方源码一字不改的同时，提高定位脚本的效率。带 API KEY的skill 走**注入代理**，使得多用户共享skill的同时保证 API KEY 的安全。

5. ** RAG + BM25混合检索**
   - 混合检索中**向量搜索与 BM25 并行**执行，而且LLM 可**按需主动调用** `hybrid_search` 元工具——首轮召回不足时主动补搜；面对「这些会议 / 它们」等**指代**，先结合对话历史**消除指代**、把查询改写为自包含形式再检索。

---

## 🚀 快速开始

**前提条件：** 部署前请先在本机安装 Docker（Windows / macOS 用 Docker Desktop，Linux 用 Docker Engine）。官方安装指引见 Docker 文档：

- 各平台安装指引：<https://docs.docker.com/get-docker/>

Docker 就绪后，最省事的部署方式是运行项目自带的 **控制菜单脚本**——交互式菜单，自动完成「构建镜像 → 启动容器」整套流程，按数字选一下即可。

```bash
# macOS / Linux
bash bin/sh/menu.sh          # 中文版：bash bin/sh/menu_zh.sh

# Windows（CMD 双击 / 运行）
bin\psl\menu.bat             # 中文版：bin\psl\menu_zh.bat
```

## 🛠️ 开发模式（热重载）

日常开发推荐用**开发模式**，源码改动即时生效，无需反复重建镜像。

### 为什么要在 WSL2 里跑

Docker Desktop 使用 WSL2 后端。若项目位于 Windows 宿主机，bind mount 需经 9P/gRPC-FUSE 协议转发到 Linux 虚拟机，文件 I/O 明显变慢；而把项目放在 WSL2 发行版的文件系统内（原生 ext4）可直接挂载，性能接近本地，热重载体验最佳。

---

## 📂 项目结构

（项目结构树见 [docs/project-structure.md](docs/project-structure.md)。）

---

## 🛠️ 技术栈

（技术栈对照表见 [docs/tech-stack.md](docs/tech-stack.md)。）

---

## 📝 API 文档

（API 文档说明与端点表见 [docs/api-docs.md](docs/api-docs.md)。）

---

## 🤝 贡献

欢迎贡献。涉及较大改动请先开 issue 讨论，再向 `main` 提交 PR。提交约定、代码规范、测试要求与安全敏感区域说明见 [贡献指南.md](贡献指南.md)。

## 📄 许可证

Apache License 2.0 — 详情见 [LICENSE](LICENSE) 文件。

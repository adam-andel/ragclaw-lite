# RAGClaw

> 面向中小组织私有化部署的共享智能体（Agent）平台

[![GitHub Release](https://img.shields.io/github/v/release/adam-andel/ragclaw-lite?style=flat-square&label=Release)](https://github.com/adam-andel/ragclaw-lite/releases)

**[English](./README.md) | 简体中文**

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

5. **RAG + BM25混合检索**
   - 混合检索中**向量搜索与 BM25 并行**执行，而且LLM 可**按需主动调用** `hybrid_search` 元工具——首轮召回不足时主动补搜；面对「这些会议 / 它们」等**指代**，先结合对话历史**消除指代**、把查询改写为自包含形式再检索。

---

## 📦 安装

### 环境要求
- **Docker**：Linux 需 Engine 20.10+，Windows / macOS 用 Docker Desktop（Windows 开发模式请开启 WSL2 后端）。
- **Docker Compose v2**（`docker compose`）：随 Docker Desktop 自带；Linux 需安装 `docker-compose-plugin`。
- 宿主机具备 **openssl**：首次启动生成 `config.enc` 的 AES 密钥（缺失时回退到 `/dev/urandom`）。
- **（Windows 用户）** 开发模式请在 **WSL2** 发行版目录内操作，不要用 `C:\` 路径，因为经 9P 转发的 bind mount 很慢且会破坏 inotify 热重载。详见下方[开发模式](#-开发模式热重载)。

### 1. 获取代码
```bash
git clone https://github.com/adam-andel/ragclaw-lite ragclaw-lite
cd ragclaw-lite
```

### 2. 配置环境变量
`.env` **是可选的**——不创建也能直接启动，此时所有变量走内置默认值（三个端口由 Docker 随机映射主机端口，项目名取目录名）。需要固定端口或调整 LLM 设置时，再复制模板修改：
```bash
cp .env.example .env
```
主要变量（`.env.example` 内有逐行注释）：

| 变量 | 未设置时的默认行为 | 说明 |
|------|------------------|------|
| `COMPOSE_PROJECT_NAME` | 空（取目录名） | 所有容器 / 网络 / 卷名前缀；多实例并行时各设不同值 |
| `RAGCLAW_HTTP_PORT` | 随机映射 | nginx HTTP 入口的主机端口 |
| `RAGCLAW_HTTPS_PORT` | 随机映射 | HTTPS 主机端口（仅在 Settings 启用 TLS 后生效） |
| `RAGCLAW_FRONTEND_PORT` | 随机映射 | dev 模式 Vite HMR 前端的主机端口 |
| `RAGCLAW_INTERNAL_NET` | `172.30.0` | 沙盒子网；每套并行实例必须不同 |

### 3. 密钥（自动生成）
首次启动时 `bin/sh/start.sh` / 菜单会在缺失时自动生成 `secrets/ragclaw_config_key`（解密 `config.enc` 的 AES-256 密钥）。也可手动创建：
```bash
openssl rand -hex 32 > secrets/ragclaw_config_key
chmod 600 secrets/ragclaw_config_key
```
> ⚠️ 请备份此文件。一旦丢失，`config.enc` 中加密的 API 密钥将无法恢复。

### 4. 构建并启动（生产）
项目自带 **控制菜单** 自动完成「构建镜像 → 启动容器」，按数字选择即可，也可直接执行：
```bash
# macOS / Linux
bash bin/sh/start.sh start        # 或：bash bin/sh/menu.sh（交互式）
# Windows（CMD）
bin\psl\start.bat
```
> 注意：中文菜单（`menu_zh.sh` / `menu_zh.bat`）默认配置了非官方镜像源（Docker / APT / pip）以加速拉取，请先确认其与您的网络环境和安全策略相符。

首次运行会构建全部镜像（`ragclaw`、`mcp-repl`、`ragclaw-egress`、`nginx`）；之后的 `reload` 直接复用镜像、不再重建。

### 5. 访问与首次初始化
- 启动脚本会打印实际访问地址（未固定端口时由 Docker 随机映射），Swagger 文档位于 **/docs**。
- 打开应用，完成**超级管理员初始设置**，随后在 **设置 → LLM** 中填写 / 粘贴 API Key（加密存入 `config.enc`）。
- 可选：在设置中启用 **HTTPS** 并上传证书，nginx 会在变更后热重载。

---

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

欢迎贡献。完整指南见 [贡献指南.md](docs/贡献指南.md)。要点：

- **较大改动先开 issue** 讨论（新功能、架构、破坏性变更），再向 `main` 提交 PR。
- **提交信息使用英文**，`type(scope): subject`，例如 `fix(chat): handle zero-quota on continue`，类型表见指南。
- **PR 小而聚焦**；切勿提交 `.env`、密钥或与本 PR 无关的文件。
- **代码规范**：注释一律英文；保持 `bin/psl`（Windows）与 `bin/sh`（macOS/Linux）脚本行为一致。
- **测试仅在容器模式下运行**：`bash bin/sh/run_all_tests.sh`，请特别关注 `security/`（鉴权、RBAC、隔离、注入、IDOR）。
- **安全敏感区域**（沙盒、出站策略、API Key 注入、skill 适配器、用户隔离）必须在 PR 中说明安全影响。

贡献即表示你同意以 **Apache License 2.0** 授权你的工作。

## 📬 联系与支持

Bug 反馈与功能建议，请 [提交 issue](https://github.com/adam-andel/ragclaw-lite/issues)

---

## 📄 许可证

Apache License 2.0 — 详情见 [LICENSE](LICENSE) 文件。

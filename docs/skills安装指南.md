# ragclaw 系统 Skills 安装指南

> 适用版本：ragclaw dev 栈（`ragclaw-lite` + `ragclaw-mcp-repl` + `ragclaw-egress`）
> 本文档面向：需要在 ragclaw 中安装、配置、排查第三方 Skill 的用户。

---

## 0. 文档说明与术语

### 0.1 本文档覆盖什么

- Skill 包长什么样、安装前如何判断它属于哪一类；
- 四种安装/更新/删除操作；
- 安装后可能需要的外网放行、API KEY、适配器配置；
- 验证方法、常见故障对照。

### 0.2 核心术语

| 术语 | 含义 |
|------|------|
| **Skill 包** | 一个目录：`SKILL.md`（必含）+ 可选 `scripts/`、可选 `.ragclaw/` 等 |
| **store/** | Skill 的唯一真源目录（共享卷 `/ragclaw_skills/store`），backend 写入、沙箱只读 |
| **enable/** | 启用软链集合（`enable/<skill> -> ../store/<skill>`），决定 skill 是否启用 |
| **.ragclaw/** | ragclaw 适配器目录（可选）。装在里面的是 ragclaw 自有的适配代码，**不是**第三方源码 |
| **adapter.json** | 适配器清单：声明 CLI 模块、endpoint 属性、KEY 环境变量、上游 API 地址 |
| **shim.py** | 适配器核心：import 第三方 CLI、把它的 endpoint 改指向注入代理、剥离 KEY 环境变量 |
| **注入代理** | ragclaw-egress 容器内 `:9090`，进程内存中托管 API KEY，出站前注入 `Authorization` |
| **egress 网络策略** | 沙箱出站白名单（allowlist），由「设置 → 网络策略」配置，保存即热加载 |
| **upstream** | CLI 要访问的真实 API 地址（如 `https://api.anysearch.com`） |

---

## 1. Skill 包长什么样

### 1.1 典型目录结构（以出厂预装的 anysearch 为例）

```
anysearch/
├── SKILL.md                 # 必含：能力描述 + frontmatter
├── README.md / LICENSE / SECURITY.md ...   # 附属文件，可选，不影响安装
├── .env.example             # 第三方自己的 KEY 示例，仅供参考
├── runtime.conf.example     # 第三方运行时模板（anysearch 特有，见 4.4）
├── scripts/                 # 可选：第三方 CLI 脚本（多平台）
│   ├── anysearch_cli.py
│   ├── anysearch_cli.sh
│   └── anysearch_cli.js
└── .ragclaw/                # 可选：ragclaw 适配器（目录名固定）
    ├── adapter.json         # 适配清单
    ├── shim.py              # endpoint 重定向 + KEY 剥离
    ├── SKILL.ragclaw.md     # 注入给 LLM 的附加说明（不动第三方 SKILL.md）
    └── init.sh              # 可选初始化钩子（enable/更新时执行一次）
```

### 1.2 SKILL.md（必含）

安装时系统会强制校验：**没有 SKILL.md 的包无法上传**（文件夹上传与 zip 上传均校验）。

`SKILL.md` 开头的 frontmatter 是判断 skill 能力的关键：

```yaml
---
name: anysearch
description: Real-time search engine supporting web search, vertical domain search...
version: 3.0.1
credentials:
  - name: ANYSEARCH_API_KEY
    required: false
    description: "API key for higher rate limits. Anonymous access available..."
    storage: ".env file, environment variable, or --api_key CLI flag"
---
```

- `name` / `description`：注册进决策上下文，是 LLM 识别并选用该 skill 的依据；
- `credentials`：声明该 skill 是否使用 API KEY、是否必填。**这是判断「要不要配 KEY」的第一手依据。**

### 1.3 `.ragclaw/` 适配器（可选，但「有脚本 + 需外网 + 有 KEY」的 skill 必须）

| 文件 | 作用 |
|------|------|
| `adapter.json` | 见下方字段说明。存在即声明「本 skill 走注入代理」 |
| `shim.py` | 运行时把 CLI 的 endpoint 属性改写为注入代理地址，并把 KEY 环境变量从进程里剥掉 |
| `SKILL.ragclaw.md` | ragclaw 注入给 LLM 的补充说明（如「用哪条命令、输出格式要求」），第三方 SKILL.md 一字不改 |
| `init.sh` | 可选初始化钩子，enable 或更新时由 backend 执行一次（见 4.4） |

`adapter.json` 字段（以 anysearch 为例）：

```json
{
  "cli_module": "anysearch_cli",      // 要 import 的第三方 CLI 模块名
  "endpoint_attr": "ENDPOINT",        // 要改写的 endpoint 模块属性
  "endpoint_env": null,               // 是否用环境变量覆盖 endpoint（null=不用）
  "key_env": "ANYSEARCH_API_KEY",     // 第三方 CLI 读取的 KEY 环境变量名
  "proxy_path": "anysearch",          // 注入代理上的路由前缀（全局唯一）
  "endpoint_suffix": "/mcp",          // 上游真实子路径（转发时拼接）
  "header_format": "Bearer {}",       // Authorization 头格式
  "upstream_base": "https://api.anysearch.com"   // 真实上游 API 地址
}
```

> 有 `adapter.json` 的 skill：LLM 在沙箱里跑的是 `.ragclaw/shim.py`（而不是裸 CLI），请求被截到注入代理 `:9090`，由代理补上托管在内存里的 KEY 再转发上游。沙箱全程接触不到真实 KEY。

### 1.4 `scripts/`（可选）

第三方 CLI 脚本，LLM 通过 `run_shell` / `run_python` 按文档调用。**系统不会去解析脚本源码**——用法一律以 `SKILL.md`（及 `.ragclaw` 说明）为准。

---

## 2. 安装前评估：这个 Skill 属于哪一类

安装前先回答三个问题：

1. **有没有 `scripts/`？** —— 决定 skill 是「脚本驱动」还是「纯知识驱动」；
2. **需不需要访问外网？** —— 看 `SKILL.md` 的 Overview/Trigger 是否提到 API、在线数据、搜索等；
3. **需不需要 API KEY？** —— 看 frontmatter 的 `credentials`（`required: true/false`），以及是否配了真实 KEY。

组合起来共五类，每类的安装后动作不同：

| 分类 | 是否含脚本 | 是否需外网 | 是否需 KEY | 安装后动作 |
|------|:---:|:---:|:---:|------|
| **① 无脚本 · 纯本地** | 否 | 否 | 否 | **装完即用，零配置** |
| **② 无脚本 · 需外网** | 否 | 是 | — | **需 egress 网络策略放行**（见 4.1） |
| **③ 有脚本 · 纯本地** | 是 | 否 | 否 | 装完即用，零配置；**可选**编写 `.ragclaw` 适配器提升定位效率 |
| **④ 有脚本 · 需外网 · 无 KEY** | 是 | 是 | 否 | 两种路径：egress 放行直连，**或**编写 `.ragclaw` 适配器走注入代理（推荐，便于日后配 KEY） |
| **⑤ 有脚本 · 需外网 · 有 KEY** | 是 | 是 | 是 | **必须**编写 `.ragclaw` 适配器走注入代理，并在设置页配置 KEY |

### 各类说明

**① 无脚本 · 纯本地**（如：本地代码分析、格式化、文档生成类）
- 只有 `SKILL.md`，无脚本、无网络请求。LLM 直接按 `SKILL.md` 的知识执行。
- 上传后启用即可用，无任何额外配置。

**② 无脚本 · 需外网**
- 没有自带脚本，但 `SKILL.md` 引导 LLM 去访问外部站点/API（例如「查询 xx 官网」）。
- 沙箱默认禁止直连外网，必须先在网络策略里把目标域名加入 allowlist。

**③ 有脚本 · 纯本地**
- 脚本只做本地处理（文件、计算、转换），不出网。
- 装完即用。**可选**：编写 `.ragclaw` 适配器（尤其 `SKILL.ragclaw.md` 写明「用什么命令、传什么参数」），让 LLM 第一次就能定位到正确调用方式，而不是反复试错。

**④ 有脚本 · 需外网 · 无 KEY**（如：anysearch 匿名模式）
- 两种路径任选：
  - **路径 A（egress 放行）**：网络策略里放行脚本要访问的域名，LLM 直接 `run_shell` 跑脚本直连；
  - **路径 B（.ragclaw 适配器，推荐）**：编写适配器走注入代理，代理匿名转发。好处是 LLM 通过 `SKILL.ragclaw.md`/`runtime.conf` 一步定位命令，且日后配了 KEY 无需改任何脚本。

**⑤ 有脚本 · 需外网 · 有 KEY**（如：私有 API 的搜索/查询类）
- **没有替代方案，必须走注入代理**：ragclaw 的设计是真实 KEY 只存在两处——加密存储 `config.enc` 和注入代理进程内存；沙箱永远不持有真实 KEY。因此脚本直连外网无法携带 KEY，唯一路径是 `.ragclaw` 适配器 → 注入代理 → 代理注入 KEY 后转发上游。
- 安装后还要在设置页为该 skill 配置 KEY（见 4.2）。

---

## 3. 安装步骤

### 3.1 准备 Skill 包

- 目录必须含 `SKILL.md`（根目录，frontmatter 完整）；
- 目录名建议与 `SKILL.md` 的 `name` 一致（小写字母/数字/连字符，避免中文与空格）；
- 所有第三方文件（`scripts/`、`README` 等）原样保留，不要改动；
- 若要走注入代理，补齐 `.ragclaw/`（adapter.json + shim.py + SKILL.ragclaw.md，可参考出厂 anysearch 包）。

### 3.2 方式一：文件夹上传

1. 打开左侧 **Skills（技能）** 管理页；
2. 点击「上传文件夹」，选择 skill 目录下的**所有文件**（保留相对路径）；
3. 提交后系统自动完成：写入 `store/` → 创建 enable 软链 → 执行 `init.sh`（若有）→ 若有 `adapter.json` 则自动注册注入代理 upstream。

### 3.3 方式二：zip 上传

1. 将 skill 目录打成 zip，**顶层必须且只能有一个目录**（该目录名即 skill 名），否则上传被拒绝；
2. 在 Skills 管理页选择「上传 zip」；
3. 后续处理与文件夹上传一致。

### 3.4 更新覆盖（重传）

- 对已存在的 skill 重传（reupload 文件夹 / zip），会**整体替换**该 skill 目录并清空脚本缓存；
- 重传后同样会重新注册注入代理 upstream；
- 建议先停用再重传，避免更新期间的并发使用。

### 3.5 启用 / 停用

- Skills 管理页的开关即「启用/停用」：启用 = 创建 `enable/<skill>` 软链，停用 = 移除软链；
- 只有**启用**的 skill 才会出现在对话的技能选择列表中；
- 停用不影响 `store/` 里的文件，随时可再启用。

### 3.6 删除

- 删除会同时移除：`store/` 目录、enable 软链、注入代理中的 upstream 映射与 KEY、DB 记录；
- 出厂预装 skill 被删除后，**下次 backend 重启会重新 seed 回来**（见 3.7）；第三方 skill 删除即消失。

### 3.7 出厂预装（seeds）

- 镜像内预置了一批出厂 skill（`backend/seeds/skills/`，当前为 anysearch）；
- 首次启动（共享卷为空）时自动复制到 `store/` 并启用；
- **幂等**：`store/` 里已存在的 skill 不会被 seed 覆盖——你对它的任何修改/删除在重启后保留；只有共享卷被清空时才重新 seed。

---

## 4. 安装后配置

### 4.1 外网放行（egress 网络策略）

适用：第 **② ④** 类（无适配器、脚本需直连外网）。

1. 打开 **设置 → 网络策略**；
2. 模式选 **allowlist（白名单）**，在「允许的域名」里加入脚本要访问的域名（如 `api.xxx.com`）；
3. 保存即生效——策略会热加载到沙箱（`PUT /policy`），**无需重启任何容器**。

> 三种模式：`deny`（全禁，默认）、`allow`（全放行，不推荐）、`allowlist`（仅白名单域名）。走注入代理的 skill（有 adapter.json）不受此策略约束——它只访问注册过的 upstream，见 4.3。

### 4.2 配置 API KEY

适用：第 **⑤** 类（以及第 ④ 类日后配了 KEY）。

- **设置页是配置 API KEY 的唯一入口**（不再从 `.env` 读取）；
- 在设置页找到该 skill 的 KEY 输入框，填入后保存；
- 保存时 backend 会把 `{folder, proxy_path, upstream_base, header_format, api_key}` 推送给注入代理（`PUT /secret`），KEY 只存在于 `config.enc` 与代理内存中；
- 清空 KEY 会同步清除代理内存中的 KEY（恢复匿名模式）。

### 4.3 注入代理 upstream 的自动注册

任何**带 `adapter.json`** 的 skill，其 `proxy_path → upstream_base` 映射会在以下时机自动推送到注入代理（`PUT /secret-config`，无需 KEY）：

- 上传 / 重传成功之后；
- 配置或清空 API KEY 之后；
- backend 每次启动时（全量扫描 `store/` 兜底注册）。

所以正常情况下无需手动干预；若发现「unregistered skill proxy path」错误，通常是上述时机之外的异常（见 5.3 故障表）。

### 4.4 初始化钩子 `init.sh` 与 `runtime.conf`

- `.ragclaw/init.sh` 是**通用可选钩子**：任何 skill 只要带它，就会在「启用」或「重传」时由 backend 执行一次（幂等，失败不影响安装）。它用于做 skill 专属的初始化，如探测 CLI 平台、生成运行时配置。
- **注意：`runtime.conf` 是出厂 anysearch 适配器用 `init.sh` 生成的私有产物**（由 `runtime.conf.example` 填充检测到的运行时与命令），**不是所有 skill 都需要，也不是通用要求**。请勿据此推断「每个 skill 都要有 runtime.conf」。
- 若你的 skill 没有 `init.sh`，跳过即可，不影响安装。

---

## 5. 验证与常见故障

### 5.1 功能验证清单

- [ ] Skills 管理页中该 skill 状态为「已启用」；
- [ ] 对话的技能选择列表中能选到它；
- [ ] 新开对话（避免旧上下文干扰），选中该 skill 发起一次典型任务；
- [ ] 期望结果：**第一轮工具调用即成功**并给出答案；
- [ ] 有 KEY 的 skill：确认请求确实经过注入代理（见日志验证）。

### 5.2 日志验证

| 容器 | 看什么 | 命令 |
|------|--------|------|
| backend | 上传/注册/推 KEY 记录 | `docker logs ragclaw-lite` |
| egress | upstream 注册、转发是否发生 | `docker logs ragclaw-egress` |
| mcp-repl | 沙箱内 run_shell 实际执行的命令 | `docker logs ragclaw-mcp-repl` |

带适配器的 skill 安装/配置后，`docker logs ragclaw-egress` 应能看到类似
`registered upstream config folder=anysearch` 的记录。

### 5.3 常见故障对照

| 现象 | 含义 | 处理 |
|------|------|------|
| `403 Egress blocked: host not in allowlist` | 沙箱脚本直连的域名不在白名单 | 4.1 网络策略放行该域名 |
| `403 forbidden: unregistered skill proxy path` | 请求到了注入代理，但该 `proxy_path` 未注册 | 确认 adapter.json 存在且已重传一次（触发注册）；backend 重启后仍未恢复则检查 egress 侧 |
| `404`（上游路径错误） | adapter.json 的 `endpoint_suffix`/`upstream_base` 与真实 API 不符 | 核对第三方文档修正适配器后重传 |
| 多轮工具调用后才出答案 | 第一轮工具失败、LLM 尝试兜底 | 看第一轮的报错，通常落入上面三类；修复后应一轮成功 |
| 工具一直失败/超时 | 网络不通或 KEY 无效 | 检查 allowlist、设置页 KEY 是否正确配置 |

---

## 6. 注意事项与边界

1. **第三方 skill 源码不改**：所有适配（endpoint 重定向、KEY 处理、命令指引）都发生在 `.ragclaw/` 适配器内，第三方 `SKILL.md` / `scripts/` 保持原样。
2. **改egress代码后须重建镜像**：本系统在开发模式下 backend / mcp-repl 是 bind-mount（热重载），但 **egress 是镜像烘焙**（`COPY` 进镜像），改动注入代理/egress 相关源码后**必须重建 `ragclaw-egress` 镜像**才会生效。
3. **数据与密钥位置**：skill 文件在共享卷（`/ragclaw_skills`），API KEY 在 `config.enc`（加密）。删除容器时共享卷数据保留；清卷则出厂 skill 会重新 seed。
4. **主流程不写死 skill**：系统不会为特定 skill 做特判；每个 skill 的能力与用法都以它自己的 `SKILL.md`（+ `.ragclaw` 说明）为准。

---

## 7. FAQ

**Q：上传后马上能用吗？要不要重启容器？**
A：一般不用重启。上传即完成写盘、软链、init、upstream 注册；新开对话即可用。改源码才需要重启/重建。

**Q：为什么我的 skill 列表里看不到刚上传的 skill？**
A：检查是否已「启用」（enable 软链）；确认 `SKILL.md` 在包根目录且 frontmatter 的 `name` 正常。

**Q：API KEY 到底放哪？**
A：只放设置页（唯一入口）。KEY 加密存于 `config.enc`，运行时只存在于注入代理进程内存，沙箱内不可见。

**Q：怎么确认我的请求走了注入代理而不是直连？**
A：有 `adapter.json` 的 skill 必然走注入代理；看 `docker logs ragclaw-egress` 是否有该 folder 的转发记录。

**Q：无 KEY 的 skill（如 anysearch）为什么要写适配器？**
A：适配器让 LLM 一步定位到正确命令（`SKILL.ragclaw.md`/`runtime.conf`），并统一走注入代理；日后配置 KEY 时无需改任何脚本。

**Q：删除出厂 skill 会怎样？**
A：删除后共享卷清空该 skill；下次 backend 重启会从 `seeds/` 重新 seed 回来。

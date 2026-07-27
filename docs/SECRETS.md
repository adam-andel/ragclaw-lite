# 密钥与运维契约（Secrets & Operations Contract）

本文档是 RAGClaw-Lite 的**权威运维契约**。凡涉及密钥生成、备份、恢复、轮换的操作，均以本文为准。
`.env.example` 只描述环境变量，不承载任何密钥契约——如果你在找「ragclaw_config_key 该怎么备份 / 能不能换」之类的问题，答案都在这里。

---

## 1. 概览：系统里有哪些密钥、存在哪

| 密钥 | 存储位置 | 是否入 VCS | 管理方式 |
| --- | --- | --- | --- |
| `ragclaw_config_key`（KEK） | 宿主文件 `secrets/ragclaw_config_key`，挂载进容器 `/run/secrets/ragclaw_config_key`（只读） | **否**（已 gitignore） | 由 `bin/sh/lib/gen-secrets.sh` 生成；可手动替换文件 |
| LLM API Key | 加密写入 `config.enc`（`/app/data/config.enc`，即 `ragclaw_data` 卷内） | **否**（卷内文件） | **仅通过 Settings 设置页 UI 填写** |
| Embedding API Key | 同上（与 LLM key 同文件） | **否** | **仅通过 Settings 设置页 UI 填写** |
| `repl_auth_secret`（REPL 身份 HMAC） | DB（`SystemSetting` 表） | 否（卷内 sqlite） | 首启自动生成；UI「重新生成」旋转 |
| `jwt_secret`（JWT HS256 签名） | DB（`SystemSetting` 表） | 否（卷内 sqlite） | 首启自动生成；UI「重新生成」旋转 |
| HTTPS 证书 / 私钥 | 耐久 TLS 卷（明文，nginx 需明文读取） | 否（卷） | Settings 上传；backend 写入卷 |

**关键结论**：API Key（LLM / Embedding）**没有任何 `.env` 或挂载 secret 的默认来源**。它们要么来自 Settings UI 写入的 `config.enc`，要么就是空的。首次启动（无 `config.enc`）时 key 为空，必须由管理员在 UI 填写后才能使用 LLM。

---

## 2. ragclaw_config_key（唯一挂载的 Docker secret）

- **是什么**：AES-256-GCM 加密 `config.enc` 的密钥（KEK，Key Encryption Key）。
- **生成**：由 `bin/sh/lib/gen-secrets.sh` 在首次 `bin/sh/start.sh` 时自动生成到 `secrets/ragclaw_config_key`（600 权限，已 gitignore）。
- **挂载**：docker-compose 以只读 bind 挂载到容器内 `/run/secrets/ragclaw_config_key`。
- **稳定性**：它是**宿主文件**，不属于容器可写层，因此 `docker compose up` 重建 backend 容器后它**不变**——这正是存储的 API Key 能跨重建解密的前提。（早期基于容器 MAC 地址派生的方案每次重建都会变，导致静默丢 key，已废弃。）
- **格式硬约束**：必须是 **64 字符 hex（32 字节）**。非 hex 值会在启动时直接 `RuntimeError` 拒绝，**不再有静默派生兜底**。

---

## 3. API Key（LLM / Embedding）

- **来源唯一**：设置页 UI。系统不再读取 `.env` 的 `LLM_API_KEY`，也不从任何挂载 secret 读取。
- **存储**：UI 填写后经 AES-256-GCM 加密写入 `config.enc`，位于 `ragclaw_data` 卷（`/app/data/config.enc`），容器重建后保留。
- **config.enc v1 格式（字节布局）**：
  ```
  MAGIC(5) | version(1) | key_fingerprint(32) | nonce(12) | ciphertext
  MAGIC           = b"RAGC1"
  version         = 1 (uint8)
  key_fingerprint = sha256(KEK)[:32]
  nonce + ct      = AES-256-GCM 输出
  ```
  `key_fingerprint` 把 KEK 以指纹形式嵌进文件，使得「用错密钥」能在解密**前**被检测出来。
- **前端能力边界**：前端没有「手动填 secret 文件」的入口；设置页只有填写 key 的输入框，以及 `repl_auth_secret` / `jwt_secret` 的「重新生成」按钮。

---

## 4. 其他密钥（不入 config.enc）

- **`repl_auth_secret`**：DB 化。首启自动生成并持久化；UI 旋转即权威，重启后存活；不再来自挂载文件（方案 B）。
- **`jwt_secret`**：DB 化（与 repl_auth 同机制）。auth 层实时读 `config_manager.jwt_secret` 缓存，旋转即时生效，无需重启。
- **HTTPS 证书 / 私钥**：明文存于耐久 TLS 卷（nginx 必须明文读取），由 backend 写入；**不入 config.enc**。

---

## 5. 运维契约（必读）

1. **必须单独备份两样东西**：
   - `secrets/` 目录（含 `ragclaw_config_key`）——KEK。
   - `ragclaw_data` 卷（含 `config.enc` + sqlite）——加密的 key 与全部配置。
   二者缺一不可：**丢了 KEK → `config.enc` 无法解密（API Key 永久丢失）；丢了卷 → 配置与 key 全失。**

2. **绝不要在未重填 API Key 之前更换 `ragclaw_config_key`**：
   KEK 以指纹形式嵌入 `config.enc`；一旦不匹配，backend 启动时打印 **FATAL「config.enc unreadable」** 并以**空 key** 继续运行（**不**静默回退到 `.env` / 默认值），直到你在 UI 重填。此期间 LLM 调用会失败，但**启动不会被阻塞**。

3. `ragclaw_config_key` **必须是 64-char hex**；非 hex 启动即拒。

4. **生产环境**：用强随机值，且绝对不要入 VCS（`secrets/` 已在 `.gitignore`）。

5. **k8s**：把它映射为原生 `Secret`，挂载到同一 `/run/secrets/ragclaw_config_key` 路径即可，**无需改应用代码**。

---

## 6. 备份与恢复（Backup & Recovery）

- **备份示例**（停服或在线均可，卷是文件级的）：
  ```bash
  # 备份 KEK
  cp -a secrets/ /path/to/backup/secrets-$(date +%F)/
  # 备份数据卷（含 config.enc + sqlite）
  docker run --rm -v ragclaw_data:/data -v /path/to/backup:/backup alpine \
    tar czf /backup/ragclaw_data-$(date +%F).tar.gz -C /data .
  ```
- **恢复**：把 KEK 文件放回 `secrets/ragclaw_config_key`、把卷恢复回去，重启即可。**前提是 KEK 与 `config.enc` 配对**（即备份时二者对应）。
- **丢失 KEK 但卷仍在**：`config.enc` 不可解密 → 启动 FATAL + 空 key。恢复方式只有：**用 UI 重新填写 API Key**（会用当前 KEK 重新加密）。原 key 值若未另存则无法找回。

---

## 7. 密钥轮换流程（Rotation）

### 7.1 轮换 ragclaw_config_key（顺序很重要）
1. **先**在 Settings UI 重填 LLM / Embedding API Key（用**旧** KEK 重新加密，写入新的 `config.enc`）。
2. **再**替换挂载的 `ragclaw_config_key` 文件（生成新 hex，更新 `secrets/` 与挂载源）。
3. 重启 backend。此时新 KEK 与「已用新 KEK 重填过的」`config.enc` 配对，FATAL 不再出现。
- **顺序做反**（先换 KEK 后重填）：启动期会出现 FATAL + 空 key，需登录 UI 补填——系统仍能启动，只是 LLM 暂时不可用。

### 7.2 轮换 repl_auth_secret / jwt_secret
直接在 Settings UI 点「重新生成」，**即时生效，无需重启**（auth 层实时读 `config_manager` 缓存，旋转同步失效缓存）。

---

## 8. 迁移说明（Migration）

- **旧版（v1 之前、无 `RAGC1` MAGIC）的 `config.enc` 一律拒绝解密**。管理员须在 UI 重新填写，系统**不做透明迁移**。
- **已删除 `.env` LLM_API_KEY 默认来源**：首次启动不再读取 `.env`，API Key 只能经 UI 设置。任何历史 `.env` 里的 `LLM_API_KEY` 现在都被忽略。

---

## 9. 安全边界（不要做的事 / Do NOT）

- 不要把 `LLM_API_KEY` 写进 `.env`（支持已移除，写了也无效）。
- 不要把 `secrets/` 提交进 git。
- 不要手动编辑 `/run/secrets/ragclaw_config_key` 或 `config.enc`。
- 不要依赖「静默默认」——任何解密失败都会**大声告警并空 key 运行**，迫使你登 UI 处理，而不是悄悄用错 key。

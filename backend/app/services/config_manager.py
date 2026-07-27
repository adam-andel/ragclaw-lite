"""Runtime configuration manager — DB for non-sensitive settings + AES-256-GCM encrypted file for API keys.

Admin manages ALL runtime settings through the web UI. API keys (LLM / embedding) have NO .env or
mounted-secret default source — they are entered exclusively via the Settings page and encrypted
into config.enc. On first boot (no config.enc) the keys are simply empty until the admin fills
them in through the UI.
"""

import hashlib
import json
import os
import secrets
import sys
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    Encoding,
    PublicFormat,
)

from app.config import settings

DEFAULT_SYSTEM_PROMPT = """你就是 ragclaw —— 一个以「claw（爪）」为核心、以「rag（检索增强生成）」为辅助的智能体。

## 你的身份
- **claw 是主要角色**：你天生拥有原生的文件管理能力与脚本执行能力，可以像在终端里一样直接操作工作区、运行代码、处理数据、生成文件。你不是单纯的问答机器人，也不局限于某一种技能。
- **rag 是附属品**：当问题涉及知识库 / 文档内容时，系统会把相关的「参考文档」注入到对话上下文中供你引用。rag 只是增强你回答的附属能力，而非你的全部。

## 原生能力一：文件管理（claw）
你可以直接对**工作区**内的文件进行增删改查（txt / csv / xlsx / pptx / png / pdf / html / markdown 等）。
- **核心原则**：你的职责是真正去操作文件，而不是把代码展示给用户。任何涉及文件操作的请求，都必须调用 `run_python` 工具来完成（绝不要只 print 代码就停下）。
- 如果上一步工具结果已经完整满足请求，则不要重复调用。

### 文件操作方式（通过 run_python）
- 创建：用 `open(path, "w", encoding="utf-8")` 写入。
- 读取：用 `open(path, "r", encoding="utf-8").read()` 或 `pathlib.Path(path).read_text()` 读取并 `print` 内容。
- 更新：先读取，再修改（替换 / 插入 / 追加）后写回；优先局部修改而非整体覆写。
- 删除：用 `pathlib.Path(path).unlink()` / `os.remove(path)`；目录用 `shutil.rmtree(path)`，且仅当用户明确要求时。

### 文件操作安全约束
- 只在**工作区目录**内操作（run_python 的当前工作目录）。不使用绝对路径，不通过 `..` 逃逸工作区。
- 删除前：明确告知将删除哪些文件。绝不删除整个工作区、无关文件或任务范围外的文件。
- 更新已有文件前：先读取，避免破坏数据。
- 不对自己未创建的文件执行破坏性操作，除非用户明确要求。

### 文件操作示例
- 用户：「生成内容为 1 的 txt」→ run_python：`with open("output.txt","w",encoding="utf-8") as f: f.write("1"); print("file created")`
- 用户：「读取 output.txt」→ run_python：`print(open("output.txt",encoding="utf-8").read())`
- 用户：「追加一行到 output.txt」→ run_python 读取后拼接再写回。
- 用户：「删除 old.txt」→ run_python：`import os; os.remove("old.txt"); print("deleted old.txt")`

### 文件操作提示
- 使用英文文件名（output.txt、report.docx、chart.png）。
- 已安装库：pandas、python-docx、python-pptx、PyPDF2。
- 无网络访问，不可调用外部进程。
- 生成的文件 60 分钟后过期自动删除；`print()` 的输出会作为工具结果返回给你。
- 避免超大文件或长时间运行（有超时风险）；保存到当前目录，工具会自动分配工作区子目录。

## 原生能力二：脚本执行（claw）
你可以用 `run_python` 运行任意 Python 脚本，进行计算、数据处理、自动化、绘图等。把代码写在 `code` 参数里传给 `run_python` 即可。

## 何时使用 rag（检索增强）
- 当用户明确指向知识库文档、要求基于资料回答、或需要引用来源时，使用对话上下文中提供的「参考文档」。引用格式：`[来源: 文档名 章节名]`。
- 如果「参考文档」中没有相关信息，诚实说明「文档中未找到相关信息」。
- 只依据提供的文档内容回答，不编造；保留原文中的专有名词与引用来源。

## 通用规则
1. 回答简洁、准确，并使用与用户提问相同的语言
2. 文档中的代码或表格保留原始格式
3. 专有名词（产品名、技术术语、API 名称等）与引用来源保留原文，不在翻译中改动；若原文为英文，即使回答使用其他语言也保留英文原文
4. 永远不要编造文件路径或 UUID"""

# English counterpart — used when prompt_language == "en" (A/B instruction-following test).
# Response language is left to follow the user's question language (not forced to English),
# so end-user behavior is consistent across both prompt variants.
DEFAULT_SYSTEM_PROMPT_EN = """You are ragclaw — an agent whose core is "claw" (the operative part) with "rag" (Retrieval-Augmented Generation) as a subordinate capability.

## Your identity
- **claw is the main character**: you natively possess file-management and script-execution abilities. Like working in a terminal, you can directly manipulate the workspace, run code, process data, and generate files. You are not a mere Q&A bot, nor are you limited to a single skill.
- **rag is subordinate**: when a question involves knowledge-base / document content, the system injects the relevant "reference documents" into the conversation context for you to cite. rag is only a supporting capability that augments your answers, not your whole purpose.

## Native capability 1: File management (claw)
You can directly create, read, update, and delete files in the **workspace** (txt / csv / xlsx / pptx / png / pdf / html / markdown and more).
- **Core rule**: your job is to actually operate on files, not to show code to the user. For any request that requires a file operation, you MUST call the `run_python` tool (never just print code and stop).
- If the previous tool result has already fully satisfied the request, do not call again.

### File operations (via run_python)
- Create: `open(path, "w", encoding="utf-8")`.
- Read: `open(path, "r", encoding="utf-8").read()` or `pathlib.Path(path).read_text()`, then `print` it.
- Update: read first, then modify (replace / insert / append) and write back. Prefer targeted edits over full overwrite.
- Delete: `pathlib.Path(path).unlink()` / `os.remove(path)`. Use `shutil.rmtree(path)` only for directories and only when explicitly asked.

### Safety constraints
- Operate ONLY inside the workspace directory (run_python's cwd). No absolute paths, no `..` traversal escaping it.
- Before any delete: state clearly which file(s) will be removed. Never delete the whole workspace, unrelated files, or files outside the task.
- Before updating an existing file: read it first so you don't destroy data.
- Do not run destructive ops on files you didn't create unless explicitly asked.

### Examples
- User: "generate a txt with content: 1" -> run_python: `with open("output.txt","w",encoding="utf-8") as f: f.write("1"); print("file created")`
- User: "read output.txt" -> run_python: `print(open("output.txt",encoding="utf-8").read())`
- User: "append a line to output.txt" -> run_python: read, concatenate, write back.
- User: "delete old.txt" -> run_python: `import os; os.remove("old.txt"); print("deleted old.txt")`

### Gotchas
- Use English filenames (output.txt, report.docx, chart.png).
- Installed libs: pandas, python-docx, python-pptx, PyPDF2.
- No network; no external process calls.
- Generated/changed files expire after 60 min and are auto-deleted.
- `print()` output is returned to you as the tool result.
- Avoid very large files or long-running ops (timeout risk); save to the current directory.

## Native capability 2: Script execution (claw)
You can run arbitrary Python via `run_python` for computation, data processing, automation, and plotting. Pass the code in the `code` argument.

## When to use rag (retrieval augmentation)
- When the user points at knowledge-base documents, asks for answers based on source material, or needs citations, use the "reference documents" provided in the conversation context. Cite as: [Source: Document Name - Section Name].
- If the reference documents contain no relevant information, honestly say "No relevant information found in the documents".
- Answer only based on the provided document content; do not fabricate. Keep proper nouns and citations in their original form.

## General rules
1. Keep answers concise and accurate, and respond in the same language as the user's question.
2. Preserve original formatting for code or tables in documents.
3. Keep proper nouns (product names, technical terms, API names) and cited sources in their original form; do not translate them.
4. Never fabricate file paths or UUIDs."""


# ── Encrypted config.enc format (v1) ───────────────────────────────────────
# Layout (bytes):  MAGIC(5) | version(1) | key_fingerprint(32) | nonce(12) | ciphertext
#   MAGIC           = b"RAGC1"
#   version         = 1 (uint8)
#   key_fingerprint = sha256(derived_key)[:32]
#       Lets us detect a KEK mismatch *before* attempting decryption, so a wrong
#       or missing ragclaw_config_key surfaces as a clear fatal warning instead
#       of a swallowed exception + silent fallback to default (empty) keys.
#   nonce + ciphertext = AES-256-GCM output.
# Legacy (pre-v1, no MAGIC) files are rejected — the admin must re-enter keys
# in Settings (no transparent migration).
_CFG_MAGIC = b"RAGC1"
_CFG_VERSION = 1
_CFG_FP_LEN = 32


class ConfigKeyMismatch(RuntimeError):
    """config.enc cannot be decrypted with the mounted ragclaw_config_key."""


def _key_fingerprint(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()[:_CFG_FP_LEN]


def _derive_key() -> bytes:
    """Return the AES-256 key for config.enc.

    The key is read verbatim from the mounted Docker secret
    ``/run/secrets/ragclaw_config_key`` (a 32-byte value, stored as 64 hex
    chars). It is deployment-provided and STABLE across container recreation,
    so encrypted values (LLM / embedding API keys) survive a ``docker compose
    up`` that recreates the backend container.

    There is intentionally NO fallback of any kind (no machine-derived key, no
    non-hex passphrase derivation): the previous MAC-based scheme broke on
    every recreate (Docker assigns a fresh MAC), silently dropping all stored
    API keys. Non-hex input now raises loudly instead of deriving a wrong key.
    """
    path = Path("/run/secrets/ragclaw_config_key")
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except Exception as e:  # file missing / unreadable
        raise RuntimeError(
            "ragclaw_config_key secret not found at /run/secrets/ragclaw_config_key. "
            "Mount the Docker secret (generated by bin/sh/lib/gen-secrets.sh) before "
            "starting the backend."
        ) from e
    if not raw:
        raise RuntimeError("ragclaw_config_key secret is empty")
    try:
        return bytes.fromhex(raw)
    except ValueError:
        raise RuntimeError(
            "ragclaw_config_key must be a 64-character hex string (32 bytes). "
            "Non-hex / passphrase values are no longer accepted — provide the hex "
            "key generated by bin/sh/lib/gen-secrets.sh."
        ) from None


def _encrypt(plaintext: str) -> bytes:
    key = _derive_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    # v1 envelope: MAGIC | version | key_fingerprint | nonce | ciphertext
    return _CFG_MAGIC + bytes([_CFG_VERSION]) + _key_fingerprint(key) + nonce + ct


def _decrypt(data: bytes) -> str:
    """Decrypt config.enc (v1 format).

    Raises ConfigKeyMismatch on any incompatibility (legacy format, wrong key,
    corruption) so the caller can surface a clear fatal warning and fall back
    to EMPTY keys rather than silently using defaults.
    """
    header_len = len(_CFG_MAGIC) + 1 + _CFG_FP_LEN
    if len(data) < header_len + 12:
        raise ConfigKeyMismatch(
            "config.enc is not in the v1 format (legacy or corrupt file). "
            "Re-enter the LLM / embedding API keys in Settings."
        )
    if data[: len(_CFG_MAGIC)] != _CFG_MAGIC:
        raise ConfigKeyMismatch(
            "config.enc has an unrecognized format marker. "
            "Re-enter the LLM / embedding API keys in Settings."
        )
    version = data[len(_CFG_MAGIC)]
    if version != _CFG_VERSION:
        raise ConfigKeyMismatch(
            f"config.enc version {version} is unsupported (expected {_CFG_VERSION}). "
            "Re-enter the LLM / embedding API keys in Settings."
        )
    fp = data[len(_CFG_MAGIC) + 1 : header_len]
    key = _derive_key()
    if _key_fingerprint(key) != fp:
        raise ConfigKeyMismatch(
            "ragclaw_config_key does NOT match config.enc (key fingerprint mismatch). "
            "The encryption key was changed without re-encrypting config.enc. "
            "Re-enter the LLM / embedding API keys in Settings."
        )
    aesgcm = AESGCM(key)
    nonce = data[header_len : header_len + 12]
    ct = data[header_len + 12 :]
    try:
        return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
    except Exception as e:
        raise ConfigKeyMismatch(f"config.enc payload decryption failed: {e}")


def _mask(key: str) -> str:
    """Mask API key for safe display."""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:4] + "****"
    return key[:6] + "****" + key[-4:]


def _generate_repl_auth_secret() -> str:
    """Generate a cryptographically strong REPL_AUTH_SECRET (64 hex chars)."""
    return secrets.token_hex(32)


def _generate_jwt_secret() -> str:
    """Generate a cryptographically strong JWT HS256 signing secret (64 hex chars)."""
    return secrets.token_hex(32)


# ── HTTPS / TLS materialization (nginx reverse proxy, prod only) ──
# The backend writes the certificate, key and a rendered nginx server config
# into this shared volume; the nginx container mounts it read-only and hot-
# reloads on change (no docker.sock needed). In dev (volume not mounted) writes
# are best-effort no-ops.
TLS_DIR = Path("/app/tls")


def _validate_cert_key(cert_pem: str, key_pem: str) -> dict:
    """Validate a PEM certificate (leaf) + private key pair.

    Returns cert metadata (subject + expiry). Raises ValueError on any
    parse/format/mismatch problem so the caller can surface a 400.
    """
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    except Exception as e:
        raise ValueError(f"证书解析失败: {e}")
    try:
        key = load_pem_private_key(key_pem.encode("utf-8"), password=None)
    except TypeError:
        raise ValueError("私钥受密码保护，请提供未加密的 PEM 私钥")
    except Exception as e:
        raise ValueError(f"私钥解析失败: {e}")
    try:
        key_pub = key.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
    except Exception:
        raise ValueError("不支持的私钥格式（需未加密的 PEM 私钥）")
    if (
        cert.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        != key_pub
    ):
        raise ValueError("证书与私钥不匹配")
    subject = cert.subject.rfc4514_string()
    try:
        not_after = cert.not_valid_after_utc
    except Exception:
        not_after = cert.not_valid_after
    return {"subject": subject, "expires": not_after.strftime("%Y-%m-%d")}


def _render_nginx_conf(https_enabled: bool) -> str:
    """Render the nginx server config written to the shared TLS volume.

    HTTPS is OPTIONAL: the feature does not force you to enable it. When it is
    OFF, an HTTP reverse proxy on port 80 serves the site over plain HTTP.

    When HTTPS is ON, HTTP is FORCED to HTTPS: the port-80 server issues a 301
    redirect to https://$host, and a 443 TLS server (with HSTS) is the only one
    that actually serves content. The backend itself is never published to the
    host — all traffic reaches it through nginx.

    The backend is referenced via a runtime-resolved variable (resolver
    127.0.0.11 = Docker's embedded DNS) instead of a static upstream host. nginx
    would otherwise try to resolve "ragclaw" at CONFIG-LOAD time and fail fatally
    ("host not found in upstream") whenever the backend container is not yet
    registered in Docker DNS when nginx starts. The backend depends_on nginx, so
    nginx ALWAYS starts first — hence the runtime resolver is required for a
    reliable startup.
    """
    # Proxy location body. Uses $backend_upstream (defined per server block) so
    # nginx resolves the backend hostname per request instead of at config load.
    location_body = (
        "        proxy_pass http://$backend_upstream;\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Real-IP $remote_addr;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_set_header X-Forwarded-Proto $scheme;\n"
        "        proxy_http_version 1.1;\n"
        "        proxy_buffering off;\n"
        "        proxy_read_timeout 3600s;\n"
    )
    resolver = "    resolver 127.0.0.11 valid=10s ipv6=off;\n"
    set_upstream = "    set $backend_upstream ragclaw:8000;\n"

    https_server = (
        "server {\n"
        "    listen 443 ssl;\n"
        "    server_name _;\n"
        "    ssl_certificate     /etc/nginx/conf.d/fullchain.pem;\n"
        "    ssl_certificate_key /etc/nginx/conf.d/privkey.pem;\n"
        "    ssl_protocols TLSv1.2 TLSv1.3;\n"
        "    add_header Strict-Transport-Security \"max-age=63072000; includeSubDomains\" always;\n"
        + resolver
        + set_upstream
        + "    location / {\n"
        + location_body
        + "    }\n"
        "}\n"
    )
    if not https_enabled:
        # Plain HTTP only — port 80 reverse-proxies to the backend.
        return (
            "server {\n"
            "    listen 80;\n"
            "    server_name _;\n"
            + resolver
            + set_upstream
            + "    location / {\n"
            + location_body
            + "    }\n"
            "}\n"
        )
    # HTTPS enabled — force HTTP -> HTTPS via 301, then serve on 443.
    http_redirect = (
        "server {\n"
        "    listen 80;\n"
        "    server_name _;\n"
        "    return 301 https://$host$request_uri;\n"
        "}\n"
    )
    return http_redirect + "\n" + https_server


class ConfigManager:
    """Thread-safe singleton for runtime configuration."""

    _instance = None
    # RLock (reentrant) — get_config_safe() holds the lock and then reads
    # other (locked) properties; a plain Lock would self-deadlock the same
    # thread and freeze the entire event loop.
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._config: dict = {}
        self._config_file = settings.data_dir / "config.enc"
        self._initialized = True

    # ── Lifecycle ──

    async def init(self):
        """Called once at startup. Loads API keys from encrypted file and non-sensitive settings from DB.

        API keys have NO .env or mounted-secret default source. On first boot (no config.enc)
        they are simply empty and MUST be entered through the Settings page UI. A present
        config.enc is decrypted with the mounted ragclaw_config_key; if that fails the system
        logs a clear FATAL and runs with EMPTY keys (admin re-enters via UI) — it never
        silently falls back to a default.
        """
        legacy_file_existed = False
        with self._lock:
            self._config = self._build_defaults()
            if self._config_file.exists():
                try:
                    saved = json.loads(_decrypt(self._config_file.read_bytes()))
                    stored_key = saved.get("llm_api_key", "")
                    if stored_key:
                        self._config["llm_api_key"] = stored_key
                    stored_emb = saved.get("embedding_api_key", "")
                    if stored_emb:
                        self._config["embedding_api_key"] = stored_emb
                    legacy_file_existed = True
                    print("[ConfigManager] loaded encrypted config")
                except ConfigKeyMismatch as e:
                    # The mounted ragclaw_config_key cannot decrypt config.enc.
                    # Fail LOUD with a clear fatal warning, then continue with
                    # EMPTY keys so the admin is forced to re-enter them in
                    # Settings. We must NOT silently fall back to .env / defaults
                    # and pretend nothing broke.
                    print(
                        f"[ConfigManager][FATAL] config.enc unreadable: {e} "
                        f"Running with EMPTY API keys — re-enter them in Settings.",
                        file=sys.stderr,
                    )
                    self._config["llm_api_key"] = ""
                    self._config["embedding_api_key"] = ""
                except Exception as e:
                    # Unexpected failure (e.g. JSON parse of a valid but
                    # malformed payload). Same loud-and-empty treatment.
                    print(
                        f"[ConfigManager][FATAL] config.enc corrupt/unreadable: {e} "
                        f"Running with EMPTY API keys — re-enter them in Settings.",
                        file=sys.stderr,
                    )
                    self._config["llm_api_key"] = ""
                    self._config["embedding_api_key"] = ""

        await self._load_from_db(legacy_file_existed)
        # HTTPS cert/key are NEVER stored in config.enc (they live as plaintext
        # in the durable TLS volume, see set_https / ensure_tls_config). Only
        # llm/embedding API keys remain encrypted here.
        # REPL identity HMAC secret is auto-generated on first boot (see
        # _ensure_repl_auth_secret) and rotated via the admin UI; it is no
        # longer sourced from a mounted Docker secret file (Plan B).
        await self._ensure_repl_auth_secret()
        # JWT signing secret: DB-backed, single source of truth. Auto-generated
        # on first boot (mirrors repl_auth_secret) and rotated via the admin UI.
        # No mounted Docker secret is used (see auth.get_jwt_secret).
        await self._ensure_jwt_secret()

    def _build_defaults(self) -> dict:
        return {
            # LLM
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_api_key": "",
            "llm_base_url": settings.llm_base_url,
            "llm_temperature": settings.llm_temperature,
            "llm_max_tokens": settings.llm_max_tokens,
            "llm_context_window": 128000,  # max context window (tokens) for the configured model
            "llm_concurrency": 3,
            # Embedding
            "embedding_model": settings.embedding_model,
            "embedding_api_key": "",
            # Server (startup-time only)
            "server_host": "0.0.0.0",
            "server_port": 8000,
            # HTTPS / TLS (nginx reverse proxy, prod only)
            "https_enabled": False,
            # System prompt (zh = original field; en = A/B variant selected by prompt_language)
            "llm_system_prompt": DEFAULT_SYSTEM_PROMPT,
            "llm_system_prompt_en": DEFAULT_SYSTEM_PROMPT_EN,
            # Agent-graph prompt language: "zh" | "en" (default "en") — switch to "zh" for Chinese
            "prompt_language": "en",
            # Cache
            "cache_ttl_seconds": 3600,
            # Sandbox network policy
            "sandbox_network_mode": "deny",
            "sandbox_allow_domains": "",
            "sandbox_allow_methods": "",
            # REPL MCP identity HMAC secret (auto-generated on first boot if absent)
            "repl_auth_secret": "",
            # JWT HS256 signing secret (auto-generated on first boot if absent)
            "jwt_secret": "",
        }

    def _build_non_sensitive_defaults(self) -> dict:
        return {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_base_url": settings.llm_base_url,
            "llm_temperature": settings.llm_temperature,
            "llm_max_tokens": settings.llm_max_tokens,
            "llm_context_window": 128000,  # max context window (tokens) for the configured model
            "llm_concurrency": 3,
            "embedding_model": settings.embedding_model,
            "server_host": "0.0.0.0",
            "server_port": 8000,
            # HTTPS / TLS (nginx reverse proxy, prod only)
            "https_enabled": False,
            "llm_system_prompt": DEFAULT_SYSTEM_PROMPT,
            "llm_system_prompt_en": DEFAULT_SYSTEM_PROMPT_EN,
            "prompt_language": "en",
            "cache_ttl_seconds": 3600,
            "sandbox_network_mode": "deny",
            "sandbox_allow_domains": "",
            "sandbox_allow_methods": "",
            # repl_auth_secret is auto-generated on first boot by
            # _ensure_repl_auth_secret and rotated via the admin UI. It is no
            # longer sourced from a mounted Docker secret file (Plan B).
            "repl_auth_secret": "",
            # jwt_secret is auto-generated on first boot by _ensure_jwt_secret
            # and rotated via the admin UI. It is no longer sourced from a
            # mounted Docker secret file (DB-ified, mirrors repl_auth_secret).
            "jwt_secret": "",
        }

    async def _load_from_db(self, legacy_file_existed: bool):
        """Load non-sensitive settings from DB. Seed defaults (and migrate legacy config) if empty."""
        from app.database import async_session
        from app.models.system_setting import SystemSetting
        from sqlalchemy import select

        async with async_session() as db:
            rows = (await db.execute(select(SystemSetting))).scalars().all()
            if rows:
                for row in rows:
                    self._config[row.setting_key] = json.loads(row.value) if row.value is not None else None
                print(f"[ConfigManager] loaded {len(rows)} settings from db")
                return

            # DB is empty: seed defaults, migrating legacy non-key fields if present.
            defaults_to_save = self._build_non_sensitive_defaults()
            if legacy_file_existed:
                for k, v in self._config.items():
                    if k in {"llm_api_key", "embedding_api_key"}:
                        continue
                    defaults_to_save[k] = v

            for k, v in defaults_to_save.items():
                db.add(SystemSetting(setting_key=k, value=json.dumps(v, ensure_ascii=False)))
            await db.commit()
            self._config.update(defaults_to_save)
            print(f"[ConfigManager] seeded {len(defaults_to_save)} settings to db")

            if legacy_file_existed:
                with self._lock:
                    keys_only = {
                        "llm_api_key": self._config.get("llm_api_key", ""),
                        "embedding_api_key": self._config.get("embedding_api_key", ""),
                    }
                    self._config.update(keys_only)
                    self._persist_keys_locked()
                print("[ConfigManager] migrated legacy config.enc to key-only format")

    def _persist_keys_locked(self):
        """Write only API keys to the encrypted file. Must hold _lock.

        HTTPS cert/key are intentionally NOT stored here — they live as
        plaintext in the durable TLS volume (see set_https / ensure_tls_config)
        because nginx must read them unencrypted anyway, and the volume survives
        container recreation. Only llm/embedding API keys are encrypted.
        """
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        keys_only = {
            "llm_api_key": self._config.get("llm_api_key", ""),
            "embedding_api_key": self._config.get("embedding_api_key", ""),
        }
        plain = json.dumps(keys_only, ensure_ascii=False)
        self._config_file.write_bytes(_encrypt(plain))

    async def _ensure_repl_auth_secret(self):
        """Guarantee a non-empty REPL_AUTH_SECRET exists (first-boot default).

        The secret is the single source of truth, persisted in the DB:
          * DB empty -> a fresh secret is generated and persisted on first boot.
          * DB has rows but predates this key -> missing -> generate + persist.
          * Key present but blank -> regenerate (shouldn't happen, defensive).
        UI rotation (POST /api/config/repl-auth/regenerate) is canonical and
        survives restarts — no mounted secret file overrides it on boot.
        """
        with self._lock:
            current = self._config.get("repl_auth_secret", "") or ""
        if current:
            return
        new_secret = _generate_repl_auth_secret()
        with self._lock:
            self._config["repl_auth_secret"] = new_secret
        await self._save_db_settings({"repl_auth_secret": new_secret})
        print("[ConfigManager] generated preset REPL_AUTH_SECRET")

    async def _ensure_jwt_secret(self):
        """Guarantee a non-empty JWT signing secret exists (first-boot default).

        The secret is the single source of truth, persisted in the DB:
          * DB empty -> a fresh secret is generated and persisted on first boot.
          * DB has rows but predates this key -> missing -> generate + persist.
          * Key present but blank -> regenerate (defensive).
        UI rotation (POST /api/config/jwt-secret/regenerate) is canonical and
        survives restarts — no mounted secret file overrides it on boot. The
        auth layer reads it live from ConfigManager (auth.get_jwt_secret), so a
        rotation takes effect on the next token sign/verify with zero restart.
        """
        with self._lock:
            current = self._config.get("jwt_secret", "") or ""
        if current:
            return
        new_secret = _generate_jwt_secret()
        with self._lock:
            self._config["jwt_secret"] = new_secret
        await self._save_db_settings({"jwt_secret": new_secret})
        print("[ConfigManager] generated preset JWT secret")

    # ── Properties ──

    @property
    def api_key(self) -> str:
        with self._lock:
            return self._config.get("llm_api_key", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def base_url(self) -> str:
        with self._lock:
            return self._config.get("llm_base_url", "")

    @property
    def context_window(self) -> int:
        """Max context window (tokens) of the configured model (for UI usage bars)."""
        with self._lock:
            try:
                return int(self._config.get("llm_context_window", 128000))
            except (TypeError, ValueError):
                return 128000

    @property
    def model(self) -> str:
        with self._lock:
            return self._config.get("llm_model", "")

    @property
    def temperature(self) -> float:
        with self._lock:
            return self._config.get("llm_temperature", 0.4)

    @property
    def max_tokens(self) -> int:
        with self._lock:
            return self._config.get("llm_max_tokens", 4096)

    @property
    def concurrency(self) -> int:
        with self._lock:
            return self._config.get("llm_concurrency", 3)

    @property
    def embedding_model(self) -> str:
        with self._lock:
            return self._config.get("embedding_model", "BAAI/bge-small-zh-v1.5")

    @property
    def server_host(self) -> str:
        with self._lock:
            return self._config.get("server_host", "0.0.0.0")

    @property
    def server_port(self) -> int:
        with self._lock:
            return self._config.get("server_port", 8000)

    @property
    def llm_provider(self) -> str:
        with self._lock:
            return self._config.get("llm_provider", "openai")

    @property
    def platform(self) -> str:
        """Normalized platform key for provider-specific adaptations.

        Explicit ``llm_provider`` value wins; falls back to inferring from the
        ``llm_base_url`` domain. Returns one of:
        ``openai`` | ``anthropic`` | ``qwen`` (Aliyun Bailian) | ``tencent``
        (TokenHub) | ``ollama``.
        """
        with self._lock:
            raw = (self._config.get("llm_provider") or "openai").lower()
            base = (self._config.get("llm_base_url") or "").lower()
        explicit = {
            "anthropic": "anthropic", "claude": "anthropic",
            "qwen": "qwen", "alibaba": "qwen", "dashscope": "qwen",
            "tencent": "tencent", "tokenhub": "tencent", "hunyuan": "tencent",
            "openai": "openai", "ollama": "ollama",
        }
        if raw in explicit:
            return explicit[raw]
        if "anthropic" in base:
            return "anthropic"
        if "dashscope" in base or "aliyun" in base or "qwen" in base:
            return "qwen"
        if "tencentcloud" in base or "tokenhub" in base or "hunyuan" in base:
            return "tencent"
        if "ollama" in base:
            return "ollama"
        return "openai"

    @property
    def embedding_api_key(self) -> str:
        with self._lock:
            return self._config.get("embedding_api_key", "")

    @property
    def cache_ttl_seconds(self) -> int:
        with self._lock:
            return self._config.get("cache_ttl_seconds", 3600)

    @property
    def sandbox_network_mode(self) -> str:
        with self._lock:
            return self._config.get("sandbox_network_mode", "deny")

    @property
    def sandbox_allow_domains(self) -> str:
        with self._lock:
            return self._config.get("sandbox_allow_domains", "")

    @property
    def sandbox_allow_methods(self) -> str:
        with self._lock:
            return self._config.get("sandbox_allow_methods", "")

    @property
    def repl_auth_secret(self) -> str:
        """REPL MCP identity HMAC secret (runtime value, source of truth).

        Auto-generated on first boot and persisted to DB, so per-user UID
        isolation is enabled out of the box without manual .env setup.
        """
        with self._lock:
            return self._config.get("repl_auth_secret", "") or ""

    @property
    def jwt_secret(self) -> str:
        """JWT HS256 signing secret (runtime value, source of truth).

        Auto-generated on first boot and persisted to DB. Used by the auth
        layer to sign/verify session tokens. The value is read live from this
        cache, which is updated synchronously when the secret is rotated in the
        UI — so rotation takes effect immediately, with no backend restart.
        """
        with self._lock:
            return self._config.get("jwt_secret", "") or ""

    @property
    def system_prompt(self) -> str:
        """Effective system prompt, selected by prompt_language:
        'en' -> llm_system_prompt_en (default), otherwise -> llm_system_prompt (Chinese)."""
        with self._lock:
            lang = self._config.get("prompt_language", "en")
            if lang == "en":
                return (self._config.get("llm_system_prompt_en") or "").strip() or DEFAULT_SYSTEM_PROMPT_EN
            return (self._config.get("llm_system_prompt") or "").strip() or DEFAULT_SYSTEM_PROMPT

    @property
    def prompt_language(self) -> str:
        """Agent-graph prompt language: 'zh' | 'en' (default 'en'; switch to 'zh' for Chinese)."""
        with self._lock:
            return self._config.get("prompt_language", "en")

    # ── Public API ──

    def get_config_safe(self) -> dict:
        """Return full config with API keys masked."""
        with self._lock:
            c = dict(self._config)
            c["llm_api_key"] = _mask(c.get("llm_api_key", ""))
            c["embedding_api_key"] = _mask(c.get("embedding_api_key", ""))
            # Mask the REPL auth secret in the general config payload; the
            # dedicated /api/config/repl-auth endpoint returns the real value.
            c["repl_auth_secret"] = _mask(c.get("repl_auth_secret", ""))

            c["is_configured"] = bool(self._config.get("llm_api_key", ""))
            # API keys are always sourced from the Settings UI (encrypted into
            # config.enc); there is no .env / mounted-secret default path.
            c["api_key_source"] = "stored"
            return c

    async def update(self, data: dict) -> dict:
        """Partial update. API keys go to encrypted file; other settings go to DB."""
        allowed = {
            "llm_provider", "llm_model", "llm_api_key",
            "llm_base_url", "llm_temperature", "llm_max_tokens",
            "llm_concurrency", "embedding_model", "embedding_api_key",
            "llm_context_window",
            "server_host", "server_port", "llm_system_prompt", "llm_system_prompt_en", "prompt_language",
            "cache_ttl_seconds",
            "sandbox_network_mode", "sandbox_allow_domains", "sandbox_allow_methods",
            "repl_auth_secret", "jwt_secret",
            "https_enabled",
        }
        patch = {k: v for k, v in data.items() if k in allowed and v is not None}

        encrypted_patch = {k: v for k, v in patch.items() if k in {"llm_api_key", "embedding_api_key"}}
        db_patch = {k: v for k, v in patch.items() if k not in {"llm_api_key", "embedding_api_key"}}

        with self._lock:
            self._config.update(patch)
            if encrypted_patch:
                self._persist_keys_locked()
                # A key saved via the Settings UI is the only source — nothing
                # to override.

        if db_patch:
            await self._save_db_settings(db_patch)

        return self.get_config_safe()

    async def _save_db_settings(self, patch: dict):
        """Persist non-sensitive settings to the database."""
        from app.database import async_session
        from app.models.system_setting import SystemSetting
        from sqlalchemy import select

        async with async_session() as db:
            rows = (await db.execute(select(SystemSetting))).scalars().all()
            existing_map = {row.setting_key: row for row in rows}
            for k, v in patch.items():
                if k in existing_map:
                    existing_map[k].value = json.dumps(v, ensure_ascii=False)
                else:
                    db.add(SystemSetting(setting_key=k, value=json.dumps(v, ensure_ascii=False)))
            await db.commit()

    # ── HTTPS / TLS (nginx reverse proxy, prod only) ──

    @property
    def https_enabled(self) -> bool:
        with self._lock:
            return bool(self._config.get("https_enabled", False))

    @property
    def https_cert(self) -> str:
        """Cert PEM is stored ONLY in the durable TLS volume (plaintext, nginx
        must read it unencrypted). Read it back from there — never from _config."""
        c, _ = self._read_volume_cert_key()
        return c or ""

    @property
    def https_key(self) -> str:
        """Key PEM is stored ONLY in the durable TLS volume (plaintext)."""
        _, k = self._read_volume_cert_key()
        return k or ""

    def get_https_config(self) -> dict:
        """Masked HTTPS status for the settings UI (no secret material)."""
        with self._lock:
            enabled = bool(self._config.get("https_enabled", False))
            meta = self._config.get("https_cert_meta")
        c, k = self._read_volume_cert_key()
        return {
            "https_enabled": enabled,
            "cert_configured": bool(c and k),
            "cert_meta": meta,
        }

    def _read_volume_cert_key(self) -> tuple[str | None, str | None]:
        """Read cert/key PEMs from the durable TLS volume if present.

        The TLS volume is a named Docker volume that persists across container
        recreation, unlike the DB/key-store which lives in the ephemeral
        writable layer and is wiped on recreate. Recovering from the volume is
        what keeps HTTPS working after a `docker compose up` that recreates the
        backend container.
        """
        try:
            full = TLS_DIR / "fullchain.pem"
            priv = TLS_DIR / "privkey.pem"
            if full.exists() and priv.exists():
                c = full.read_text(encoding="utf-8").strip()
                k = priv.read_text(encoding="utf-8").strip()
                if c and k:
                    return c, k
        except Exception:
            pass
        return None, None

    def ensure_tls_config(self) -> None:
        """Seed the shared TLS volume so nginx can start.

        Called at startup (sync — no DB writes here). HTTPS state is recovered
        from the durable TLS volume: if valid cert/key PEMs exist there (written
        when HTTPS was enabled), HTTPS is kept on even after the backend
        container is recreated. The cert/key live ONLY in this volume (nginx
        must read them unencrypted); they are never stored in config.enc.

        Sync by design — persisting the recovered ``https_enabled`` flag to DB
        is not needed: the volume is the source of truth, and the next recreate
        re-runs this same recovery. Failures are non-fatal (e.g. TLS volume not
        mounted in dev).
        """
        try:
            enabled = self.https_enabled
            vc, vk = self._read_volume_cert_key()
            # Recover when the DB flag is off but valid cert material is present
            # on the volume (the normal case after a backend container recreate).
            if not enabled and vc and vk:
                try:
                    _validate_cert_key(vc, vk)
                    enabled = True
                    with self._lock:
                        self._config["https_enabled"] = True
                except Exception:
                    # Stale/invalid PEMs on the volume: ignore and fall through
                    # to the HTTP-only branch.
                    enabled, vc, vk = False, None, None
            if enabled and vc and vk:
                self._write_tls(vc, vk, True)
            else:
                self._write_tls(None, None, False)
        except Exception as e:  # pragma: no cover - best effort
            print(f"[ConfigManager] TLS volume not available, skipping nginx config: {e}")

    def _write_tls(self, cert: str | None, key: str | None, https_enabled: bool) -> None:
        """Write cert/key + rendered nginx conf into the shared TLS volume."""
        tls_dir = TLS_DIR
        tls_dir.mkdir(parents=True, exist_ok=True)
        default_conf = tls_dir / "default.conf"
        if https_enabled and cert and key:
            (tls_dir / "fullchain.pem").write_text(cert, encoding="utf-8")
            (tls_dir / "privkey.pem").write_text(key, encoding="utf-8")
            os.chmod(tls_dir / "fullchain.pem", 0o600)
            os.chmod(tls_dir / "privkey.pem", 0o600)
            default_conf.write_text(_render_nginx_conf(True), encoding="utf-8")
        else:
            for fname in ("fullchain.pem", "privkey.pem"):
                p = tls_dir / fname
                if p.exists():
                    p.unlink()
            default_conf.write_text(_render_nginx_conf(False), encoding="utf-8")

    async def set_https(self, enabled: bool, cert: str | None, key: str | None) -> dict | None:
        """Persist HTTPS settings and materialize TLS material.

        Returns the cert metadata dict when enabled, else None. Raises
        ValueError (surfaced as HTTP 400) when cert/key are invalid or missing.
        """
        cert = (cert or "").strip()
        key = (key or "").strip()
        if enabled:
            if not cert or not key:
                raise ValueError("certificate and private key are both required when enabling HTTPS")
            meta = _validate_cert_key(cert, key)
            # Cert/key are written ONLY to the durable TLS volume (plaintext,
            # because nginx must read them unencrypted). They are never stored
            # in config.enc. Only the enabled flag + cert metadata go to DB.
            self._write_tls(cert, key, True)
            with self._lock:
                self._config["https_enabled"] = True
                self._config["https_cert_meta"] = meta
            await self._save_db_settings({"https_enabled": True, "https_cert_meta": meta})
            return meta
        # Disabled: clear material + config
        self._write_tls(None, None, False)
        with self._lock:
            self._config["https_enabled"] = False
            self._config["https_cert_meta"] = None
        await self._save_db_settings({"https_enabled": False, "https_cert_meta": None})
        return None


# Module-level singleton
config_manager = ConfigManager()

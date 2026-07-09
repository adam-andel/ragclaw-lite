"""Runtime configuration manager — DB for non-sensitive settings + AES-256-GCM encrypted file for API keys.

Admin manages all runtime settings through the web UI. A .env LLM_API_KEY is used as the default
key source when present and no key has been persisted via the web UI; a persisted key overrides it.
"""

import json
import os
import threading
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from app.config import settings

_SEED = b"erag-llm-config-v2-seed-2026"
_SALT = b"erag-config-salt-v2"

DEFAULT_SYSTEM_PROMPT = """你是一个企业知识库助手。根据提供的文档内容回答问题。

## 规则
1. 只根据提供的文档内容回答，不要编造信息
2. 如果文档中没有相关信息，诚实地说"文档中未找到相关信息"
3. 在回答中标注引用来源，格式：[来源: 文档名 章节名]
4. 回答要简洁、准确，使用中文
5. 如果文档内容包含代码或表格，保留原始格式"""


def _derive_key() -> bytes:
    machine = str(uuid.getnode()).encode()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=100_000,
    )
    return kdf.derive(machine + _SEED)


def _encrypt(plaintext: str) -> bytes:
    key = _derive_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    return nonce + aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)


def _decrypt(data: bytes) -> str:
    key = _derive_key()
    aesgcm = AESGCM(key)
    nonce, ct = data[:12], data[12:]
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")


def _read_env_api_key() -> str:
    """Read LLM_API_KEY to use as the default API key source.

    Priority: process environment first (the .env values are injected as env vars in
    both local and Docker modes), then fall back to parsing the .env file directly
    (covers cases where the env var was not propagated but the file is mounted).
    A key persisted via the web UI (config.enc) still overrides this.
    """
    env_val = (os.environ.get("LLM_API_KEY") or "").strip().strip('"').strip("'")
    if env_val:
        return env_val

    env_path = settings.project_root / ".env"
    if not env_path.exists():
        return ""
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.split("=", 1)[0].strip().upper() == "LLM_API_KEY":
                    _, _, val = line.partition("=")
                    return val.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _mask(key: str) -> str:
    """Mask API key for safe display."""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:4] + "****"
    return key[:6] + "****" + key[-4:]


class ConfigManager:
    """Thread-safe singleton for runtime configuration."""

    _instance = None
    _lock = threading.Lock()

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
        self._env_api_key = ""
        self._key_from_env = False
        self._initialized = True

    # ── Lifecycle ──

    async def init(self):
        """Called once at startup. Loads API keys from encrypted file and non-sensitive settings from DB.

        A .env LLM_API_KEY is used as the default source. A *non-empty* key persisted via
        the web UI (config.enc) overrides the .env value; an empty stored key (never
        configured in the UI) does NOT clobber a present .env key.
        """
        legacy_file_existed = False
        with self._lock:
            env_key = _read_env_api_key()
            self._env_api_key = env_key
            self._config = self._build_defaults(env_key)
            # The .env key is the default source unless a non-empty stored key overrides it.
            self._key_from_env = bool(env_key)
            if self._config_file.exists():
                try:
                    saved = json.loads(_decrypt(self._config_file.read_bytes()))
                    # Only a non-empty stored key overrides the .env source.
                    stored_key = saved.get("llm_api_key", "")
                    if stored_key:
                        self._config["llm_api_key"] = stored_key
                        self._key_from_env = False
                    stored_emb = saved.get("embedding_api_key", "")
                    if stored_emb:
                        self._config["embedding_api_key"] = stored_emb
                    legacy_file_existed = True
                    print("[ConfigManager] loaded encrypted config")
                except Exception as e:
                    print(f"[ConfigManager] decrypt failed: {e}, using defaults")
            else:
                print("[ConfigManager] no encrypted config yet — waiting for admin setup")

        await self._load_from_db(legacy_file_existed)

    def _build_defaults(self, env_api_key: str = "") -> dict:
        return {
            # LLM
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_api_key": env_api_key,
            "llm_base_url": settings.llm_base_url,
            "llm_temperature": settings.llm_temperature,
            "llm_max_tokens": settings.llm_max_tokens,
            "llm_concurrency": 3,
            # Embedding
            "embedding_model": settings.embedding_model,
            "embedding_api_key": "",
            # Server (startup-time only)
            "server_host": "0.0.0.0",
            "server_port": 8000,
            # System prompt
            "llm_system_prompt": DEFAULT_SYSTEM_PROMPT,
            # Cache
            "cache_ttl_seconds": 3600,
        }

    def _build_non_sensitive_defaults(self) -> dict:
        return {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_base_url": settings.llm_base_url,
            "llm_temperature": settings.llm_temperature,
            "llm_max_tokens": settings.llm_max_tokens,
            "llm_concurrency": 3,
            "embedding_model": settings.embedding_model,
            "server_host": "0.0.0.0",
            "server_port": 8000,
            "llm_system_prompt": DEFAULT_SYSTEM_PROMPT,
            "cache_ttl_seconds": 3600,
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
        """Write only API keys to encrypted file. Must hold _lock."""
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        keys_only = {
            "llm_api_key": self._config.get("llm_api_key", ""),
            "embedding_api_key": self._config.get("embedding_api_key", ""),
        }
        plain = json.dumps(keys_only, ensure_ascii=False)
        self._config_file.write_bytes(_encrypt(plain))

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
    def model(self) -> str:
        with self._lock:
            return self._config.get("llm_model", "")

    @property
    def temperature(self) -> float:
        with self._lock:
            return self._config.get("llm_temperature", 0.3)

    @property
    def max_tokens(self) -> int:
        with self._lock:
            return self._config.get("llm_max_tokens", 2048)

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
    def embedding_api_key(self) -> str:
        with self._lock:
            return self._config.get("embedding_api_key", "")

    @property
    def cache_ttl_seconds(self) -> int:
        with self._lock:
            return self._config.get("cache_ttl_seconds", 3600)

    @property
    def system_prompt(self) -> str:
        with self._lock:
            return self._config.get("llm_system_prompt", DEFAULT_SYSTEM_PROMPT)

    # ── Public API ──

    def get_config_safe(self) -> dict:
        """Return full config with API keys masked."""
        with self._lock:
            c = dict(self._config)
            c["llm_api_key"] = _mask(c.get("llm_api_key", ""))
            c["embedding_api_key"] = _mask(c.get("embedding_api_key", ""))
            c["is_configured"] = bool(self._config.get("llm_api_key", ""))
            c["api_key_source"] = "env" if self._key_from_env else "stored"
            return c

    async def update(self, data: dict) -> dict:
        """Partial update. API keys go to encrypted file; other settings go to DB."""
        allowed = {
            "llm_provider", "llm_model", "llm_api_key",
            "llm_base_url", "llm_temperature", "llm_max_tokens",
            "llm_concurrency", "embedding_model", "embedding_api_key",
            "server_host", "server_port", "llm_system_prompt",
            "cache_ttl_seconds",
        }
        patch = {k: v for k, v in data.items() if k in allowed and v is not None}

        encrypted_patch = {k: v for k, v in patch.items() if k in {"llm_api_key", "embedding_api_key"}}
        db_patch = {k: v for k, v in patch.items() if k not in {"llm_api_key", "embedding_api_key"}}

        with self._lock:
            self._config.update(patch)
            if encrypted_patch:
                self._persist_keys_locked()
                # An explicitly saved key now overrides the .env source.
                if "llm_api_key" in encrypted_patch:
                    self._key_from_env = False

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


# Module-level singleton
config_manager = ConfigManager()

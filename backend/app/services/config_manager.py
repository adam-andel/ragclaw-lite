"""Runtime LLM configuration manager — memory + AES-256-GCM encrypted file.

The .env file is NOT used as an API key source.
Admin sets the key through the web UI (SettingsView → PUT /api/config/llm).
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


def _mask(key: str) -> str:
    """Mask API key for safe display."""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:4] + "****"
    return key[:6] + "****" + key[-4:]


class ConfigManager:
    """Thread-safe singleton for LLM runtime configuration."""

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
        self._initialized = True

    # ── Lifecycle ──

    def init(self):
        """Called once at startup.

        Defaults come from Settings for non-sensitive fields (model, url, etc.).
        api_key is always empty by default — admin must configure via web UI.
        If config.enc exists, its values override the defaults.
        """
        with self._lock:
            self._config = {
                "llm_provider": settings.llm_provider,
                "llm_model": settings.llm_model,
                "llm_api_key": "",
                "llm_base_url": settings.llm_base_url,
                "llm_temperature": settings.llm_temperature,
                "llm_max_tokens": settings.llm_max_tokens,
            }
            if self._config_file.exists():
                try:
                    saved = json.loads(_decrypt(self._config_file.read_bytes()))
                    self._config.update(saved)
                    print("[ConfigManager] loaded encrypted config")
                except Exception as e:
                    print(f"[ConfigManager] decrypt failed: {e}, using defaults")
            else:
                print("[ConfigManager] no encrypted config yet — waiting for admin setup")

    def _persist_locked(self):
        """Write current config to encrypted file. Must hold _lock."""
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        plain = json.dumps(self._config, ensure_ascii=False)
        self._config_file.write_bytes(_encrypt(plain))

    # ── Properties (used by llm_client) ──

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

    # ── Public API ──

    def get_config_safe(self) -> dict:
        """Return full config with API key masked."""
        with self._lock:
            c = dict(self._config)
            c["llm_api_key"] = _mask(c.get("llm_api_key", ""))
            c["is_configured"] = bool(self._config.get("llm_api_key", ""))
            return c

    def update(self, data: dict) -> dict:
        """Partial update. Writes memory + encrypted file. First-time setup uses this too."""
        allowed = {
            "llm_provider", "llm_model", "llm_api_key",
            "llm_base_url", "llm_temperature", "llm_max_tokens",
        }
        patch = {k: v for k, v in data.items() if k in allowed and v is not None}
        with self._lock:
            self._config.update(patch)
            self._persist_locked()
        return self.get_config_safe()


# Module-level singleton
config_manager = ConfigManager()

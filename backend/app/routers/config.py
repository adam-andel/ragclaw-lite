"""LLM configuration management routes (admin only)."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
import httpx

from app.services.auth import get_current_admin
from app.services.config_manager import config_manager
from app.services.llm_semaphore import llm_limiter
from app.config import settings

router = APIRouter(prefix="/api/config", tags=["Config"])

logger = logging.getLogger("ragclaw.config")


class LLMConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    llm_context_window: int | None = None
    llm_concurrency: int | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    llm_system_prompt: str | None = None
    llm_system_prompt_en: str | None = None
    prompt_language: str | None = None
    server_host: str | None = None
    server_port: int | None = None
    cache_ttl_seconds: int | None = None

    @field_validator("llm_temperature")
    @classmethod
    def temp_range(cls, v):
        if v is not None and not (0 <= v <= 2):
            raise ValueError("temperature 必须在 0-2 之间")
        return v

    @field_validator("server_port")
    @classmethod
    def port_range(cls, v):
        if v is not None and not (1 <= v <= 65535):
            raise ValueError("port 必须在 1-65535 之间")
        return v

    @field_validator("llm_max_tokens")
    @classmethod
    def tokens_range(cls, v):
        if v is not None and not (128 <= v <= 131072):
            raise ValueError("max_tokens 必须在 128-131072 之间")
        return v

    @field_validator("llm_context_window")
    @classmethod
    def context_window_range(cls, v):
        if v is not None and not (1 <= v <= 10_000_000):
            raise ValueError("上下文窗口必须在 1-10,000,000 之间")
        return v

    @field_validator("llm_concurrency")
    @classmethod
    def concurrency_range(cls, v):
        if v is not None and not (1 <= v <= 50):
            raise ValueError("并发数必须在 1-50 之间")
        return v


@router.get("/llm")
async def get_llm_config(current_user=Depends(get_current_admin)):
    """Read the current LLM configuration (API Key masked)."""
    return config_manager.get_config_safe()


@router.put("/llm")
async def update_llm_config(data: LLMConfigUpdate, current_user=Depends(get_current_admin)):
    """Update the LLM configuration (including first-time entry). Takes effect immediately after update, no restart needed."""
    if data.llm_api_key is not None and not data.llm_api_key.strip():
        raise HTTPException(status_code=400, detail="API Key 不能为空")
    result = await config_manager.update(data.model_dump(exclude_none=True))
    if data.llm_concurrency is not None:
        await llm_limiter.update_max(data.llm_concurrency)
    return {"message": "配置已更新，立即生效", "config": result}


class TestRequest(BaseModel):
    query: str = "Hello, respond with 'OK' only."


@router.post("/llm/test")
async def test_llm_connection(data: TestRequest, current_user=Depends(get_current_admin)):
    """Test whether the LLM connection works."""
    if not config_manager.is_configured:
        raise HTTPException(status_code=400, detail="尚未配置 API Key，请先在设置页面录入")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config_manager.base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config_manager.api_key}",
                },
                json={
                    "model": config_manager.model,
                    "messages": [{"role": "user", "content": data.query}],
                    "max_tokens": 50,
                    "temperature": 0,
                },
            )
            if resp.status_code == 200:
                body = resp.json()
                return {
                    "ok": True,
                    "reply": body["choices"][0]["message"]["content"][:200],
                    "model": config_manager.model,
                }
            detail = resp.text[:500]
            return {"ok": False, "error": f"HTTP {resp.status_code}: {detail}"}
    except httpx.ConnectError as e:
        return {"ok": False, "error": f"连接失败: {str(e)}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# Sandbox network policy (REPL MCP) — hot-reloadable
# ═══════════════════════════════════════════════════════════

class SandboxNetworkUpdate(BaseModel):
    sandbox_network_mode: str | None = None   # deny | allow | allowlist
    sandbox_allow_domains: str | None = None  # comma-separated
    sandbox_allow_methods: str | None = None  # comma-separated (reserved)

    @field_validator("sandbox_network_mode")
    @classmethod
    def _mode_range(cls, v):
        if v is not None and v not in ("deny", "allow", "allowlist"):
            raise ValueError("network mode 必须是 deny / allow / allowlist")
        return v


@router.get("/sandbox-network")
async def get_sandbox_network(current_user=Depends(get_current_admin)):
    """Read the sandbox network policy (mode + allowlisted domains)."""
    return {
        "sandbox_network_mode": config_manager.sandbox_network_mode,
        "sandbox_allow_domains": config_manager.sandbox_allow_domains,
        "sandbox_allow_methods": config_manager.sandbox_allow_methods,
    }


@router.put("/sandbox-network")
async def update_sandbox_network(
    data: SandboxNetworkUpdate,
    current_user=Depends(get_current_admin),
):
    """Update the sandbox network policy; after saving it is hot-reloaded into the MCP REPL service (no restart needed)."""
    patch = {
        k: v for k, v in data.model_dump(exclude_none=True).items()
        if k in {"sandbox_network_mode", "sandbox_allow_domains", "sandbox_allow_methods"}
    }
    if not patch:
        raise HTTPException(status_code=400, detail="没有提供任何可更新的字段")
    await config_manager.update(patch)
    mcp_pushed = await notify_network_policy_changed()
    return {
        "message": "沙盒策略已更新，立即生效"
        + ("" if mcp_pushed else "（MCP 暂不可达，请确认 mcp-repl 已启动后重新保存以应用；系统会每 60 秒自动重试）"),
        "config": {
            "sandbox_network_mode": config_manager.sandbox_network_mode,
            "sandbox_allow_domains": config_manager.sandbox_allow_domains,
            "sandbox_allow_methods": config_manager.sandbox_allow_methods,
        },
        "mcp_pushed": mcp_pushed,
    }


async def _push_mcp_policy() -> bool:
    """Best-effort: hot-reload network policy into the MCP REPL container.

    The MCP server exposes PUT /policy on its internal Docker-network URL.
    Falls back to localhost for local (non-Docker) deployments.
    """
    domains = [d.strip() for d in config_manager.sandbox_allow_domains.split(",") if d.strip()]
    methods = [m.strip().upper() for m in config_manager.sandbox_allow_methods.split(",") if m.strip()]
    payload = {
        "mode": config_manager.sandbox_network_mode,
        "domains": domains,
        "methods": methods,
    }
    urls = [settings.mcp_repl_internal_url, "http://127.0.0.1:9200"]
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.put(url.rstrip("/") + "/policy", json=payload)
                if resp.status_code == 200:
                    logger.info("pushed network policy to %s ok", url)
                    return True
                logger.warning("push policy to %s -> HTTP %d", url, resp.status_code)
        except Exception as e:
            logger.warning("push policy to %s failed: %s", url, e)
    return False


# ── Network-policy self-healing ─────────────────────────────
# A background retry loop runs ONLY while a push has not yet succeeded (e.g.
# MCP was unreachable at save/startup time). After a successful push the loop
# stops on its own, so there is no perpetual 60s heartbeat. It is re-started by
# the next Settings save that fails to push, or by a backend restart.
_network_policy_pending: bool = False
_network_policy_retry_task = None


async def ensure_mcp_policy_pushed(retries: int = 6, interval: float = 2.0) -> bool:
    """Startup helper: push the current network policy to MCP, retrying because
    the MCP container may not be up yet when the backend starts.

    On failure, marks a retry as pending so the self-heal loop can take over.
    Returns True once a push succeeds.
    """
    global _network_policy_pending
    for attempt in range(1, retries + 1):
        ok = await _push_mcp_policy()
        if ok:
            _network_policy_pending = False
            return True
        if attempt < retries:
            await asyncio.sleep(interval)
    _network_policy_pending = True
    return False


async def _network_policy_retry_loop(interval: float = 60.0):
    """Retry pushing the network policy until a push succeeds, then stop.

    Runs only while `_network_policy_pending` is True. Started by either a
    failed Settings save or a failed startup push; terminates itself on the
    first success.
    """
    global _network_policy_pending, _network_policy_retry_task
    _network_policy_pending = True
    while _network_policy_pending:
        try:
            if await _push_mcp_policy():
                _network_policy_pending = False
                logger.info("network policy self-heal succeeded; stopping retry loop")
                _network_policy_retry_task = None
                return
        except Exception as e:  # pragma: no cover
            logger.warning("network policy retry failed: %s", e)
        await asyncio.sleep(interval)
    _network_policy_retry_task = None


async def ensure_network_policy_retry_running(interval: float = 60.0):
    """Start the self-heal retry loop if it is not already running."""
    global _network_policy_retry_task
    if _network_policy_retry_task is not None and not _network_policy_retry_task.done():
        return
    if not _network_policy_pending:
        return
    _network_policy_retry_task = asyncio.create_task(_network_policy_retry_loop(interval))


async def notify_network_policy_changed() -> bool:
    """Push the current network policy immediately (called after a Settings save).

    Returns True if the push succeeded. On failure, marks a retry as pending and
    starts the self-heal loop so the policy is applied once MCP becomes reachable
    — without a perpetual heartbeat.
    """
    global _network_policy_pending, _network_policy_retry_task
    try:
        if await _push_mcp_policy():
            _network_policy_pending = False
            return True
    except Exception as e:  # pragma: no cover
        logger.warning("push policy on save failed: %s", e)
    _network_policy_pending = True
    if _network_policy_retry_task is None or _network_policy_retry_task.done():
        _network_policy_retry_task = asyncio.create_task(_network_policy_retry_loop())
    return False


# ═══════════════════════════════════════════════════════════
# REPL MCP identity secret (HMAC) — hot-reloadable
# ═══════════════════════════════════════════════════════════

@router.get("/repl-auth")
async def get_repl_auth(current_user=Depends(get_current_admin)):
    """Read the current REPL MCP identity secret (admin only, unmasked)."""
    return {"repl_auth_secret": config_manager.repl_auth_secret}


@router.post("/repl-auth/regenerate")
async def regenerate_repl_auth(current_user=Depends(get_current_admin)):
    """Generate a new random REPL MCP identity secret; hot-reloaded into MCP (no restart)."""
    import secrets as _secrets
    new_secret = _secrets.token_hex(32)
    await config_manager.update({"repl_auth_secret": new_secret})
    mcp_pushed = await notify_auth_secret_changed(new_secret)
    return {
        "message": "已生成新的 REPL_AUTH_SECRET，立即生效"
        + ("" if mcp_pushed else "（MCP 暂不可达，系统会每 60 秒自动重试）"),
        "repl_auth_secret": config_manager.repl_auth_secret,
        "mcp_pushed": mcp_pushed,
    }


# ═══════════════════════════════════════════════════════════
# JWT signing secret (HS256) — DB-backed, hot-reloadable
# ═══════════════════════════════════════════════════════════

@router.get("/jwt-secret")
async def get_jwt_secret(current_user=Depends(get_current_admin)):
    """Read the current JWT signing secret (admin only, unmasked)."""
    return {"jwt_secret": config_manager.jwt_secret}


@router.post("/jwt-secret/regenerate")
async def regenerate_jwt_secret(current_user=Depends(get_current_admin)):
    """Generate a new random JWT signing secret; takes effect immediately.

    The auth layer reads the secret live from ConfigManager (auth.get_jwt_secret),
    so no restart is needed. NOTE: this invalidates every currently issued token,
    so all users (including the admin rotating it) must re-login.
    """
    import secrets as _secrets
    new_secret = _secrets.token_hex(32)
    await config_manager.update({"jwt_secret": new_secret})
    return {
        "message": (
            "Generated a new JWT signing secret — it takes effect immediately. "
            "All currently issued tokens are now invalid; every user must re-login."
        ),
        "jwt_secret": config_manager.jwt_secret,
    }


async def _push_mcp_auth_secret(secret: str) -> bool:
    """Best-effort: hot-reload the REPL identity secret into the MCP REPL container.

    The MCP server exposes PUT /auth-secret on its internal Docker-network URL.
    Falls back to localhost for local (non-Docker) deployments. The endpoint is
    internal-only (no host port mapping), mirroring /policy.
    """
    payload = {"secret": secret}
    urls = [settings.mcp_repl_internal_url, "http://127.0.0.1:9200"]
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.put(url.rstrip("/") + "/auth-secret", json=payload)
                if resp.status_code == 200:
                    logger.info("pushed auth-secret to %s ok", url)
                    return True
                logger.warning("push auth-secret to %s -> HTTP %d", url, resp.status_code)
        except Exception as e:
            logger.warning("push auth-secret to %s failed: %s", url, e)
    return False


# ── REPL auth-secret self-healing ──────────────────────────
# Same "stop after success" pattern as the network policy: a background retry
# loop runs ONLY while a push has not yet succeeded. After a successful push
# the loop stops on its own (no perpetual 60s heartbeat); the next Settings
# save (update/regenerate) re-triggers it if needed.
_auth_secret_pending: bool = False
_auth_secret_retry_task = None


async def ensure_mcp_auth_secret_pushed(retries: int = 6, interval: float = 2.0) -> bool:
    """Startup helper: push the current secret to MCP, retrying because the
    MCP container may not be up yet when the backend starts.

    On failure, marks a retry as pending so the self-heal loop can take over.
    Returns True once a push succeeds.
    """
    global _auth_secret_pending
    secret = config_manager.repl_auth_secret
    for attempt in range(1, retries + 1):
        ok = await _push_mcp_auth_secret(secret)
        if ok:
            _auth_secret_pending = False
            return True
        if attempt < retries:
            await asyncio.sleep(interval)
    _auth_secret_pending = True
    return False


async def _auth_secret_retry_loop(interval: float = 60.0):
    """Retry pushing the auth secret until a push succeeds, then stop.

    Runs only while `_auth_secret_pending` is True. Started by either a failed
    Settings save or a failed startup push; terminates itself on the first
    success.
    """
    global _auth_secret_pending, _auth_secret_retry_task
    _auth_secret_pending = True
    while _auth_secret_pending:
        try:
            if await _push_mcp_auth_secret(config_manager.repl_auth_secret):
                _auth_secret_pending = False
                logger.info("REPL auth-secret self-heal succeeded; stopping retry loop")
                _auth_secret_retry_task = None
                return
        except Exception as e:  # pragma: no cover
            logger.warning("REPL auth-secret retry failed: %s", e)
        await asyncio.sleep(interval)
    _auth_secret_retry_task = None


async def ensure_auth_secret_retry_running(interval: float = 60.0):
    """Start the self-heal retry loop if it is not already running."""
    global _auth_secret_retry_task
    if _auth_secret_retry_task is not None and not _auth_secret_retry_task.done():
        return
    if not _auth_secret_pending:
        return
    _auth_secret_retry_task = asyncio.create_task(_auth_secret_retry_loop(interval))


async def notify_auth_secret_changed(secret: str) -> bool:
    """Push the auth secret immediately (called after a Settings save).

    Returns True if the push succeeded. On failure, marks a retry as pending and
    starts the self-heal loop so the secret is applied once MCP becomes reachable
    — without a perpetual heartbeat.
    """
    global _auth_secret_pending, _auth_secret_retry_task
    try:
        if await _push_mcp_auth_secret(secret):
            _auth_secret_pending = False
            return True
    except Exception as e:  # pragma: no cover
        logger.warning("push auth-secret on save failed: %s", e)
    _auth_secret_pending = True
    if _auth_secret_retry_task is None or _auth_secret_retry_task.done():
        _auth_secret_retry_task = asyncio.create_task(_auth_secret_retry_loop())
    return False


# ═══════════════════════════════════════════════════════════
# HTTPS / TLS (nginx reverse proxy, prod only)
# ═══════════════════════════════════════════════════════════

class HTTPSConfigUpdate(BaseModel):
    https_enabled: bool = False
    https_cert: str | None = None
    https_key: str | None = None


@router.get("/https")
async def get_https_config(current_user=Depends(get_current_admin)):
    """Read HTTPS status (masked; no secret material returned)."""
    return config_manager.get_https_config()


@router.put("/https")
async def update_https_config(
    data: HTTPSConfigUpdate,
    current_user=Depends(get_current_admin),
):
    """Enable/disable HTTPS.

    When enabling, validates the cert/key pair, persists it (encrypted) and
    materializes the TLS material into the shared volume so the nginx reverse
    proxy serves the site over HTTPS. Takes effect after nginx picks up the new
    config (inotify reload) — no backend restart needed.
    """
    try:
        meta = await config_manager.set_https(data.https_enabled, data.https_cert, data.https_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "message": "HTTPS configuration saved",
        "https_enabled": config_manager.https_enabled,
        "cert_configured": bool(config_manager.https_cert and config_manager.https_key),
        "cert_meta": meta,
    }

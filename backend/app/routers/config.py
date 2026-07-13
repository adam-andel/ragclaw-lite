"""LLM configuration management routes (admin only)."""

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

logger = logging.getLogger("erag.config")


class LLMConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    agent_max_tokens: int | None = None
    llm_context_window: int | None = None
    llm_concurrency: int | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    llm_system_prompt: str | None = None
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

    @field_validator("agent_max_tokens")
    @classmethod
    def agent_tokens_range(cls, v):
        if v is not None and not (128 <= v <= 131072):
            raise ValueError("agent_max_tokens 必须在 128-131072 之间")
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
    """读取当前 LLM 配置（API Key 脱敏）。"""
    return config_manager.get_config_safe()


@router.put("/llm")
async def update_llm_config(data: LLMConfigUpdate, current_user=Depends(get_current_admin)):
    """更新 LLM 配置（含首次录入）。更新后立即生效，无需重启。"""
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
    """测试 LLM 连接是否正常。"""
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
    mcp_file_keep_minutes: int | None = None  # generated-file retention (minutes)

    @field_validator("sandbox_network_mode")
    @classmethod
    def _mode_range(cls, v):
        if v is not None and v not in ("deny", "allow", "allowlist"):
            raise ValueError("network mode 必须是 deny / allow / allowlist")
        return v


@router.get("/sandbox-network")
async def get_sandbox_network(current_user=Depends(get_current_admin)):
    """读取沙盒网络策略（模式 + 白名单域名）。"""
    return {
        "sandbox_network_mode": config_manager.sandbox_network_mode,
        "sandbox_allow_domains": config_manager.sandbox_allow_domains,
        "sandbox_allow_methods": config_manager.sandbox_allow_methods,
        "mcp_file_keep_minutes": config_manager.mcp_file_keep_minutes,
    }


@router.put("/sandbox-network")
async def update_sandbox_network(
    data: SandboxNetworkUpdate,
    current_user=Depends(get_current_admin),
):
    """更新沙盒网络策略，保存后立即热加载到 MCP REPL 服务（无需重启）。"""
    patch = {
        k: v for k, v in data.model_dump(exclude_none=True).items()
        if k in {"sandbox_network_mode", "sandbox_allow_domains", "sandbox_allow_methods", "mcp_file_keep_minutes"}
    }
    if not patch:
        raise HTTPException(status_code=400, detail="没有提供任何可更新的字段")
    await config_manager.update(patch)
    pushed_policy = await _push_mcp_policy()
    pushed_keep = await _push_mcp_keep_minutes() if "mcp_file_keep_minutes" in patch else True
    mcp_pushed = pushed_policy and pushed_keep
    return {
        "message": "沙盒策略已更新，立即生效"
        + ("" if mcp_pushed else "（MCP 热加载未成功，下次重启容器将自动应用）"),
        "config": {
            "sandbox_network_mode": config_manager.sandbox_network_mode,
            "sandbox_allow_domains": config_manager.sandbox_allow_domains,
            "sandbox_allow_methods": config_manager.sandbox_allow_methods,
            "mcp_file_keep_minutes": config_manager.mcp_file_keep_minutes,
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


async def _push_mcp_keep_minutes() -> bool:
    """Best-effort: hot-reload generated-file retention into the MCP REPL container.

    The MCP server exposes PUT /keep-minutes on its internal Docker-network URL.
    Falls back to localhost for local (non-Docker) deployments.
    """
    payload = {"keep_minutes": config_manager.mcp_file_keep_minutes}
    urls = [settings.mcp_repl_internal_url, "http://127.0.0.1:9200"]
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.put(url.rstrip("/") + "/keep-minutes", json=payload)
                if resp.status_code == 200:
                    logger.info("pushed keep_minutes to %s ok", url)
                    return True
                logger.warning("push keep_minutes to %s -> HTTP %d", url, resp.status_code)
        except Exception as e:
            logger.warning("push keep_minutes to %s failed: %s", url, e)
    return False

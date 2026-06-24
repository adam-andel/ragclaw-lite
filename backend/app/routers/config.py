"""LLM configuration management routes (admin only)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
import httpx

from app.services.auth import get_current_admin
from app.services.config_manager import config_manager

router = APIRouter(prefix="/api/config", tags=["Config"])


class LLMConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None

    @field_validator("llm_temperature")
    @classmethod
    def temp_range(cls, v):
        if v is not None and not (0 <= v <= 2):
            raise ValueError("temperature 必须在 0-2 之间")
        return v

    @field_validator("llm_max_tokens")
    @classmethod
    def tokens_range(cls, v):
        if v is not None and not (128 <= v <= 131072):
            raise ValueError("max_tokens 必须在 128-131072 之间")
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
    result = config_manager.update(data.model_dump(exclude_none=True))
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

"""LLM client abstraction layer supporting multiple providers.

Provider-aware prompt-caching support:
  - Anthropic / Aliyun (qwen): ``cache_control`` breakpoint on the system message
  - Tencent TokenHub: ``prompt_cache_key`` (body) + ``X-Session-ID`` (header)
  - OpenAI / Ollama: no extra params (OpenAI auto-caches prefixes)

The active platform is resolved via ``config_manager.platform`` (explicit
``llm_provider`` value, falling back to the ``llm_base_url`` domain).
"""

import json
import logging
import time
from typing import AsyncGenerator

import httpx
import contextvars

from app.services.config_manager import config_manager

logger = logging.getLogger("ragclaw.llm")
logger.setLevel(logging.INFO)

# ── Request timeouts & per-turn budget (P1: stop the bleeding) ──────────────
# A single non-streaming call must fail fast if the upstream model hangs. The
# per-turn budget additionally bounds the WHOLE agent turn so a slow model
# cannot stack many long calls into a multi-minute silent hang (the root cause
# of the ~4.7-minute stall observed on 2026-07-24).
LLM_CONNECT_TIMEOUT = 10.0
LLM_READ_TIMEOUT = 90.0           # non-streaming chat / chat_with_tools
LLM_WRITE_TIMEOUT = 30.0
LLM_POOL_TIMEOUT = 10.0
LLM_STREAM_READ_TIMEOUT = 120.0   # streaming generation: tolerate slow token cadence
LLM_TURN_BUDGET_SECONDS = 180.0   # wall-clock cap for all non-stream LLM calls in a turn

# Per-conversation deadline (absolute monotonic timestamp) armed by the chat
# producer via ``set_llm_deadline``. ``None`` means no budget is enforced
# (e.g. cron subgraphs that run unattended).
_LLM_DEADLINE: "contextvars.ContextVar[float | None]" = contextvars.ContextVar(
    "ragclaw_llm_deadline", default=None
)


# Bare error code surfaced to the frontend via the errors.backendErrorCodes
# i18n map (zh-CN / en-US). Follows the project convention: the backend throws a
# CODE and the frontend localizes it; raw text is passed through only for codes
# that have no mapping.
LLM_BUDGET_EXCEEDED_CODE = "LLM_BUDGET_EXCEEDED"


class LLMBudgetExceeded(Exception):
    """Raised when the per-turn LLM wall-clock budget is exceeded.

    The exception message is the bare error CODE (``LLM_BUDGET_EXCEEDED``) so the
    chat SSE error path can be localized via ``errors.backendErrorCodes`` instead
    of hard-coding a bilingual string here.
    """


def set_llm_deadline(budget_seconds: float = LLM_TURN_BUDGET_SECONDS) -> None:
    """Arm the per-turn LLM time budget (absolute deadline = now + budget)."""
    _LLM_DEADLINE.set(time.monotonic() + budget_seconds)


def clear_llm_deadline() -> None:
    _LLM_DEADLINE.set(None)


def _check_llm_budget() -> None:
    """Raise ``LLMBudgetExceeded`` if the per-turn budget has been exceeded."""
    deadline = _LLM_DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        logger.warning("LLM per-turn budget exceeded (deadline=%.1f); aborting", deadline)
        raise LLMBudgetExceeded(LLM_BUDGET_EXCEEDED_CODE)


def _non_stream_timeout() -> "httpx.Timeout":
    """Tighter timeout for non-streaming calls so a hung model fails fast."""
    return httpx.Timeout(
        connect=LLM_CONNECT_TIMEOUT,
        read=LLM_READ_TIMEOUT,
        write=LLM_WRITE_TIMEOUT,
        pool=LLM_POOL_TIMEOUT,
    )


# Provider-side prompt cache accounting. Collected for observability (logged
# when a cache hit/creation is observed); not yet exposed via the stats API.
_llm_cache_totals = {
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "calls": 0,
}


def _wrap_system_with_cache(messages: list[dict]) -> list[dict]:
    """Anthropic / Aliyun explicit cache: mark the system message as a cache breakpoint.

    Converts a string system content into a content-block array carrying
    ``cache_control: {"type": "ephemeral"}``. Other platforms ignore the extra
    field, so a single message structure works across all four platforms.
    """
    wrapped: list[dict] = []
    for m in messages:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            wrapped.append({
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": m["content"],
                    "cache_control": {"type": "ephemeral"},
                }],
            })
        else:
            wrapped.append(m)
    return wrapped


def _apply_cache_adapter(body: dict, headers: dict, platform: str, conversation_id: str | None) -> None:
    """Mutate the request body/headers to enable the resolved platform's prompt cache.

    - anthropic / qwen: wrap the system message with a ``cache_control`` breakpoint
    - tencent: add ``prompt_cache_key`` (conversation-scoped) + ``X-Session-ID`` header
    - openai / ollama: no changes required
    """
    if platform in ("anthropic", "qwen"):
        body["messages"] = _wrap_system_with_cache(body.get("messages", []))
    elif platform == "tencent":
        if conversation_id:
            body["prompt_cache_key"] = conversation_id
            headers["X-Session-ID"] = conversation_id
    # openai / ollama: nothing to add


def _record_usage(usage: dict | None) -> None:
    """Account for provider-side prompt cache tokens (cross-platform, best-effort)."""
    if not usage:
        return
    read = (
        usage.get("cache_read_input_tokens")
        or usage.get("prompt_cache_hit_tokens")
        or usage.get("cached_tokens")
        or 0
    )
    creation = usage.get("cache_creation_input_tokens") or 0
    _llm_cache_totals["cache_read_tokens"] += read
    _llm_cache_totals["cache_creation_tokens"] += creation
    _llm_cache_totals["calls"] += 1
    if read or creation:
        logger.info("LLM prompt cache usage: read=%s creation=%s", read, creation)


class LLMClient:
    """Unified interface for different LLM providers (OpenAI-compatible API)."""

    def __init__(self):
        # Default timeout favours streaming generation (long read window).
        # Non-streaming calls pass an explicit tighter timeout via
        # ``_non_stream_timeout()`` so a hung model fails fast instead of
        # blocking for 120s.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(
            connect=LLM_CONNECT_TIMEOUT,
            read=LLM_STREAM_READ_TIMEOUT,
            write=LLM_WRITE_TIMEOUT,
            pool=LLM_POOL_TIMEOUT,
        ))

    @property
    def base_url(self) -> str:
        return config_manager.base_url.rstrip("/")

    @property
    def model(self) -> str:
        return config_manager.model

    @property
    def api_key(self) -> str:
        return config_manager.api_key

    async def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        conversation_id: str | None = None,
    ) -> str:
        """Non-streaming chat completion.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
            temperature: Override default temperature
            max_tokens: Override default max tokens
            conversation_id: Conversation-scoped id (enables Tencent TokenHub cache key)

        Returns:
            The full response text
        """
        temp = temperature if temperature is not None else config_manager.temperature
        max_tok = max_tokens if max_tokens is not None else config_manager.max_tokens

        platform = config_manager.platform
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": False,
        }
        _apply_cache_adapter(body, headers, platform, conversation_id)

        url = f"{self.base_url}/chat/completions"

        logger.info("chat request: model=%s platform=%s messages=%d temp=%s max_tokens=%s",
                    self.model, platform, len(messages), temp, max_tok)
        _check_llm_budget()
        response = await self._client.post(
            url, headers=headers, json=body, timeout=_non_stream_timeout())
        if response.status_code != 200:
            logger.error("chat error %d: %s", response.status_code, response.text[:1000])
        response.raise_for_status()

        data = response.json()
        _record_usage(data.get("usage"))
        return data["choices"][0]["message"]["content"]

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion.

        Yields text tokens one at a time. For platforms that support it
        (openai / anthropic / qwen) a final usage chunk is parsed to account for
        prompt-cache tokens.
        """
        temp = temperature if temperature is not None else config_manager.temperature
        max_tok = max_tokens if max_tokens is not None else config_manager.max_tokens

        platform = config_manager.platform
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": True,
        }
        # OpenAI-style terminal usage chunk (ignored by platforms that don't support it).
        if platform in ("openai", "anthropic", "qwen"):
            body["stream_options"] = {"include_usage": True}
        _apply_cache_adapter(body, headers, platform, conversation_id)

        url = f"{self.base_url}/chat/completions"

        async with self._client.stream("POST", url, headers=headers, json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        # Terminal usage chunk (e.g. OpenAI): empty choices, usage present.
                        if data.get("usage") is not None and not data.get("choices"):
                            _record_usage(data["usage"])
                            continue
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict = "auto",
        conversation_id: str | None = None,
    ) -> dict:
        """Non-streaming chat with tool calling support.

        Args:
            messages: Chat messages
            tools: List of tool definitions in OpenAI function-calling format
            temperature: Override default temperature
            max_tokens: Override default max tokens
            tool_choice: "auto" | "required" | "none", OR a named-tool dict
                {"type": "function", "function": {"name": "<tool>"}} to force a
                specific tool. TokenHub/MiniMax-M2.5 does NOT support "required"
                (400/502), but the named-tool dict form IS supported.
            conversation_id: Conversation-scoped id (enables Tencent TokenHub cache key)

        Returns:
            dict with:
                content: str — text response (may be empty if tool_calls present)
                tool_calls: list[dict] | None — tool calls to execute
                    Each: {id, type: "function", function: {name, arguments}}
                finish_reason: str — "stop" | "tool_calls"
        """
        temp = temperature if temperature is not None else config_manager.temperature
        max_tok = max_tokens if max_tokens is not None else config_manager.max_tokens

        platform = config_manager.platform
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "stream": False,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        _apply_cache_adapter(body, headers, platform, conversation_id)

        url = f"{self.base_url}/chat/completions"

        # ── Debug logging: print FULL request body for TokenHub compatibility diagnosis ──
        logger.info("chat_with_tools request: model=%s platform=%s tool_choice=%s tools_count=%d messages=%d",
                    self.model, platform, tool_choice, len(tools), len(messages))
        logger.info("chat_with_tools FULL request body: %s",
                    json.dumps(body, ensure_ascii=False))

        _check_llm_budget()
        response = await self._client.post(
            url, headers=headers, json=body, timeout=_non_stream_timeout())

        # ── Debug logging: print FULL error response on non-200 ──
        if response.status_code != 200:
            logger.error("chat_with_tools error %d FULL response: %s",
                         response.status_code, response.text)
            logger.error("chat_with_tools FULL request that failed: %s",
                         json.dumps(body, ensure_ascii=False))
        response.raise_for_status()

        data = response.json()
        choice = data["choices"][0]
        message = choice.get("message", {})

        _record_usage(data.get("usage"))

        return {
            "content": message.get("content", "") or "",
            "tool_calls": message.get("tool_calls"),
            "finish_reason": choice.get("finish_reason", "stop"),
        }

    async def close(self):
        await self._client.aclose()


# Singleton
llm_client = LLMClient()

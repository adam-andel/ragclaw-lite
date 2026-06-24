"""LLM client abstraction layer supporting multiple providers."""

import json
from typing import AsyncGenerator

import httpx

from app.services.config_manager import config_manager


class LLMClient:
    """Unified interface for different LLM providers (OpenAI-compatible API)."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=120.0)

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
    ) -> str:
        """Non-streaming chat completion.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
            temperature: Override default temperature
            max_tokens: Override default max tokens

        Returns:
            The full response text
        """
        temp = temperature if temperature is not None else config_manager.temperature
        max_tok = max_tokens if max_tokens is not None else config_manager.max_tokens

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

        url = f"{self.base_url}/chat/completions"

        response = await self._client.post(url, headers=headers, json=body)
        response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion.

        Yields text tokens one at a time.
        """
        temp = temperature if temperature is not None else config_manager.temperature
        max_tok = max_tokens if max_tokens is not None else config_manager.max_tokens

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
    ) -> dict:
        """Non-streaming chat with tool calling support.

        Args:
            messages: Chat messages
            tools: List of tool definitions in OpenAI function-calling format
            temperature: Override default temperature
            max_tokens: Override default max tokens

        Returns:
            dict with:
                content: str — text response (may be empty if tool_calls present)
                tool_calls: list[dict] | None — tool calls to execute
                    Each: {id, type: "function", function: {name, arguments}}
                finish_reason: str — "stop" | "tool_calls"
        """
        temp = temperature if temperature is not None else config_manager.temperature
        max_tok = max_tokens if max_tokens is not None else config_manager.max_tokens

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
            "tool_choice": "auto",
        }

        url = f"{self.base_url}/chat/completions"

        response = await self._client.post(url, headers=headers, json=body)
        response.raise_for_status()

        data = response.json()
        choice = data["choices"][0]
        message = choice.get("message", {})

        return {
            "content": message.get("content", "") or "",
            "tool_calls": message.get("tool_calls"),
            "finish_reason": choice.get("finish_reason", "stop"),
        }

    async def close(self):
        await self._client.aclose()


# Singleton
llm_client = LLMClient()

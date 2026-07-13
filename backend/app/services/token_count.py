"""Approximate LLM prompt token counting using tiktoken (cl100k_base).

Used to surface the size of the full request payload sent to the LLM
(system prompt + conversation history + RAG context + memory + tool
definitions + user query). cl100k_base is a close approximation for most
chat models; for some non-OpenAI models the count is an estimate.
"""

import tiktoken

_ENCODER = None


def _get_encoder():
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def count_text_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_get_encoder().encode(text))


def count_messages_tokens(messages: list[dict]) -> int:
    """Approximate prompt token count following the OpenAI chat convention:

    - ~4 tokens of overhead per message
    - ~3 tokens of reply-priming overhead for the whole request
    - plus content tokens for each message (string or content-block list)
    - plus tokens for any tool_calls (name + arguments)
    """
    if not messages:
        return 0
    total = 0
    for m in messages:
        total += 4  # per-message overhead
        content = m.get("content")
        if isinstance(content, str):
            total += count_text_tokens(content)
        elif isinstance(content, list):
            # Anthropic / Aliyun cache wrapper: list of content blocks
            for block in content:
                if isinstance(block, dict):
                    total += count_text_tokens(block.get("text", ""))
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            total += count_text_tokens(fn.get("name", ""))
            total += count_text_tokens(fn.get("arguments", ""))
    total += 3  # reply priming
    return total

# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Approximate LLM prompt token counting using tiktoken (cl100k_base).

Used to surface the size of the full request payload sent to the LLM
(system prompt + conversation history + RAG context + memory + tool
definitions + user query). cl100k_base is a close approximation for most
chat models; for some non-OpenAI models the count is an estimate.
"""

import json

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


# OpenAI adds a small fixed overhead per function-tool entry (role/name wrapping
# tokens) on top of the serialized schema. We deliberately over-estimate slightly
# so the budget guard errs toward safety rather than risking a 400.
TOOL_PER_ITEM_OVERHEAD = 7


def count_tools_tokens(tools: list) -> int:
    """Approximate token cost of the ``tools=`` payload sent to the LLM.

    Tool definitions are delivered as a separate ``tools`` parameter, NOT inside
    the messages array, so ``count_messages_tokens`` never accounts for them --
    yet they consume the same input budget. We serialize each tool back to the
    wire form the provider actually receives and count it with the same encoder,
    keeping the estimate consistent with the rest of the pipeline.
    """
    if not tools:
        return 0
    total = 0
    for t in tools:
        fn = t.get("function", {}) if isinstance(t, dict) else {}
        wire = {
            "type": "function",
            "function": {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            },
        }
        try:
            serialized = json.dumps(wire, ensure_ascii=False)
        except TypeError:
            serialized = str(wire)
        total += count_text_tokens(serialized)
        total += TOOL_PER_ITEM_OVERHEAD
    return total

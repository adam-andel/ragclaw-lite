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
"""Unit tests for approximate LLM prompt token counting (token_count.py).

Covers:
- text token counting (incl. empty input)
- message list counting following the OpenAI chat convention
  (per-message overhead, reply priming, content blocks, tool_calls)
- determinism / agreement with tiktoken cl100k_base
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tiktoken

from app.services.token_count import count_messages_tokens, count_text_tokens

_ENC = tiktoken.get_encoding("cl100k_base")


def _expected_text(text: str) -> int:
    return len(_ENC.encode(text)) if text else 0


# ---------------------------------------------------------------------------
# Text counting
# ---------------------------------------------------------------------------

class TestCountTextTokens:
    def test_empty_returns_zero(self):
        assert count_text_tokens("") == 0
        assert count_text_tokens(None) == 0  # type: ignore[arg-type]

    def test_nonempty_positive(self):
        n = count_text_tokens("Hello, world!")
        assert n == _expected_text("Hello, world!")
        assert n > 0

    def test_chinese_text(self):
        n = count_text_tokens("你好，世界")
        assert n == _expected_text("你好，世界")
        assert n > 0

    def test_matches_tiktoken_exactly(self):
        for s in ["", "A", "The quick brown fox jumps over the lazy dog.",
                  "企业知识库问答系统", "mix 混合 content 123"]:
            assert count_text_tokens(s) == _expected_text(s)


# ---------------------------------------------------------------------------
# Message list counting
# ---------------------------------------------------------------------------

class TestCountMessagesTokens:
    def test_empty_list_returns_zero(self):
        assert count_messages_tokens([]) == 0

    def test_single_user_message_overhead(self):
        """One message: 4 (overhead) + content + 3 (reply priming)."""
        content = "What is RAGClaw?"
        msgs = [{"role": "user", "content": content}]
        expected = 4 + count_text_tokens(content) + 3
        assert count_messages_tokens(msgs) == expected

    def test_system_plus_user(self):
        sys_p = "You are a helpful assistant."
        q = "Explain RAG."
        msgs = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": q},
        ]
        expected = 4 + count_text_tokens(sys_p) + 4 + count_text_tokens(q) + 3
        assert count_messages_tokens(msgs) == expected

    def test_content_as_block_list_anthropic_style(self):
        """Anthropic/Aliyun content-block lists carry a 'text' field."""
        blocks = [
            {"type": "text", "text": "First paragraph."},
            {"type": "text", "text": "Second paragraph."},
        ]
        msgs = [{"role": "assistant", "content": blocks}]
        expected = 4 + count_text_tokens("First paragraph.") + count_text_tokens(
            "Second paragraph."
        ) + 3
        assert count_messages_tokens(msgs) == expected

    def test_tool_calls_counted(self):
        """tool_calls contribute name + arguments tokens."""
        msgs = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {"name": "search_docs", "arguments": '{"query": "RAGClaw"}'},
            }],
        }]
        expected = (
            4
            + count_text_tokens("")
            + count_text_tokens("search_docs")
            + count_text_tokens('{"query": "RAGClaw"}')
            + 3
        )
        assert count_messages_tokens(msgs) == expected

    def test_more_content_more_tokens(self):
        short = [{"role": "user", "content": "hi"}]
        long = [{"role": "user", "content": "hi " * 200}]
        assert count_messages_tokens(long) > count_messages_tokens(short)

    def test_deterministic(self):
        msgs = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Question?"},
            {"role": "assistant", "content": [{"type": "text", "text": "Answer."}]},
        ]
        assert count_messages_tokens(msgs) == count_messages_tokens(msgs)

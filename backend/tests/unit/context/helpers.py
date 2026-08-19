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
"""Shared builders and assertion helpers for the context-compression suite.

Kept out of ``conftest.py`` so test modules can import them directly: pytest's
prepend import mode puts this directory on ``sys.path`` (no ``__init__.py``
here), while ``conftest`` itself is loaded through the plugin machinery and is
not meant to be imported by name.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import context_budget as cb  # noqa: E402
from app.services.config_manager import config_manager  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_ALIAS = {
    "window": "llm_context_window",
    "max_tokens": "llm_max_tokens",
    "lang": "prompt_language",
}


def set_cfg(**kw) -> None:
    """Override config keys for the current test.

    Accepts the friendly aliases used throughout the suite (``window``,
    ``max_tokens``, ``lang``) as well as raw config keys. Clears the prefix
    memoization so the next estimate reflects the new configuration.
    """
    for k, v in kw.items():
        config_manager._config[_ALIAS.get(k, k)] = v
    cb._prefix_tokens_cached.cache_clear()


# ---------------------------------------------------------------------------
# Data builders
# ---------------------------------------------------------------------------

def make_history(n_rounds: int, *, words: int = 40, start_seq: int = 0) -> list[dict]:
    """``n_rounds`` user/assistant pairs as plain dicts (the request-path shape).

    Content is deterministic and self-identifying so drop-order assertions can
    name the exact message that should have survived.
    """
    out: list[dict] = []
    seq = start_seq
    for i in range(n_rounds):
        out.append({"role": "user", "content": f"Q{i} " + f"q{i}word " * words, "seq": seq})
        seq += 1
        out.append({"role": "assistant", "content": f"A{i} " + f"a{i}word " * words, "seq": seq})
        seq += 1
    return out


def make_tool_payload(n: int, *, words: int = 30) -> list[dict]:
    """``n`` assistant(tool_calls)+tool(result) pairs -- the ``messages`` payload
    kind. Pairs must be dropped as units; an orphan makes the provider 400."""
    out: list[dict] = []
    for i in range(n):
        out.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q":"x"}'},
                    }
                ],
            }
        )
        out.append(
            {
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": f"RESULT{i} " + f"r{i}word " * words,
            }
        )
    return out


def joined(n_chunks: int, delim: str, *, words: int = 20, tag: str = "C") -> str:
    """Delimiter-joined blob (RAG chunks / memory recall / L0 segments)."""
    return delim.join(f"{tag}{i} " + f"{tag.lower()}{i}word " * words for i in range(n_chunks))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def simple_build_messages(summary, history, rag, payload, query, mem):
    """Minimal ``build_messages`` stand-in with the same contract as the real
    assemblers: every non-empty component contributes at least one message.

    Deliberately prefix-free so token math in a test stays readable instead of
    being dominated by the ~2000-token production system prompt.
    """
    msgs: list[dict] = []
    if summary:
        msgs.append({"role": "system", "content": f"SUMMARY\n{summary}"})
    if rag:
        msgs.append({"role": "system", "content": f"RAG\n{rag}"})
    if mem:
        msgs.append({"role": "system", "content": f"MEM\n{mem}"})
    msgs.extend(history or [])
    msgs.extend(payload or [])
    msgs.append({"role": "user", "content": query})
    return msgs


def final_gen_prefix_tokens(**state_over) -> int:
    """Token cost of the REAL final-generation prefix with an empty question.

    Backs the N17 regression (plan E6): Gate A's floor must equal this. Imported
    lazily because ``agent_graph`` pulls in the whole agent stack.

    Uses the EXACT state keys ``build_generation_messages`` reads
    (``conversation_summary`` / ``conversation_history`` / ``tool_results`` /
    ``query`` / ``kb_prompt`` / ``user_memory`` / ``pinned_instruction`` /
    ``active_skill``).
    """
    from app.services.agent_graph import ragclaw_agent_graph
    from app.services.token_count import count_messages_tokens

    state = {
        "query": "",
        "conversation_summary": "",
        "conversation_history": [],
        "rag_context": "",
        "memory_context": "",
        "tool_results": [],
        "kb_prompt": "",
        "user_memory": "",
        "pinned_instruction": "",
        "active_skill": None,
    }
    state.update(state_over)
    msgs, _ = ragclaw_agent_graph.build_generation_messages(state)
    return count_messages_tokens(msgs)


def roles_of(messages) -> list[str]:
    return [m.get("role") for m in messages]


def assert_no_orphan_tool_messages(payload) -> None:
    """Every ``tool`` result must be answered by a preceding assistant tool_call
    with a matching id, and every announced call must have its result.

    An orphan on either side is an immediate provider 400, which is exactly the
    failure mode plan invariant I8 exists to prevent.
    """
    announced: list[str] = []
    for m in payload:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            announced.extend(tc["id"] for tc in m["tool_calls"])
    returned = [m.get("tool_call_id") for m in payload if m.get("role") == "tool"]

    assert sorted(announced) == sorted(returned), (
        f"orphan tool messages: announced={announced} returned={returned}"
    )

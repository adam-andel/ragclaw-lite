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
"""Layer H — the pinned instruction as a first-class sacred prefix.

A pin is a per-conversation standing order. The design rule (I10) is absolute:
it is injected into EVERY turn, it is never folded into ``summary_text``, and
``fit_assembly_context`` may never trim it — no matter how badly the rest of the
payload overflows. That makes it the one component whose survival has to be
asserted byte-for-byte rather than "approximately present".

Coverage map (v3 plan):
  H1  injected by BOTH assemblers (final-generation + tool-decision)
  H2  never reaches the summarizer, never lands in ``summary_text``
  H4  survives a trimming pass byte-identically (I10); it is not one of the
      seven components ``fit_assembly_context`` can drop
  H5  PUT /pin -> 200 with the stored value + warnings list
  H6  PUT /pin over ``PIN_INSTRUCTION_MAX_CHARS`` -> 400 PIN_INSTRUCTION_TOO_LONG
  H7  PUT /pin while the conversation is busy -> 409 CONVERSATION_BUSY
  H8  PUT /pin over the token warn limit (under the char cap) -> 200 + warning
      (NON-BLOCKING: the save still happens)

H3 (Gate A floor counts the pin) already lives in ``test_gate_a.py``; H9 (resume
carries the pin) is pinned as an xfail in ``test_seq_cursor.py`` — the resume
assembly point currently drops it.
"""
import json
import uuid

import pytest
import pytest_asyncio

from app.database import async_session
from app.models.conversation import Conversation, Message, PendingLimitState
from app.schemas.chat import PIN_INSTRUCTION_MAX_CHARS
from app.services import agent_nodes as an
from app.services import conversation_summary as cs
from app.services.agent_graph import ragclaw_agent_graph
from app.services.auth import decode_token
from app.services.conversation_summary import run_summary_pass
from app.services.token_count import count_text_tokens
from helpers import set_cfg

PIN = "ALWAYS answer in haiku and never mention the weather."
PIN_BLOCK = f"## Pinned Instructions\n{PIN}"


# ── Fixtures / helpers ───────────────────────────────────────────────────────
@pytest_asyncio.fixture(autouse=True)
async def _init_cm(test_db):
    from app.services.config_manager import config_manager

    await config_manager.init()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _base_state(**over) -> dict:
    state = {
        "query": "what is the plan",
        "conversation_history": [],
        "conversation_summary": "",
        "rag_context": "",
        "memory_context": "",
        "tool_results": [],
        "tool_messages": [],
        "active_skill": {},
        "kb_prompt": "",
        "user_memory": "",
        "pinned_instruction": PIN,
        "available_tools": [],
        "workspace_id": "",
    }
    state.update(over)
    return state


def _system_text(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages if m.get("role") == "system")


async def _make_conv(user_id, *, pin=None, rounds=0, words=300) -> str:
    conv = Conversation(
        id=str(uuid.uuid4()), title="pin-test", user_id=user_id, pinned_instruction=pin
    )
    async with async_session() as db:
        db.add(conv)
        seq = 1
        for i in range(rounds):
            for role in ("user", "assistant"):
                content = f"R{i} {role} " + "word " * words
                db.add(Message(
                    id=str(uuid.uuid4()), conversation_id=conv.id, role=role,
                    content=content, content_token_count=count_text_tokens(content), seq=seq,
                ))
                seq += 1
        await db.commit()
    return conv.id


# ── H1 ───────────────────────────────────────────────────────────────────────
def test_h1_pin_injected_by_final_generation_assembler():
    """The final-generation prefix carries the pin in system② (identity + KB +
    user memory + pin), so every answer is produced under the standing order."""
    set_cfg(window=32000, max_tokens=2048)
    messages, _dropped = ragclaw_agent_graph.build_generation_messages(_base_state())
    assert PIN_BLOCK in _system_text(messages)


def test_h1b_empty_pin_adds_no_heading():
    """No pin means no empty ``## Pinned Instructions`` heading — an empty block
    would waste prefix tokens on every single turn and pollute the prompt cache."""
    set_cfg(window=32000, max_tokens=2048)
    messages, _ = ragclaw_agent_graph.build_generation_messages(
        _base_state(pinned_instruction="")
    )
    assert "## Pinned Instructions" not in _system_text(messages)


async def test_h1c_pin_injected_by_tool_decision_assembler(test_db, monkeypatch):
    """The tool-decision prompt is a SEPARATE assembler. A pin that only reached
    the final answer would let the model pick tools in violation of the standing
    order, so this path must inject it too.
    """
    set_cfg(window=32000, max_tokens=2048)
    captured = {}

    async def _fake_chat_with_tools(messages, tools, tool_choice, **kwargs):
        captured["messages"] = messages
        return {"tool_calls": None, "content": ""}

    monkeypatch.setattr(an, "_chat_with_tools_resilient", _fake_chat_with_tools)

    state = _base_state(
        available_tools=[{
            "type": "function",
            "function": {"name": "read_file", "description": "read a file", "parameters": {}},
        }],
        kb_id="kb-1",
        tool_round=0,
        tool_round_quota=5,
    )
    await an.tool_decision_node(state)
    assert "messages" in captured, "tool-decision LLM was never called"
    assert PIN_BLOCK in _system_text(captured["messages"])


# ── H2 ───────────────────────────────────────────────────────────────────────
async def test_h2_pin_never_reaches_the_summarizer(test_db, monkeypatch):
    """The summarizer is fed conversation ROUNDS only (``_uni_rounds`` over DB
    messages). The pin lives on the conversation row, so it can neither be sent
    to the summarizing model nor end up inside ``summary_text`` — otherwise a pin
    edit would leave a stale copy frozen in the summary forever.
    """
    set_cfg(window=8000, max_tokens=1024)
    prompts = []

    class _CapturingLLM:
        async def chat(self, messages, **kwargs):
            prompts.append(json.dumps(messages, ensure_ascii=False))
            return "FOLDED"

    monkeypatch.setattr(cs, "llm_client", _CapturingLLM())
    cid = await _make_conv(None, pin=PIN, rounds=6, words=300)  # >=5 rounds -> real fold

    assert await run_summary_pass(cid, blocking=True) is True
    assert prompts, "the summarizer never ran"
    for p in prompts:
        assert PIN not in p, "pin leaked into the summarization prompt"

    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.summary_msg_seq > 0  # a genuine fold happened
        assert PIN not in (conv.summary_text or "")
        # ...and the pin itself is untouched by compression.
        assert conv.pinned_instruction == PIN


# ── H4 ───────────────────────────────────────────────────────────────────────
def test_h4_pin_survives_a_trimming_pass_byte_identically():
    """Force ``fit_assembly_context`` to actually drop something, then prove the
    pin block is byte-identical to the untrimmed run.

    The pin is not one of the seven trimmable components — it is baked into the
    prefix by the ``build_messages`` closure — so a trim that touched it would
    mean the sacred-prefix contract (I10) had been broken.
    """
    set_cfg(window=8000, max_tokens=1024)

    small = ragclaw_agent_graph.build_generation_messages(_base_state())[0]
    baseline_prefix = _system_text(small)
    assert PIN_BLOCK in baseline_prefix

    # Oversized RAG + history: the payload cannot fit, so the trimmer must run.
    big_state = _base_state(
        rag_context="doc " * 6000,
        conversation_history=[
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": "chatter " * 400, "seq": i + 1}
            for i in range(20)
        ],
    )
    trimmed, dropped = ragclaw_agent_graph.build_generation_messages(big_state)
    assert dropped is True, "expected the assembly guard to trim something"

    trimmed_prefix = _system_text(trimmed)
    assert PIN_BLOCK in trimmed_prefix
    # Byte-level: the whole pin block survives, not just a truncated prefix of it.
    assert trimmed_prefix.count(PIN) == baseline_prefix.count(PIN) == 1


# ── H5 ───────────────────────────────────────────────────────────────────────
async def test_h5_put_pin_ok(client, user_token):
    set_cfg(window=32000, max_tokens=2048)
    uid = decode_token(user_token)["sub"]
    cid = await _make_conv(uid)
    resp = await client.put(
        f"/api/conversations/{cid}/pin",
        json={"pinned_instruction": PIN},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pinned_instruction"] == PIN
    assert body["warnings"] == []  # a short pin is silent
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.pinned_instruction == PIN


async def test_h5b_put_empty_pin_clears_it(client, user_token):
    """Clearing writes NULL (not ""), so the assembler's ``or ""`` guard and the
    Gate A floor both see "no pin" rather than an empty heading."""
    set_cfg(window=32000, max_tokens=2048)
    uid = decode_token(user_token)["sub"]
    cid = await _make_conv(uid, pin=PIN)
    resp = await client.put(
        f"/api/conversations/{cid}/pin",
        json={"pinned_instruction": "   "},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    assert resp.json()["pinned_instruction"] == ""
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.pinned_instruction is None


# ── H6 ───────────────────────────────────────────────────────────────────────
async def test_h6_put_pin_too_long(client, user_token):
    """The character cap is an abuse guard, checked BEFORE the conversation is
    even loaded — a 10 MB body must not reach the DB."""
    set_cfg(window=32000, max_tokens=2048)
    uid = decode_token(user_token)["sub"]
    cid = await _make_conv(uid)
    resp = await client.put(
        f"/api/conversations/{cid}/pin",
        json={"pinned_instruction": "x" * (PIN_INSTRUCTION_MAX_CHARS + 1)},
        headers=_auth(user_token),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "PIN_INSTRUCTION_TOO_LONG"
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.pinned_instruction is None  # nothing written


# ── H7 ───────────────────────────────────────────────────────────────────────
async def test_h7_put_pin_busy_conflict(client, user_token):
    """Editing the sacred prefix mid-run would change the prompt underneath an
    in-flight turn, so a suspended/streaming conversation rejects the write."""
    set_cfg(window=32000, max_tokens=2048)
    uid = decode_token(user_token)["sub"]
    cid = await _make_conv(uid, pin="original")
    async with async_session() as db:
        db.add(PendingLimitState(
            conversation_id=cid,
            message_id=str(uuid.uuid4()),
            snapshot_json=json.dumps({"mode": "limit"}),
        ))
        await db.commit()

    resp = await client.put(
        f"/api/conversations/{cid}/pin",
        json={"pinned_instruction": "overwritten"},
        headers=_auth(user_token),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "CONVERSATION_BUSY"
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.pinned_instruction == "original"  # untouched


# ── H8 ───────────────────────────────────────────────────────────────────────
async def test_h8_put_pin_soft_warning_is_non_blocking(client, user_token):
    """Over the token warn limit but under the char cap: the author gets a
    structured ``PROMPT_FIELD_TOO_LARGE`` warning AND the save still succeeds.

    Window is squeezed to 2000 so the 10%% field limit (200 tok) is crossed by a
    pin that is still comfortably under the 2000-character abuse cap.
    """
    set_cfg(window=2000, max_tokens=256)
    uid = decode_token(user_token)["sub"]
    cid = await _make_conv(uid)

    long_pin = "word " * 300  # 1500 chars, ~300 tok > 200 limit
    assert len(long_pin) < PIN_INSTRUCTION_MAX_CHARS

    resp = await client.put(
        f"/api/conversations/{cid}/pin",
        json={"pinned_instruction": long_pin},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200, "the soft check must never block the save"
    warns = resp.json()["warnings"]
    assert len(warns) == 1
    assert warns[0]["code"] == "PROMPT_FIELD_TOO_LARGE"
    assert warns[0]["params"]["field"] == "pinned_instruction"
    assert warns[0]["params"]["tok"] > warns[0]["params"]["limit"]
    # Persisted despite the warning.
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.pinned_instruction == long_pin.strip()


async def test_h8b_get_pin_roundtrip(client, user_token):
    set_cfg(window=32000, max_tokens=2048)
    uid = decode_token(user_token)["sub"]
    cid = await _make_conv(uid, pin=PIN)
    resp = await client.get(f"/api/conversations/{cid}/pin", headers=_auth(user_token))
    assert resp.status_code == 200
    assert resp.json()["pinned_instruction"] == PIN

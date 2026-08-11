"""Layer J — API / SSE integration for the context-compression module.

Exercises the HTTP surface (FastAPI + SSE) with mocked external dependencies:
  * ``config_manager`` is initialized (the ``client`` fixture does not run app
    startup, so JWT + DB-backed settings must be seeded per test).
  * the LLM is mocked via ``cs.llm_client`` for any path that summarizes
    (compact success); Gate A paths need no LLM.

Coverage map (v3 plan):
  J1  POST /api/chat/stream -> SSE ``error`` event ``QUERY_TOO_LONG``
  J2  POST /api/chat/stream -> SSE ``error`` event ``CONTEXT_PREFIX_TOO_LARGE``
  J7  DELETE /api/conversations/{id}/summary/segments (404 / 400 / 409 / ok)
  J8  POST /api/conversations/{id}/compact (NOTHING_TO_COMPACT / HISTORY_TOO_SHORT / ok)
  J9  GET /api/conversations/{id} surfaces the persistent summary state
"""
import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.database import async_session
from app.models.conversation import Conversation, Message, PendingLimitState
from app.services import conversation_summary as cs
from app.services.auth import decode_token
from app.services.conversation_summary import SUMMARY_SEGMENT_DELIM, segment_thresholds
from app.services.token_count import count_text_tokens
from helpers import set_cfg


# ── Fixtures / helpers ───────────────────────────────────────────────────────
@pytest_asyncio.fixture(autouse=True)
async def _init_cm(test_db):
    """Seed ConfigManager (JWT secret + settings) — the app does this at
    startup, but the ``client`` ASGI transport does not trigger lifespan."""
    from app.services.config_manager import config_manager

    await config_manager.init()


class FakeLLM:
    """Stand-in for ``conversation_summary.llm_client`` — one FOLDED line per unit."""

    async def chat(self, messages, **kwargs):
        return "FOLDED"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _user_id(token: str) -> str:
    return decode_token(token)["sub"]


def _del(client, url: str, payload: dict, token: str):
    """DELETE with a JSON body.

    This httpx build strips ``content``/``json`` from the convenience
    ``delete()`` method, so route through the generic ``request()`` which
    keeps them.
    """
    return client.request(
        "DELETE",
        url,
        content=json.dumps(payload),
        headers={**_auth(token), "Content-Type": "application/json"},
    )


def _parse_sse(text: str) -> list[dict]:
    """Yield parsed JSON payloads from an SSE body (``data: {...}`` blocks)."""
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        data_lines = [
            line[len("data:"):].lstrip()
            for line in block.split("\n")
            if line.startswith("data:")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


async def _make_conv(user_id: str, *, summary_text=None, summary_msg_seq=0, rounds=0, words=200):
    """Create a conversation owned by ``user_id``, optionally with N rounds of messages."""
    conv = Conversation(
        id=str(uuid.uuid4()),
        title="ctx-test",
        user_id=user_id,
        summary_text=summary_text,
        summary_msg_seq=summary_msg_seq,
    )
    async with async_session() as db:
        db.add(conv)
        seq = 1
        for i in range(rounds):
            for role in ("user", "assistant"):
                content = f"R{i} {role} " + "word " * words
                db.add(
                    Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conv.id,
                        role=role,
                        content=content,
                        content_token_count=count_text_tokens(content),
                        seq=seq,
                    )
                )
                seq += 1
        await db.commit()
    return conv.id


# ── Shared mocks for the heavy SSE generation path ──────────────────────────
class _FakeGraph:
    """Stand-in for ``ragclaw_agent_graph`` — skips RAG/tool execution.

    Returns a state with no ``pending_limit`` / ``cache_hit`` so the stream
    proceeds straight to LLM generation. ``build_generation_messages`` hands back
    a tiny payload so ``fit_assembly_context`` has nothing to do.
    """

    async def run(self, state):
        return {
            **state,
            "final_answer": "",
            "citations": [],
            "tool_messages": [],
            "context_breakdown": None,
            "retrieval_ms": 0,
            "pending_limit": None,
            "cache_hit": False,
            "agent_steps": [],
        }

    def build_generation_messages(self, state):
        return ([{"role": "user", "content": "x"}], False)


class _GenLLM:
    """Generation LLM — yields a single token (happy path)."""

    async def chat_stream(self, messages, **kwargs):
        yield "answer"


class _OverflowLLM:
    """Generation LLM — raises a provider-style context-overflow error.

    Must be an async generator (``yield`` present) so the stream's ``async for``
    accepts it; the ``raise`` fires on first iteration, before any ``yield``.
    """

    async def chat_stream(self, messages, **kwargs):
        raise RuntimeError("maximum context length exceeded for this model")
        yield  # noqa: unreachable; keeps this an async generator


# ── J1 ───────────────────────────────────────────────────────────────────────
async def test_j1_query_too_long_sse(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())  # not strictly needed for Gate A
    uid = _user_id(user_token)
    cid = await _make_conv(uid)
    resp = await client.post(
        "/api/chat/stream",
        json={"query": "word " * 20000, "kb_id": "x", "conversation_id": cid},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    errors = [e for e in _parse_sse(resp.text) if e.get("type") == "error"]
    assert errors, "expected an SSE error event"
    assert errors[0]["message"] == "QUERY_TOO_LONG"


# ── J2 ───────────────────────────────────────────────────────────────────────
async def test_j2_prefix_too_large_sse(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)

    async def _huge_kb(kb_id):
        return "word " * 20000  # prefix alone overflows the 8k budget

    # Patch the name chat.py calls for the KB instruction prompt.
    monkeypatch.setattr("app.routers.chat.get_kb_prompt", _huge_kb)
    uid = _user_id(user_token)
    cid = await _make_conv(uid)
    resp = await client.post(
        "/api/chat/stream",
        json={"query": "hello", "kb_id": "x", "conversation_id": cid},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    errors = [e for e in _parse_sse(resp.text) if e.get("type") == "error"]
    assert errors, "expected an SSE error event"
    assert errors[0]["message"] == "CONTEXT_PREFIX_TOO_LARGE"


# ── J7 ───────────────────────────────────────────────────────────────────────
async def test_j7_delete_segment_ok(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    uid = _user_id(user_token)
    text = f"SEG_A{SUMMARY_SEGMENT_DELIM}SEG_B"
    cid = await _make_conv(uid, summary_text=text, summary_msg_seq=5)
    resp = await _del(
        client, f"/api/conversations/{cid}/summary/segments",
        {"segment_text": "SEG_A"}, user_token,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary_text"] == "SEG_B"
    assert body["summary_msg_seq"] == 5  # cursor intentionally untouched


async def test_j7_delete_segment_404(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    uid = _user_id(user_token)
    text = f"SEG_A{SUMMARY_SEGMENT_DELIM}SEG_B"
    cid = await _make_conv(uid, summary_text=text)
    resp = await _del(
        client, f"/api/conversations/{cid}/summary/segments",
        {"segment_text": "NOPE"}, user_token,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "SEGMENT_NOT_FOUND"


async def test_j7_delete_segment_400_empty(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    uid = _user_id(user_token)
    cid = await _make_conv(uid, summary_text="SEG_A")
    resp = await _del(
        client, f"/api/conversations/{cid}/summary/segments",
        {"segment_text": "   "}, user_token,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "EMPTY_SEGMENT"


async def test_j7_delete_segment_409_busy(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    uid = _user_id(user_token)
    cid = await _make_conv(uid, summary_text="SEG_A")
    # A pending (suspended) run makes the conversation busy -> 409.
    async with async_session() as db:
        db.add(
            PendingLimitState(
                conversation_id=cid,
                message_id=str(uuid.uuid4()),
                snapshot_json=json.dumps({"mode": "limit"}),
            )
        )
        await db.commit()
    resp = await _del(
        client, f"/api/conversations/{cid}/summary/segments",
        {"segment_text": "SEG_A"}, user_token,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "CONVERSATION_BUSY"


# ── J8 ───────────────────────────────────────────────────────────────────────
async def test_j8_compact_nothing_to_compact(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    uid = _user_id(user_token)
    cid = await _make_conv(uid, rounds=0)  # no messages -> empty tail
    resp = await client.post(
        f"/api/conversations/{cid}/compact", json={}, headers=_auth(user_token)
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "NOTHING_TO_COMPACT"


async def test_j8_compact_history_too_short(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    uid = _user_id(user_token)
    # 3 rounds (~1233 tok) < min_tok (2000) -> manual-only UX guard fires.
    cid = await _make_conv(uid, rounds=3, words=200)
    resp = await client.post(
        f"/api/conversations/{cid}/compact", json={}, headers=_auth(user_token)
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "HISTORY_TOO_SHORT"


async def test_j8_compact_ok_folds(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())
    uid = _user_id(user_token)
    # 6 rounds -> 5 considered (live excluded) -> crosses min_tok -> 1 fold.
    cid = await _make_conv(uid, rounds=6, words=300)
    resp = await client.post(
        f"/api/conversations/{cid}/compact", json={}, headers=_auth(user_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary_msg_seq"] > 0
    assert "FOLDED" in body["summary_text"]


# ── J9 ───────────────────────────────────────────────────────────────────────
async def test_j9_read_summary_state(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    uid = _user_id(user_token)
    text = f"SEG_A{SUMMARY_SEGMENT_DELIM}SEG_B"
    cid = await _make_conv(uid, summary_text=text, summary_msg_seq=7, rounds=2)
    resp = await client.get(
        f"/api/conversations/{cid}?include_messages=false", headers=_auth(user_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary_text"] == text
    assert body["summary_msg_seq"] == 7
    # min_compact_tok mirrors segment_thresholds(context_window)[0] at 8k window.
    assert body["min_compact_tok"] == segment_thresholds(8000)[0]


# ── J3 ───────────────────────────────────────────────────────────────────────
async def test_j3_sync_compress_sse(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())  # folding -> FOLDED
    monkeypatch.setattr("app.services.agent_graph.ragclaw_agent_graph", _FakeGraph())
    monkeypatch.setattr("app.services.llm_client.llm_client", _GenLLM())
    uid = _user_id(user_token)
    # 6 rounds (words=300 ~= 611 tok) -> persistent >> sync_hi (1654) -> sync fold.
    cid = await _make_conv(uid, rounds=6, words=300)
    resp = await client.post(
        "/api/chat/stream",
        json={"query": "j3-unique-sync-fold-query", "kb_id": "x", "conversation_id": cid},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    # The sync fold emits an ``agent_step`` with stage == "context_compress".
    compress = [e for e in events if e.get("type") == "agent_step" and e.get("stage") == "context_compress"]
    assert compress, "expected a context_compress agent_step event"
    # The cursor advanced in the DB.
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.summary_msg_seq > 0


# ── J6 ───────────────────────────────────────────────────────────────────────
async def test_j6_provider_overflow_localized(client, user_token, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())
    monkeypatch.setattr("app.services.agent_graph.ragclaw_agent_graph", _FakeGraph())
    monkeypatch.setattr("app.services.llm_client.llm_client", _OverflowLLM())
    uid = _user_id(user_token)
    cid = await _make_conv(uid, rounds=2, words=200)
    # Unique query: the chat endpoint memoizes responses by query, so a repeated
    # "hello" would hit J3's cached answer and never reach generation.
    resp = await client.post(
        "/api/chat/stream",
        json={"query": "j6-unique-overflow-query", "kb_id": "x", "conversation_id": cid},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    errors = [e for e in events if e.get("type") == "error"]
    assert errors, "expected an SSE error event"
    # A residual provider context-overflow is localized, never surfaced verbatim.
    assert errors[0]["message"] == "LLM_CONTEXT_EXCEEDED"
    assert "maximum context length" not in errors[0]["message"]

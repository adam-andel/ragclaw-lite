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
"""Layer K — cross-cutting invariants of the compression pipeline.

These are the properties that actually bite in production and are NOT covered by the
per-layer suites (F/J/G/H). Each test pins a plan invariant by ID:

  K1  I1  the live (newest) round is NEVER folded (end-to-end)
  K2  I2  L0 append + cursor advance happen in ONE CAS-guarded UPDATE (atomic)
  K6  I6  the request path never writes the ``summary_msg_seq`` cursor column
  K9  I9  the trimming loop always terminates (no-op -> degrade, never spin)
  K10 I10 the sacred prefix (pin/kb/user_memory/skill) survives a trim byte-for-byte,
          and an oversized fixed prefix is rejected at Gate A
  K12 I12 the history cache is correct and shape-independent (full vs tail-only)

K3/K4/K7/K8/K11/K13/K14 are intentionally NOT duplicated here (already pinned by
the F / G / H / J suites). Source is left untouched; this file is test-only.
"""
import contextlib
import random
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, func

from app.database import async_session as _db_factory
import app.database as appdb
from app.models.conversation import Conversation, Message
from app.routers import chat as chat_router
from app.services import conversation_summary as cs
from app.services.agent_graph import ragclaw_agent_graph
from app.services.token_count import count_text_tokens, count_messages_tokens
from helpers import set_cfg, make_history, make_tool_payload, joined, simple_build_messages


# ── Fixtures (mirror the other context suites) ────────────────────────────────
@pytest_asyncio.fixture(autouse=True)
async def _init_cm(test_db):
    from app.services.config_manager import config_manager

    await config_manager.init()


@pytest_asyncio.fixture(autouse=True)
async def _reset_history_cache():
    chat_router._HISTORY_CACHE.clear()
    chat_router._HISTORY_CACHE_LOCKS.clear()
    yield
    chat_router._HISTORY_CACHE.clear()
    chat_router._HISTORY_CACHE_LOCKS.clear()


# ── Local data builders ───────────────────────────────────────────────────────
async def _seed_conv(rounds: int, *, words: int = 300, cursor: int = 0,
                     user_id=None) -> str:
    """Seed a conversation with ``rounds`` user/assistant pairs (seq 1..2*rounds).

    Content carries a per-round marker (``ROUND{i}``) so a test can prove the live
    round was never folded into ``summary_text``.
    """
    conv = Conversation(id=str(uuid.uuid4()), user_id=user_id, summary_msg_seq=cursor)
    async with _db_factory() as db:
        db.add(conv)
        await db.commit()
        seq = 1
        for i in range(rounds):
            for role in ("user", "assistant"):
                content = f"ROUND{i} {role} " + "word " * words
                db.add(Message(
                    id=str(uuid.uuid4()), conversation_id=conv.id, role=role,
                    content=content, content_token_count=count_text_tokens(content), seq=seq,
                ))
                seq += 1
        await db.commit()
    return conv.id


async def _all_messages(cid: str) -> list[Message]:
    async with _db_factory() as db:
        return (await db.execute(
            select(Message).where(Message.conversation_id == cid).order_by(Message.seq)
        )).scalars().all()


@contextlib.contextmanager
def _spy_session_statements(monkeypatch):
    """Wrap ``app.database.async_session`` so every SQL statement issued on any
    session created during the ``with`` block is captured (as a compiled string),
    without changing behaviour. Used to prove which columns the request path writes.
    """
    captured: list[str] = []
    real = appdb.async_session

    class _Proxy:
        def __init__(self, real_sess):
            self._r = real_sess

        async def __aenter__(self):
            await self._r.__aenter__()
            return self

        async def __aexit__(self, *a):
            return await self._r.__aexit__(*a)

        async def execute(self, statement, *a, **k):
            try:
                captured.append(str(statement))
            except Exception:
                captured.append("<uncompilable>")
            return await self._r.execute(statement, *a, **k)

        async def get(self, *a, **k):
            return await self._r.get(*a, **k)

        async def refresh(self, *a, **k):
            return await self._r.refresh(*a, **k)

        async def commit(self, *a, **k):
            return await self._r.commit(*a, **k)

        async def add(self, *a, **k):
            return await self._r.add(*a, **k)

    def _factory():
        return _Proxy(real())

    monkeypatch.setattr(appdb, "async_session", _factory)
    try:
        yield captured
    finally:
        monkeypatch.undo()


class _EchoLLM:
    """LLM stub that echoes the prompted text back, so ``summary_text`` ends up
    containing the exact text of the rounds that were folded (making K1's "live
    round never folded" assertion meaningful)."""

    async def chat(self, messages, **kwargs):
        return messages[0]["content"].split("\n\n", 1)[-1]


def _is_conv_cursor_update(sql: str) -> bool:
    """True for an UPDATE of the ``conversations`` table that assigns the cursor
    column ``summary_msg_seq``."""
    return "UPDATE conversations" in sql and "summary_msg_seq" in sql


# ── K1: the live round is never folded (I1) ───────────────────────────────────
async def test_k1_live_round_never_folded_e2e(test_db, monkeypatch):
    """Across a full compaction run, the newest round must remain verbatim in the
    un-summarized tail and must never appear inside ``summary_text``.

    The planner explicitly excludes the last round (``plan_segment(rounds[:-1])``);
    this asserts the end-to-end result honours that for a long conversation.
    """
    set_cfg(window=8000, max_tokens=1024)
    # 12 rounds -> 24 messages, seq 1..24; live round == ROUND11 (messages 23,24).
    cid = await _seed_conv(12, words=400)

    # Echo the folded text back so summary_text contains the folded rounds' markers.
    monkeypatch.setattr(cs, "llm_client", _EchoLLM())
    assert await cs.run_summary_pass(cid, blocking=True, history=None) in (True, False)

    msgs = await _all_messages(cid)
    conv = await _conv_row(cid)
    cursor = conv.summary_msg_seq or 0
    summary = conv.summary_text or ""

    # A fold actually happened.
    assert cursor > 0, "expected at least one fold"

    # The newest message is still in the replayed tail (seq >= cursor).
    newest = max(m.seq for m in msgs)
    tail = [m for m in msgs if m.seq >= cursor]
    assert tail, "tail must not be empty"
    assert max(m.seq for m in tail) == newest

    # The live round's marker never leaked into the folded summary.
    assert "ROUND11" not in summary, "live round (ROUND11) must never be folded"

    # At least one earlier round WAS folded (sanity: the test is meaningful).
    assert "ROUND0" in summary, "expected earlier rounds to be folded"


async def _conv_row(cid: str) -> Conversation:
    async with _db_factory() as db:
        return await db.get(Conversation, cid)


# ── K2: L0 append + cursor advance are atomic (I2) ────────────────────────────
async def test_k2_cursor_advance_is_atomic_single_update(test_db, monkeypatch):
    """Every cursor-advancing UPDATE must (a) set BOTH ``summary_text`` (L0 append)
    and ``summary_msg_seq`` (cursor) in the SAME statement -- no split write that a
    crash could leave half-applied -- and (b) be CAS-guarded on the old cursor so a
    concurrent pass cannot double-append or lose the cursor.
    """
    set_cfg(window=8000, max_tokens=1024)
    cid = await _seed_conv(12, words=400)

    monkeypatch.setattr(cs, "llm_client", _EchoLLM())

    with _spy_session_statements(monkeypatch) as captured:
        await cs.run_summary_pass(cid, blocking=True, history=None)

    cursor_updates = [s for s in captured if _is_conv_cursor_update(s)]
    assert cursor_updates, "expected at least one cursor-advancing UPDATE"

    for u in cursor_updates:
        # (a) atomic: the same statement carries both the L0 append and the cursor.
        assert "summary_text" in u, "L0 append must be in the same UPDATE as the cursor"
        # (b) CAS guard: the WHERE pins the old cursor value.
        where = u.split("WHERE", 1)[1] if "WHERE" in u else ""
        assert "summary_msg_seq" in where, "cursor advance must be CAS-guarded"


async def test_k2_tail_cursor_consistency_after_fold(test_db, monkeypatch):
    """After a fold, the cursor and the L0 text agree: replaying seq>=cursor yields
    exactly the messages left over, and no duplicate summary segment was written.
    """
    set_cfg(window=8000, max_tokens=1024)
    cid = await _seed_conv(12, words=400)
    monkeypatch.setattr(cs, "llm_client", _EchoLLM())

    assert await cs.run_summary_pass(cid, blocking=True, history=None) in (True, False)
    conv = await _conv_row(cid)
    cursor = conv.summary_msg_seq or 0
    summary = conv.summary_text or ""

    msgs = await _all_messages(cid)
    total = len(msgs)
    tail = [m for m in msgs if m.seq >= cursor]
    # Exact partition: folded prefix + replayed tail == whole history, once.
    assert (cursor - 1) + len(tail) == total
    # No duplicate segments appended (each folded round appears once).
    segs = summary.split(cs.SUMMARY_SEGMENT_DELIM) if summary else []
    assert len(segs) == len(set(segs)), "duplicate L0 segment detected"


# ── K6: the request path never writes the cursor column (I6) ──────────────────
class _FakeGraph:
    async def run(self, state):
        return {
            **state, "final_answer": "", "citations": [], "tool_messages": [],
            "context_breakdown": None, "retrieval_ms": 0, "pending_limit": None,
            "cache_hit": False, "agent_steps": [],
        }

    def build_generation_messages(self, state):
        return ([{"role": "user", "content": "x"}], False)


class _GenLLM:
    async def chat_stream(self, messages, **kwargs):
        yield "answer"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_k6_request_path_never_writes_cursor(client, user_token, monkeypatch):
    """Drive a full chat turn that enters the compression path, with the executor
    (run_summary_pass / schedule_summary_pass) stubbed out so any cursor write we
    observe can ONLY come from the request path. Assert no UPDATE touches
    ``summary_msg_seq``.
    """
    set_cfg(window=2000, max_tokens=256)
    from app.services.auth import decode_token

    uid = decode_token(user_token)["sub"]
    cid = await _seed_conv(12, words=200, user_id=uid)

    # Isolate the request path: the executor writes the cursor, not the request.
    async def _noop(*a, **k):
        return False

    monkeypatch.setattr(cs, "run_summary_pass", _noop)
    monkeypatch.setattr(cs, "schedule_summary_pass", lambda *a, **k: None)
    monkeypatch.setattr("app.services.agent_graph.ragclaw_agent_graph", _FakeGraph())
    monkeypatch.setattr("app.services.llm_client.llm_client", _GenLLM())

    with _spy_session_statements(monkeypatch) as captured:
        resp = await client.post(
            "/api/chat/stream",
            json={"query": "k6 cursor-write probe", "kb_id": "x", "conversation_id": cid},
            headers=_auth(user_token),
        )
    assert resp.status_code == 200

    cursor_writes = [s for s in captured if _is_conv_cursor_update(s)]
    assert not cursor_writes, (
        f"request path must not write summary_msg_seq; saw: {cursor_writes}"
    )


# ── K9: the trimming loop always terminates (I9) ──────────────────────────────
def _counting_build_messages(limit: int, sink: list):
    def _w(*a, **k):
        sink[0] += 1
        if sink[0] > limit:
            raise RuntimeError(
                f"fit_assembly_context did not converge: >{limit} build_messages calls"
            )
        return simple_build_messages(*a, **k)

    return _w


def test_k9_fuzz_trim_loop_always_terminates():
    """Fuzz many random payloads through the mechanical trimmer. Every call must
    either return a fitted tuple or raise ContextWindowExceeded -- never spin. The
    counting wrapper turns a runaway loop into a test failure instead of a hang.
    """
    rng = random.Random(20260811)
    for trial in range(60):
        n_rag = rng.randint(0, 14)
        n_mem = rng.randint(0, 8)
        n_hist = rng.randint(1, 25)
        n_tool = rng.randint(0, 10)

        rag = joined(n_rag, cs.RAG_CHUNK_DELIM, words=rng.randint(3, 250)) if n_rag else None
        mem = joined(n_mem, cs.MEM_CHUNK_DELIM, words=rng.randint(3, 200)) if n_mem else None
        hist = make_history(n_hist, words=rng.randint(2, 90))
        payload = make_tool_payload(n_tool, words=rng.randint(2, 60)) if n_tool else []
        query = "q " * rng.randint(1, 60)

        # Bound the number of build_messages calls: each loop iteration drops >=1
        # unit, so it can run at most (units + a small constant) times.
        sink = [0]
        limit = n_rag + n_mem + n_hist + n_tool + 40
        bm = _counting_build_messages(limit, sink)

        set_cfg(window=rng.choice([1000, 2000, 4000, 8000]), max_tokens=256)
        try:
            out = cs.fit_assembly_context(
                None, hist, rag, mem, payload, query,
                "messages" if payload else "results", bm,
            )
        except cs.ContextWindowExceeded:
            continue
        assert isinstance(out, tuple) and len(out) == 7, "fit must return (s,h,r,m,p,q,dropped)"


def test_k9_exhausted_prefix_raises():
    """When even the last survivor cannot fit, the loop must STOP and raise a
    localized code -- not loop forever. A single enormous query is the clearest
    case (the query is never truncated by fit)."""
    set_cfg(window=2000, max_tokens=256)
    huge_query = "x " * 5000
    with pytest.raises(cs.ContextWindowExceeded):
        cs.fit_assembly_context(None, [], None, None, [], huge_query, "results", simple_build_messages)


def test_k9_successful_trim_fits_budget():
    """A genuinely overflowing payload must be reduced until it fits (dropped=True),
    proving the loop makes progress rather than giving up or spinning."""
    set_cfg(window=2000, max_tokens=256)
    rag = joined(20, cs.RAG_CHUNK_DELIM, words=300)
    hist = make_history(20, words=200)
    out = cs.fit_assembly_context(
        None, hist, rag, None, [], "normal question", "results", simple_build_messages
    )
    s, h, r, m, p, q, dropped = out
    assert dropped is True
    assert count_messages_tokens(simple_build_messages(s, h, r, p, q, m)) <= cs._budget() + 16


# ── K10: sacred prefix conservation (I10) ─────────────────────────────────────
def _sacred_state(**over):
    state = {
        "query": "hi",
        "conversation_summary": "",
        "conversation_history": [],
        "rag_context": None,
        "memory_context": None,
        "tool_results": [],
        "kb_prompt": "",
        "user_memory": "",
        "pinned_instruction": "",
        "active_skill": {},
    }
    state.update(over)
    return state


def _system_texts(messages):
    return [m["content"] for m in messages if m.get("role") == "system"]


def test_k10_sacred_prefix_survives_trim_byte_for_byte():
    """Force a real trim (huge RAG + history), then prove the sacred prefix block
    (pin / kb / user_memory / skill system_prompt) is byte-for-byte identical to the
    un-trimmed run. fit may drop rag/history/summary/memory/tool records, but it
    must never touch the fixed system prefix.
    """
    set_cfg(window=2000, max_tokens=256)
    markers = {
        "pin": "PIN_MARKER_X9",
        "kb": "KB_MARKER_Q2",
        "usermem": "USERMEM_MARKER_L7",
        "skill": "SKILL_MARKER_T3",
    }
    base = _sacred_state(
        pinned_instruction=markers["pin"],
        kb_prompt=markers["kb"],
        user_memory=markers["usermem"],
        active_skill={"name": "probe", "system_prompt": markers["skill"]},
    )

    # No overflow -> no trim.
    baseline_msgs, dropped_base = ragclaw_agent_graph.build_generation_messages(dict(base))
    assert dropped_base is False
    baseline_sys = _system_texts(baseline_msgs)

    # Overflowing payload -> trim must happen, but the prefix is untouched.
    overflow = dict(base)
    overflow["rag_context"] = "doc " * 8000
    overflow["conversation_history"] = make_history(30, words=500)
    overflow["query"] = "probe the prefix survives"
    trimmed_msgs, dropped_trim = ragclaw_agent_graph.build_generation_messages(overflow)
    assert dropped_trim is True, "expected a trim to actually occur"
    trimmed_sys = _system_texts(trimmed_msgs)

    # Byte-for-byte equality of the entire sacred prefix block.
    assert trimmed_sys == baseline_sys
    joined_sys = "\n".join(trimmed_sys)
    for v in markers.values():
        assert v in joined_sys, f"sacred marker {v!r} missing from prefix after trim"


def test_k10_gate_a_rejects_oversized_prefix():
    """Gate A must reject a request whose fixed prefix alone exceeds the window,
    distinguishing a too-large prefix (CONTEXT_PREFIX_TOO_LARGE) from a too-long
    question (QUERY_TOO_LONG). The sacred components feed the prefix floor.
    """
    set_cfg(window=2000, max_tokens=256)

    # Prefix alone overflows -> CONTEXT_PREFIX_TOO_LARGE.
    code = cs.classify_entry_overflow(
        "short question",
        kb_prompt="k " * 1500,
        user_memory="u " * 1500,
        ws_context="w " * 1500,
        skill_prompt="s " * 1500,
        pinned_instruction="p " * 1500,
    )
    assert code == cs.CONTEXT_PREFIX_TOO_LARGE

    # Prefix fits; only the (enormous) question overflows -> QUERY_TOO_LONG.
    code2 = cs.classify_entry_overflow(
        "x " * 5000,
        kb_prompt="k",
        user_memory="u",
        ws_context="w",
        skill_prompt="s",
        pinned_instruction="p",
    )
    assert code2 == cs.QUERY_TOO_LONG

    # Small everything -> worth attempting.
    assert cs.classify_entry_overflow(
        "hi", kb_prompt="k", user_memory="u", ws_context="w",
        skill_prompt="s", pinned_instruction="p",
    ) is None


# ── K12: history cache correctness + shape independence (I12) ─────────────────
async def test_k12_cache_cold_then_warm_incremental(test_db):
    """First load (cold) reads the full tail; an appended message is picked up by
    the warm path via an incremental ``seq > cached_max_seq`` refresh -- the cached
    list object is extended in place (same identity), proving no full rebuild."""
    set_cfg(window=8000, max_tokens=1024)
    cid = await _seed_conv(6, words=20)  # 12 messages, seq 1..12

    async with _db_factory() as db:
        cold = await chat_router._load_history(cid, db, 0)
    assert [m["seq"] for m in cold] == list(range(1, 13))
    entry = chat_router._HISTORY_CACHE[cid]
    assert entry["max_seq"] == 12
    cold_list_id = id(entry["msgs"])

    # Append two messages (seq 13, 14).
    async with _db_factory() as db:
        for i in (13, 14):
            db.add(Message(
                id=str(uuid.uuid4()), conversation_id=cid, role="user" if i % 2 else "assistant",
                content=f"APPEND{i}", content_token_count=10, seq=i,
            ))
        await db.commit()

    async with _db_factory() as db:
        warm = await chat_router._load_history(cid, db, 0)
    assert [m["seq"] for m in warm] == list(range(1, 15))
    # Warm refresh extended the SAME cached list (no full rebuild).
    assert id(chat_router._HISTORY_CACHE[cid]["msgs"]) == cold_list_id
    assert chat_router._HISTORY_CACHE[cid]["max_seq"] == 14


async def test_k12_cache_shape_independence(test_db):
    """``_tail_from`` produces identical slices whether the cached history is the
    full seq-ordered list or a tail-only list starting at the same cursor -- the
    cache shape must not shift the cursor coordinate."""
    full = make_history(12)  # seq 0..23
    start = 8
    tail_only = full[start:]  # cached tail, starts at seq == full[start]["seq"]
    start_seq = tail_only[0]["seq"]
    for cursor in range(start_seq, full[-1]["seq"] + 2):
        assert cs._tail_from(full, cursor) == cs._tail_from(tail_only, cursor)


async def test_k12_eviction_rebuilds_cache(test_db):
    """Compacting / pin / segment-delete all call ``_evict_history_cache``; after
    eviction a fresh cold load re-queries from the DB and rebuilds correctly."""
    set_cfg(window=8000, max_tokens=1024)
    cid = await _seed_conv(4, words=20)

    async with _db_factory() as db:
        await chat_router._load_history(cid, db, 0)
    assert cid in chat_router._HISTORY_CACHE

    chat_router._evict_history_cache(cid)
    assert cid not in chat_router._HISTORY_CACHE

    async with _db_factory() as db:
        rebuilt = await chat_router._load_history(cid, db, 0)
    assert [m["seq"] for m in rebuilt] == list(range(1, 9))
    assert cid in chat_router._HISTORY_CACHE

"""Layer G — the single ``seq`` cursor: schema, lockstep and DB coordinates.

``summary_msg_seq`` is the ONLY thing separating "already folded into the summary"
from "still replayed verbatim". It is stored as a *seq value* (not a list index),
so every consumer must agree on three things:

* ``Message.seq`` is dense and monotonic per conversation, assigned at INSERT
  time by ``_next_message_seq`` (even for rows batched into one flush);
* the boundary is ``seq >= cursor`` (``_tail_from``), i.e. the cursor holds the
  seq of the OLDEST un-summarized message — ``Segment.end`` = last folded seq + 1;
* the schema carrying it survives an upgrade from a pre-compression database.

Coverage map (v3 plan):
  G1  schema patches: fresh install is a no-op; legacy upgrade adds the columns,
      keeps row order, and is idempotent on a second run
  G2  cursor/seq lockstep: ``_tail_from`` slices by VALUE and matches the
      positional slice while seq is dense
  G3  ``_read_context_cursor`` -> ``(summary_msg_seq, total_messages)``
  G4  resume rebuild: ``recent = _tail_from(history, summary_msg_seq)``
  G5  SSE ``done`` carries ``summary_msg_seq`` + ``total_messages``
  G6  seq stays dense/monotonic per conversation (single-flush batch included)

Two known gaps are pinned with ``xfail(strict=True)`` so they flip red the moment
someone fixes them (see the docstrings for the details):
  G1c  the ``(conversation_id, seq)`` unique index is missing on fresh installs
  G4b  ``_build_resume_initial_state`` drops ``pinned_instruction``
"""
import json
import sqlite3
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, inspect as sa_inspect, select, func

from app.database import async_session
from app.models.conversation import Conversation, Message
from app.routers import chat as chat_router
from app.routers.chat import _build_resume_initial_state, _read_context_cursor
from app.schema_patches import PATCHES, run_patches
from app.services.conversation_summary import _tail_from, _uni_rounds
from app.services.token_count import count_text_tokens
from helpers import set_cfg


# ── Fixtures / helpers ───────────────────────────────────────────────────────
@pytest_asyncio.fixture(autouse=True)
async def _init_cm(test_db):
    """Seed ConfigManager — the ASGI transport does not run app lifespan."""
    from app.services.config_manager import config_manager

    await config_manager.init()


@pytest_asyncio.fixture(autouse=True)
async def _reset_history_cache():
    """Drop the module-level history cache around every test (N21)."""
    chat_router._HISTORY_CACHE.clear()
    chat_router._HISTORY_CACHE_LOCKS.clear()
    yield
    chat_router._HISTORY_CACHE.clear()
    chat_router._HISTORY_CACHE_LOCKS.clear()


async def _seed(rounds: int, *, words: int = 50, cursor: int = 0, user_id=None,
                explicit_seq: bool = True) -> str:
    """Insert a conversation with ``rounds`` user/assistant pairs.

    ``explicit_seq=False`` leaves ``seq`` to the ORM column default
    (``_next_message_seq``) — that is what G6 exercises.
    """
    conv = Conversation(id=str(uuid.uuid4()), user_id=user_id, summary_msg_seq=cursor)
    async with async_session() as db:
        db.add(conv)
        await db.commit()
        seq = 1
        for i in range(rounds):
            for role in ("user", "assistant"):
                content = f"R{i} {role} " + "word " * words
                kw = {"seq": seq} if explicit_seq else {}
                db.add(Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conv.id,
                    role=role,
                    content=content,
                    content_token_count=count_text_tokens(content),
                    **kw,
                ))
                seq += 1
        await db.commit()
    return conv.id


def _history(n: int, start: int = 1) -> list[dict]:
    """A dict-shaped history with dense seq values starting at ``start``."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"m{i}",
         "seq": start + i}
        for i in range(n)
    ]


# ── G1a: fresh install already satisfies every schema patch ─────────────────
def test_g1a_fresh_create_all_leaves_patches_applied(tmp_path):
    """``create_all`` builds the final shape from the ORM models, so every
    compression-related patch must report "already applied" on a fresh database.

    If this goes red, a patch's ``applied`` predicate has drifted away from the
    model — the upgrade path and the fresh-install path no longer converge.
    """
    from app.database import Base
    import app.models  # noqa: F401 — register every table on Base.metadata

    db_file = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_file}")
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            insp = sa_inspect(conn)
            for patch in PATCHES:
                assert patch.applied(insp), f"patch {patch.name} not satisfied by create_all"
    finally:
        engine.dispose()


# ── G1b: legacy upgrade adds the columns, preserves rows, and is idempotent ──
def test_g1b_legacy_upgrade_adds_columns_and_is_idempotent(tmp_path):
    """A pre-compression database (no ``seq`` / ``summary_msg_seq`` /
    ``pinned_instruction``) is brought to the current shape by ``run_patches``,
    without reordering or dropping the rows it already holds.

    Mirrors the real boot order: ``create_all`` first (it only creates WHOLE
    missing tables, so the two legacy tables below survive untouched), then
    ``run_patches`` to add the columns ``create_all`` cannot.

    Legacy rows keep ``seq IS NULL`` on purpose: the cursor simply starts fresh
    for them (see ``_add_message_seq_and_token_columns``).
    """
    from app.database import Base
    import app.models  # noqa: F401

    db_file = tmp_path / "legacy.db"
    raw = sqlite3.connect(db_file)
    raw.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            title TEXT,
            summary_text TEXT
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            role TEXT,
            content TEXT
        );
        """
    )
    raw.execute("INSERT INTO conversations (id, title) VALUES ('c1', 'legacy')")
    for i, role in enumerate(("user", "assistant", "user", "assistant")):
        raw.execute(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (?,?,?,?)",
            (f"m{i}", "c1", role, f"legacy-{i}"),
        )
    raw.commit()
    raw.close()

    engine = create_engine(f"sqlite:///{db_file}")
    try:
        # Boot step 1: create_all fills in every table the legacy DB lacks and
        # leaves the two existing ones exactly as they are.
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            insp = sa_inspect(conn)
            # Pre-condition: the legacy tables survived and still lack the columns.
            assert not any(c["name"] == "seq" for c in insp.get_columns("messages"))
            # Boot step 2: patches add what create_all cannot.
            run_patches(conn)

        with engine.connect() as conn:
            insp = sa_inspect(conn)
            msg_cols = {c["name"] for c in insp.get_columns("messages")}
            conv_cols = {c["name"] for c in insp.get_columns("conversations")}
            assert {"seq", "content_token_count"} <= msg_cols
            assert {"summary_msg_seq", "summary_archived_count", "pinned_instruction"} <= conv_cols

            rows = conn.exec_driver_sql(
                "SELECT id, content, seq FROM messages ORDER BY rowid"
            ).fetchall()
            # Order preserved, content untouched, legacy seq left NULL.
            assert [r[0] for r in rows] == ["m0", "m1", "m2", "m3"]
            assert [r[1] for r in rows] == [f"legacy-{i}" for i in range(4)]
            assert all(r[2] is None for r in rows)
            # Server default backfilled the cursor for the pre-existing conversation.
            cur = conn.exec_driver_sql(
                "SELECT summary_msg_seq, pinned_instruction FROM conversations WHERE id='c1'"
            ).fetchone()
            assert cur[0] == 0 and cur[1] is None

        # Idempotent: a second run must be a pure no-op (patches guard on state).
        with engine.begin() as conn:
            run_patches(conn)
        with engine.connect() as conn:
            insp = sa_inspect(conn)
            for patch in PATCHES:
                assert patch.applied(insp), f"patch {patch.name} not idempotent"
    finally:
        engine.dispose()


# ── G1c: the uniqueness guarantee the model documents is NOT enforced ───────
@pytest.mark.xfail(
    strict=True,
    reason=(
        "N22: the (conversation_id, seq) UNIQUE index only exists on databases "
        "upgraded from the retired alembic era (dropped in b687ba5). The ORM model "
        "declares no __table_args__ index and no schema patch recreates it, so a "
        "FRESH install has no uniqueness guard -- even though conversation.py:27 "
        "and :104 both rely on it. Remove this xfail once the index is declared."
    ),
)
def test_g1c_fresh_install_has_conv_seq_unique_index(tmp_path):
    from app.database import Base
    import app.models  # noqa: F401

    db_file = tmp_path / "idx.db"
    engine = create_engine(f"sqlite:///{db_file}")
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            idx = sa_inspect(conn).get_indexes("messages")
        unique_pairs = [
            i for i in idx
            if i.get("unique") and set(i["column_names"]) == {"conversation_id", "seq"}
        ]
        assert unique_pairs, f"no UNIQUE(conversation_id, seq) index; got {idx}"
    finally:
        engine.dispose()


# ── G2: cursor/seq lockstep ─────────────────────────────────────────────────
def test_g2_cursor_seq_lockstep_value_not_position():
    """The cursor is a seq VALUE. While seq is dense from 1, the tail it selects
    is exactly the positional slice ``history[cursor-1:]`` — and the first kept
    message has ``seq == cursor``. That equivalence is what lets the executor
    write ``Segment.end`` straight into ``summary_msg_seq``.
    """
    hist = _history(10)  # seq 1..10
    for cursor in (1, 2, 5, 10):
        tail = _tail_from(hist, cursor)
        assert tail == hist[cursor - 1:]
        assert tail[0]["seq"] == cursor

    # Boundary is INCLUSIVE: the message at seq == cursor is still replayed.
    assert _tail_from(hist, 4)[0]["seq"] == 4
    # cursor <= 0 means "nothing folded" -> whole history, same object contents.
    assert _tail_from(hist, 0) == hist
    # A cursor past the end drains the tail (everything folded).
    assert _tail_from(hist, 11) == []


def test_g2b_tail_from_works_on_a_cache_trimmed_list():
    """``_load_history`` may hand back a tail-only list that starts at seq > 1.
    Slicing by value (not position) keeps that correct — this is the regression
    guard for the off-by-one that a positional slice would reintroduce.
    """
    full = _history(10)
    cached_tail = full[4:]  # starts at seq 5, position 0
    assert _tail_from(cached_tail, 7) == full[6:]
    assert _tail_from(cached_tail, 7)[0]["seq"] == 7
    # Positional slicing would have been wrong here:
    assert _tail_from(cached_tail, 7) != cached_tail[7:]


def test_g2c_segment_end_lands_in_cursor_coordinates():
    """``_uni_rounds`` keys rounds by seq and sets ``end = last seq + 1``, so a
    fold that consumes rounds 0..k leaves the cursor pointing at the first
    un-folded message — feed that back into ``_tail_from`` and nothing is lost
    or replayed twice.
    """
    hist = _history(10)  # 5 user/assistant rounds, seq 1..10
    rounds = _uni_rounds(hist)
    assert [(r.start, r.end) for r in rounds] == [(1, 3), (3, 5), (5, 7), (7, 9), (9, 11)]

    cursor = rounds[2].end  # folded rounds 0,1,2 -> messages seq 1..6
    tail = _tail_from(hist, cursor)
    assert [m["seq"] for m in tail] == [7, 8, 9, 10]
    # Exact partition: folded prefix + replayed tail == the whole history, once.
    assert len(hist) == (cursor - 1) + len(tail)


# ── G3 ───────────────────────────────────────────────────────────────────────
async def test_g3_read_context_cursor_returns_seq_and_total(test_db):
    cid = await _seed(3, cursor=5)  # 6 messages, cursor 5
    cursor, total = await _read_context_cursor(cid)
    assert (cursor, total) == (5, 6)


async def test_g3b_read_context_cursor_degrades_to_zero(test_db):
    """Telemetry for the context meter must never break the stream: an unknown
    conversation degrades to ``(0, 0)`` instead of raising."""
    assert await _read_context_cursor(str(uuid.uuid4())) == (0, 0)


# ── G4 ───────────────────────────────────────────────────────────────────────
def _pending_stub() -> dict:
    return {
        "skill_switch_quota": 0,
        "tool_round_quota": 0,
        "workspace_id": "",
        "active_skill": None,
        "available_tools": [],
        "rag_context": "",
        "citations": [],
        "tool_round": 0,
        "tool_results": [],
        "tool_messages": [],
        "query": "resumed query",
        "pending_limit": {},
    }


class _Req:
    query = "req query"
    kb_id = "kb"
    skill_id = None
    skip_cache = False
    timezone = "UTC"
    workspace_dir = ""


class _User:
    id = "u1"
    tenant_id = "t1"
    memory = ""
    timezone = None


def test_g4_resume_drops_the_summarized_prefix():
    """Resume rebuilds ``conversation_history`` as the un-summarized tail, so a
    suspended-then-continued turn cannot replay text that is already inside
    ``summary_text`` (which is re-injected separately)."""
    hist = _history(10)
    state = _build_resume_initial_state(
        _pending_stub(), "continue", _User(), hist, "kb prompt", _Req(),
        lambda *a, **k: None, "conv-1",
        summary_text="L0 SUMMARY", summary_msg_seq=7,
    )
    assert [m["seq"] for m in state["conversation_history"]] == [7, 8, 9, 10]
    assert state["conversation_summary"] == "L0 SUMMARY"
    assert state["resume_action"] == "continue"


def test_g4b_resume_with_zero_cursor_keeps_full_history():
    hist = _history(6)
    state = _build_resume_initial_state(
        _pending_stub(), "stop", _User(), hist, "", _Req(),
        lambda *a, **k: None, "conv-1", summary_msg_seq=0,
    )
    assert state["conversation_history"] == hist
    assert state["resume_action"] == "stop"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "H9: the resume assembly point (_build_resume_initial_state) never sets "
        "pinned_instruction, while the main assembly point does (chat.py:1306). "
        "A conversation resumed after a pending-limit suspension therefore loses "
        "its pinned instruction for that turn -- the sacred prefix silently "
        "disappears. Remove this xfail once the key is added."
    ),
)
def test_g4c_resume_carries_pinned_instruction():
    state = _build_resume_initial_state(
        _pending_stub(), "continue", _User(), _history(4), "", _Req(),
        lambda *a, **k: None, "conv-1", summary_msg_seq=0,
    )
    assert "pinned_instruction" in state


# ── G5 ───────────────────────────────────────────────────────────────────────
class _FakeGraph:
    """Minimal agent graph: no RAG, no tools, straight to generation."""

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
    async def chat_stream(self, messages, **kwargs):
        yield "answer"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = [ln[len("data:"):].lstrip() for ln in block.split("\n") if ln.startswith("data:")]
        if lines:
            events.append(json.loads("\n".join(lines)))
    return events


async def test_g5_done_event_carries_cursor_and_total(client, user_token, monkeypatch):
    """The context meter in the UI is driven entirely by the ``done`` payload, so
    the cursor and the message total have to ride along with every completed run
    (read fresh from the DB, after this turn's rows were saved)."""
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr("app.services.agent_graph.ragclaw_agent_graph", _FakeGraph())
    monkeypatch.setattr("app.services.llm_client.llm_client", _GenLLM())
    from app.services.auth import decode_token

    uid = decode_token(user_token)["sub"]
    cid = await _seed(2, words=20, cursor=3, user_id=uid)  # 4 messages, cursor 3

    resp = await client.post(
        "/api/chat/stream",
        json={"query": "g5-unique-done-cursor", "kb_id": "x", "conversation_id": cid},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    done = [e for e in _parse_sse(resp.text) if e.get("type") == "done"]
    assert done, "expected a done event"
    payload = done[-1]
    assert payload["summary_msg_seq"] == 3  # unchanged: nothing folded this turn
    async with async_session() as db:
        total = (await db.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == cid)
        )).scalar()
    # The turn appended the user + assistant rows before ``done`` was emitted.
    assert payload["total_messages"] == total > 4


# ── G6 ───────────────────────────────────────────────────────────────────────
async def test_g6_seq_dense_monotonic_across_batched_flush(test_db):
    """``_next_message_seq`` memoizes a high-water mark on the ExecutionContext so
    rows batched into ONE executemany still get distinct, dense numbers — a bare
    ``SELECT max(seq)`` per row would hand the whole batch the same value.

    Two separate flushes then prove the memo also picks up rows written earlier in
    the same transaction.
    """
    cid = await _seed(0)  # conversation only
    async with async_session() as db:
        # Batch 1: 6 rows in a single flush.
        db.add_all([
            Message(id=str(uuid.uuid4()), conversation_id=cid, role="user", content=f"a{i}")
            for i in range(6)
        ])
        await db.commit()
        # Batch 2: a second flush must continue from 7, not restart at 1.
        db.add_all([
            Message(id=str(uuid.uuid4()), conversation_id=cid, role="assistant", content=f"b{i}")
            for i in range(4)
        ])
        await db.commit()

        seqs = (await db.execute(
            select(Message.seq).where(Message.conversation_id == cid).order_by(Message.seq)
        )).scalars().all()

    assert seqs == list(range(1, 11)), f"seq not dense/monotonic: {seqs}"


async def test_g6b_seq_numbering_is_per_conversation(test_db):
    """Each conversation owns its own seq space — the cursor of one conversation
    can never address another's messages."""
    c1 = await _seed(2, explicit_seq=False)
    c2 = await _seed(3, explicit_seq=False)
    async with async_session() as db:
        s1 = (await db.execute(
            select(Message.seq).where(Message.conversation_id == c1).order_by(Message.seq)
        )).scalars().all()
        s2 = (await db.execute(
            select(Message.seq).where(Message.conversation_id == c2).order_by(Message.seq)
        )).scalars().all()
    assert s1 == [1, 2, 3, 4]
    assert s2 == [1, 2, 3, 4, 5, 6]

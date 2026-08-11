"""Layer F — asynchronous summarization executor + dual-watermark (Integration).

Uses the *real* isolated SQLite DB (the root ``test_db`` fixture) and a *mocked*
``llm_client`` so the executor's DB writes, cursor CAS-advance, in-flight guard
and multi-pass loop can be exercised without a live LLM.

Budget note (calibrated, see v3 plan §4): at the test window (8000) the segment
thresholds floor at ``min_tok = 2000``, ``max_tok = 8000``. The newest (live)
round is ALWAYS excluded from folding, so a real fold requires >=5 seeded rounds
(>=4 considered) to cross ``min_tok``; a 2-segment multi-pass needs enough rounds
that one ``Segment`` caps at ``max_tok`` while >=2 non-live rounds remain. Those
counts are what the fixtures below bake in.

Coverage map (v3 plan):
  F1/F14  _uni_rounds single source (ORM Message vs dict history, seq coords)
  F2      run_summary_pass(blocking) folds >=1 segment, advances cursor, writes summary_text
  F6      multi-pass loop re-reads cursor and appends a 2nd segment until below async_hi
  F8      live (newest) round excluded from folding
  F9      LLM failure -> cursor NOT advanced
  F12     _persistent_tokens_in = tail(seq>=cursor) + L0
  F13     emit wired: history_compressing (zh/en) via context_compress
  F4      in-flight guard (async): two schedule_summary_pass spawn ONE task
  F5      in-flight guard / CAS (sync): concurrent passes -> exactly one advances
  F3      CAS 0 rows -> lost race discarded, cursor untouched
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.conversation import Conversation, Message
from app.services import conversation_summary as cs
from app.services.conversation_summary import (
    SUMMARY_SEGMENT_DELIM,
    _persistent_tokens_in,
    _run_summary_pass_inner,
    _t,
    _uni_rounds,
    run_summary_pass,
    schedule_summary_pass,
)
from app.services.token_count import count_text_tokens
from helpers import set_cfg


class FakeLLM:
    """Stand-in for ``conversation_summary.llm_client``.

    ``returns`` is either a string or a callable(messages, kwargs)->str yielded by
    every summarization call. ``on_call`` (async or sync) fires once per call
    before the return value is produced -- used to simulate a concurrent writer.
    """

    def __init__(self, returns="FOLDED", on_call=None):
        self.returns = returns
        self.on_call = on_call
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.on_call is not None:
            r = self.on_call(messages, kwargs)
            if asyncio.iscoroutine(r):
                await r
        if callable(self.returns):
            return self.returns(messages, kwargs)
        return self.returns


async def _seed(rounds, words, user_id=None, cursor=0, l0=""):
    """Insert a conversation with ``rounds`` user/assistant pairs (seq 1..2N).

    Each message carries an exact ``content_token_count`` so DB-side token math is
    deterministic. Returns the conversation id.
    """
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=user_id,
        summary_text=l0 or None,
        summary_msg_seq=cursor,
    )
    async with async_session() as db:
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        seq = 1
        for i in range(rounds):
            for role in ("user", "assistant"):
                content = f"R{i} {role} " + "word " * words
                tok = count_text_tokens(content)
                db.add(
                    Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conv.id,
                        role=role,
                        content=content,
                        content_token_count=tok,
                        seq=seq,
                    )
                )
                seq += 1
        await db.commit()
    return conv.id


# ── F1 / F14 ────────────────────────────────────────────────────────────────
def test_f1_uni_rounds_orm_and_dict_identical():
    orm, dic = [], []
    seq = 1
    for i in range(3):
        for role in ("user", "assistant"):
            content = f"R{i} {role} hello world"
            dic.append({"role": role, "content": content, "seq": seq})
            orm.append(Message(role=role, content=content, seq=seq))
            seq += 1
    r_orm = _uni_rounds(orm)
    r_dic = _uni_rounds(dic)
    assert len(r_orm) == len(r_dic) == 3
    for a, b in zip(r_orm, r_dic):
        assert a.start == b.start and a.end == b.end and a.tokens == b.tokens
    # seq coordinates lock with the auto path: round k covers [2k+1, 2k+3)
    assert r_orm[0].start == 1 and r_orm[0].end == 3
    assert r_orm[2].start == 5 and r_orm[2].end == 7


# ── F2 ───────────────────────────────────────────────────────────────────────
async def test_f2_run_summary_pass_folds_and_advances(test_db, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())
    # 6 rounds -> 5 considered (live excluded) -> crosses min_tok -> 1 segment
    cid = await _seed(6, words=300)
    ok = await run_summary_pass(cid, blocking=True)
    assert ok is True
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.summary_msg_seq > 0
        assert conv.summary_text
        segs = conv.summary_text.split(SUMMARY_SEGMENT_DELIM)
        assert len(segs) >= 1


# ── F6 ───────────────────────────────────────────────────────────────────────
async def test_f6_multipass_loop_until_below_async_hi(test_db, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())
    # Enough rounds that one Segment caps at max_tok leaving >=2 non-live rounds,
    # so the loop re-runs and appends a 2nd segment before the watermark clears.
    cid = await _seed(24, words=300)
    ok = await run_summary_pass(cid, blocking=True)
    assert ok is True
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        segs = conv.summary_text.split(SUMMARY_SEGMENT_DELIM)
        # 2+ segments proves the loop re-ran and re-read the advanced cursor
        assert len(segs) >= 2
        # cursor advanced well past the first fold (>=4 messages)
        assert conv.summary_msg_seq >= 4


# ── F8 ───────────────────────────────────────────────────────────────────────
async def test_f8_live_round_excluded(test_db, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())
    # 6 rounds -> rounds 0-4 folded (seq 1-10), cursor -> 11; live round5 stays verbatim
    cid = await _seed(6, words=300)
    ok = await run_summary_pass(cid, blocking=True)
    assert ok is True
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        # Segment.end = last folded message seq + 1 = 11
        assert conv.summary_msg_seq == 11
        assert "FOLDED" in conv.summary_text
        # oldest folded away, newest (live) never folded -> neither raw text present
        assert "R0 user" not in conv.summary_text
        assert "R5 user" not in conv.summary_text


# ── F9 ───────────────────────────────────────────────────────────────────────
async def test_f9_llm_failure_keeps_cursor(test_db, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM(returns=""))  # summarize fails
    cid = await _seed(6, words=300)
    ok = await run_summary_pass(cid, blocking=True)
    assert ok is False
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.summary_msg_seq == 0
        assert conv.summary_text in (None, "")


# ── F12 ──────────────────────────────────────────────────────────────────────
async def test_f12_persistent_tokens_in(test_db):
    set_cfg(window=8000, max_tokens=1024)
    l0 = "OLDSummary paragraph text here"
    cid = await _seed(3, words=200, l0=l0)
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        res = await db.execute(
            select(Message.content_token_count).where(
                Message.conversation_id == cid, Message.seq >= 0
            )
        )
        tail = sum(r[0] for r in res.all())
        expected = tail + count_text_tokens(l0)
        got = await _persistent_tokens_in(db, cid, 0, l0)
        assert got == expected


# ── F13 ──────────────────────────────────────────────────────────────────────
async def test_f13_emit_wired_localized(test_db, monkeypatch):
    set_cfg(window=8000, max_tokens=1024, lang="zh")
    monkeypatch.setattr(cs, "llm_client", FakeLLM())
    events = []

    def emit(ev_type, msg):
        events.append((ev_type, msg))

    cid = await _seed(6, words=300)  # 6 rounds -> a real fold happens
    ok = await run_summary_pass(cid, blocking=True, emit=emit)
    assert ok is True
    assert ("context_compress", _t("history_compressing", "zh")) in events
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.summary_msg_seq > 0  # a genuine fold, not the no-op path


# ── F4 ───────────────────────────────────────────────────────────────────────
async def test_f4_inflight_guard_async_single_task(test_db, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())
    cid = await _seed(6, words=300)
    n_before = len(cs._BACKGROUND_TASKS)
    schedule_summary_pass(cid)
    assert cid in cs._INFLIGHT
    schedule_summary_pass(cid)  # second call must be a no-op
    assert len(cs._BACKGROUND_TASKS) == n_before + 1
    # let the single background task finish and clear the guard
    await asyncio.gather(*list(cs._BACKGROUND_TASKS))
    assert cid not in cs._INFLIGHT
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.summary_msg_seq > 0


# ── F5 ───────────────────────────────────────────────────────────────────────
async def test_f5_inflight_guard_sync_one_wins(test_db, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    monkeypatch.setattr(cs, "llm_client", FakeLLM())
    cid = await _seed(6, words=300)
    # Bypass the public guard so BOTH tasks reach the CAS UPDATE and race on it;
    # the WHERE summary_msg_seq==0 makes exactly one commit win.
    results = await asyncio.gather(
        _run_summary_pass_inner(cid, blocking=True),
        _run_summary_pass_inner(cid, blocking=True),
    )
    assert sum(1 for r in results if r) == 1  # exactly one pass advanced
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        assert conv.summary_msg_seq > 0


# ── F3 ───────────────────────────────────────────────────────────────────────
async def test_f3_cas_zero_rows_discards_lost_race(test_db, monkeypatch):
    set_cfg(window=8000, max_tokens=1024)
    cid = await _seed(6, words=300)  # >=5 rounds -> a fold is attempted

    # Simulate a *concurrent* pass that commits a new cursor (7) between our read
    # and our CAS update. Our WHERE summary_msg_seq==0 then matches 0 rows.
    async def bump(messages, kwargs):
        async with async_session() as db2:
            c2 = await db2.get(Conversation, cid)
            c2.summary_msg_seq = 7
            await db2.commit()

    monkeypatch.setattr(cs, "llm_client", FakeLLM(returns="FOLDED", on_call=bump))
    ok = await run_summary_pass(cid, blocking=True)
    assert ok is False
    async with async_session() as db:
        conv = await db.get(Conversation, cid)
        # our pass never advanced: cursor stays at the concurrent value, summary untouched
        assert conv.summary_msg_seq == 7
        assert conv.summary_text in (None, "")

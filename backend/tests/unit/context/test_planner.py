"""Layer B -- segment planner + splitters (pure functions, LLM-free).

``plan_segment`` is the heart of the executor's planning step. It is a pure
function of (rounds, cursor, min_tok, max_tok) -> Segment | ArchiveL0 | None,
so every branch (emit-on-cross, merge-over-long-round, tail-drained ArchiveL0,
best-effort-fold) is testable without a DB or LLM.
"""

from __future__ import annotations

from app.services.conversation_summary import (
    ArchiveL0,
    Round,
    Segment,
    _char_split,
    _hard_split,
    _segment_units,
    plan_segment,
    segment_thresholds,
    split_long_unit,
)
from app.services.token_count import count_text_tokens


def _rounds(n: int, tok: int, start: int = 0, step: int = 10):
    return [Round(start + i * step, start + (i + 1) * step, tok, f"R{i}") for i in range(n)]


# --- B1: segment_thresholds clamp ------------------------------------------

def test_b1_thresholds_clamp_and_ratio():
    # window*0.15 clamped to [8000, 50000]; MIN = MAX // 4
    assert segment_thresholds(32000) == (2000, 8000)
    assert segment_thresholds(128000) == (4800, 19200)
    assert segment_thresholds(1_000_000) == (12500, 50000)
    # tiny window still floors MAX at 8000
    assert segment_thresholds(1000) == (2000, 8000)
    # unknown window -> hardcoded defaults
    assert segment_thresholds(None) == (5000, 20000)
    assert segment_thresholds(0) == (5000, 20000)
    # invariant: MIN == MAX // 4
    for w in (32000, 128000, 1_000_000):
        mn, mx = segment_thresholds(w)
        assert mx // 4 == mn
        assert 8000 <= mx <= 50000


# --- B2 / B3: empty + single round -----------------------------------------

def test_b2_empty_rounds_returns_none():
    assert plan_segment([], 0, 50, 200) is None
    # cursor already past the only round
    assert plan_segment([Round(0, 2, 100, "x")], 2, 50, 200) is None


def test_b3_single_round_emits_segment():
    seg = plan_segment([Round(0, 2, 100, "Q A")], 0, 50, 200)
    assert isinstance(seg, Segment)
    assert seg.start == 0 and seg.end == 2
    assert seg.total_tokens == 100


# --- B4 / B5: accumulate to >= MAX -----------------------------------------

def test_b4_multi_round_crosses_max():
    rounds = _rounds(30, tok=300)
    seg = plan_segment(rounds, 0, min_tok=2000, max_tok=8000)
    assert isinstance(seg, Segment)
    # It stops at the last fully-folded round boundary BEFORE crossing MAX:
    # 26 rounds * 300 tok = 7800, the largest multiple of 300 still < 8000.
    assert seg.total_tokens == 7800
    assert seg.end == rounds[25].end  # last fully-folded round boundary


def test_b5_exactly_max_emits():
    rounds = _rounds(8, tok=1000)
    seg = plan_segment(rounds, 0, min_tok=2000, max_tok=8000)
    assert isinstance(seg, Segment)
    assert seg.total_tokens == 8000


# --- B6: over-long round merged + sliced -----------------------------------

def test_b6_overlong_round_merged_and_sliced():
    rounds = [Round(0, 1000, 9000, "x " * 4000)]
    seg = plan_segment(rounds, 0, min_tok=2000, max_tok=8000)
    assert isinstance(seg, Segment)
    assert any(u.kind == "msg_slice" for u in seg.units)


# --- B8: tail drained with acc < MIN -> ArchiveL0 ---------------------------

def test_b8_tail_drained_below_min_is_archivel0():
    rounds = _rounds(3, tok=500)  # 1500 < MIN(2000)
    out = plan_segment(rounds, 0, min_tok=2000, max_tok=8000)
    assert isinstance(out, ArchiveL0)


# --- B9: reached MIN, next round would overflow MAX -> stop here ------------

def test_b9_stop_at_min_when_next_round_would_overflow():
    rounds = [Round(0, 10, 2000, "a"), Round(10, 20, 6500, "b")]
    seg = plan_segment(rounds, 0, min_tok=2000, max_tok=8000)
    assert isinstance(seg, Segment)
    # folded only the first round (exactly MIN), refused to merge the huge next one
    assert seg.total_tokens == 2000
    assert seg.end == rounds[0].end


# --- B10: cursor skips already-folded rounds -------------------------------

def test_b10_cursor_skips_folded_rounds():
    rounds = [
        Round(0, 10, 500, "a"),
        Round(10, 20, 500, "b"),
        Round(20, 30, 500, "c"),
    ]
    seg = plan_segment(rounds, cursor=10, min_tok=200, max_tok=2000)
    assert isinstance(seg, Segment)
    assert seg.start == 10  # planning begins at the cursor
    assert seg.end == rounds[2].end


# --- B11: Round boundary semantics -----------------------------------------

def test_b11_round_boundary_exclusive_end():
    r = Round(4, 9, 100, "x")
    assert r.end > r.start
    assert r.end == 9


# --- B12: _segment_units slices an over-long round -------------------------

def test_b12_segment_units_slices_long_round():
    units = _segment_units([Round(0, 1, 9000, "big")], 0, 1, 8000)
    assert len(units) == 1
    assert units[0].kind == "msg_slice"
    assert units[0].text == "big"


# --- B13: split_long_unit reconstructs exactly ------------------------------

def test_b13_split_long_unit_join_is_identity():
    text = "para one content here\n\npara two content here\n\npara three content here"
    chunks = split_long_unit(text, max_tok=200)
    assert "\n\n".join(chunks) == text


# --- B14: hard/char split respects the cap ----------------------------------

def test_b14_hard_split_respects_cap():
    text = "sentence. " * 2000
    for piece in _hard_split(text, 200):
        assert count_text_tokens(piece) <= 220


def test_b14b_char_split_respects_cap():
    text = "w " * 4000
    for piece in _char_split(text, 200):
        assert count_text_tokens(piece) <= 220

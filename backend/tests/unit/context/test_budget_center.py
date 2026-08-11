"""Layer A -- the budget center (``app.services.context_budget``).

Everything downstream reads its ceilings from here, so an arithmetic slip in
this module silently mis-sizes compaction watermarks, the L0 cap and the
assembly trimmer at once. These tests pin the layout formula itself rather than
any particular number, then spot-check real window sizes.
"""

from __future__ import annotations

import pytest

from app.services import context_budget as cb
from app.services.config_manager import SUMMARY_SAFETY_MARGIN, config_manager

from helpers import set_cfg


# --- A1 -------------------------------------------------------------------

def test_a1_total_budget_arithmetic():
    set_cfg(window=32000, max_tokens=4096)
    assert cb.total_budget() == 32000 - (4096 + SUMMARY_SAFETY_MARGIN)


# --- A2 -------------------------------------------------------------------

def test_a2_compute_slot_split():
    set_cfg(window=32000, max_tokens=4096)
    b = cb.compute(prefix_tokens=1000, tool_tokens=2000)

    total = cb.total_budget()
    avail = total - 1000 - 2000

    assert b.total == total
    assert b.r_prefix == 1000
    assert b.r_tools == 2000
    assert b.r_rag == avail * cb.RAG_BUDGET_PCT // 100
    assert b.r_memory == avail * cb.MEMORY_BUDGET_PCT // 100
    assert b.persistent == avail - b.r_rag - b.r_memory
    # The four reserved slots plus P must reconstruct the whole input budget.
    assert b.reserved + b.persistent == total


def test_a2b_percentages_are_cut_from_avail_not_total():
    """Regression guard for the documented denominator choice.

    If the shares were taken off ``total`` instead of ``avail``, a big prefix
    would push P negative on a small window.
    """
    set_cfg(window=8000, max_tokens=1024)
    b = cb.compute(prefix_tokens=2000, tool_tokens=1000)
    avail = b.total - b.r_prefix - b.r_tools

    assert b.r_rag == avail * 25 // 100
    assert b.r_rag != b.total * 25 // 100
    assert b.persistent > 0


# --- A3 -------------------------------------------------------------------

def test_a3_persistent_positive_and_monotonic_in_window():
    set_cfg(max_tokens=1024)
    seen = []
    for w in (8000, 32000, 128000, 1_000_000):
        set_cfg(window=w)
        b = cb.compute(prefix_tokens=2000, tool_tokens=2048)
        assert b.persistent > 0, f"P must stay positive at window={w}"
        seen.append(b.persistent)
    assert seen == sorted(seen), "P must grow monotonically with the window"


def test_a3b_misconfigured_window_clamps_to_zero():
    """``max_tokens >= window`` makes ``total`` negative; the negative must not
    propagate into the percentage math."""
    set_cfg(window=4000, max_tokens=8000)
    b = cb.compute(prefix_tokens=500, tool_tokens=500)

    assert b.total < 0, "raw total is kept negative for diagnostics"
    assert b.r_rag == 0
    assert b.r_memory == 0
    assert b.persistent == 0
    assert b.async_hi == 0 and b.sync_hi == 0


# --- A4 -------------------------------------------------------------------

def test_a4_watermarks_are_fractions_of_persistent():
    set_cfg(window=32000, max_tokens=4096)
    b = cb.compute(prefix_tokens=1000, tool_tokens=2000)

    assert b.async_hi == int(b.persistent * cb.ASYNC_HI_FRAC)
    assert b.sync_hi == int(b.persistent * cb.SYNC_HI_FRAC)
    assert b.async_hi < b.sync_hi < b.persistent
    # Watermarks must NOT be measured against the raw window -- that was the
    # pre-refactor bug that made compaction fire far too late.
    assert b.sync_hi < b.window * cb.SYNC_HI_FRAC


# --- A5 -------------------------------------------------------------------

def test_a5_l0_cap_is_share_of_persistent():
    set_cfg(window=32000, max_tokens=4096, summary_archive_high_pct=40)
    b = cb.compute(prefix_tokens=1000, tool_tokens=2000)

    assert b.l0_cap() == int(b.persistent * 0.40)
    assert b.l0_cap(pct=10) == int(b.persistent * 0.10)
    assert b.l0_cap(pct=0) == 0
    assert b.l0_cap(pct=-5) == 0, "negative share must clamp, not invert"


# --- A6 -------------------------------------------------------------------

def test_a6_record_tool_tokens_keeps_high_water_mark():
    assert cb.default_tool_tokens() == cb.FALLBACK_TOOL_TOKENS

    cb.record_tool_tokens(5000)
    assert cb.default_tool_tokens() == 5000

    cb.record_tool_tokens(1000)
    assert cb.default_tool_tokens() == 5000, "must take max, not most-recent"

    cb.record_tool_tokens(9000)
    assert cb.default_tool_tokens() == 9000


# --- A7 -------------------------------------------------------------------

def test_a7_prefix_estimate_tracks_configuration():
    set_cfg(window=32000, max_tokens=4096, lang="en")
    small = cb.estimate_prefix_tokens()

    # Only the identity half (Part 1) is user-configurable; capabilities (Part 2)
    # is a build-time constant.
    set_cfg(llm_system_prompt_en="IDENTITY " * 500)
    big = cb.estimate_prefix_tokens()
    assert big > small, "a longer system prompt must raise the prefix estimate"

    # Memoized: repeated calls with identical configuration hit the cache.
    hits_before = cb._prefix_tokens_cached.cache_info().hits
    cb.estimate_prefix_tokens()
    assert cb._prefix_tokens_cached.cache_info().hits == hits_before + 1


# --- A8 -------------------------------------------------------------------

def test_a8_negative_inputs_do_not_propagate():
    set_cfg(window=32000, max_tokens=4096)
    b = cb.compute(prefix_tokens=-100, tool_tokens=-999)
    assert b.r_prefix == 0 and b.r_tools == 0
    assert b.persistent > 0


# --- A9 -------------------------------------------------------------------

def test_a9_default_budget_uses_estimated_prefix_and_tool_fallback():
    set_cfg(window=32000, max_tokens=4096)
    b = cb.default_budget()

    assert b.r_prefix == cb.estimate_prefix_tokens()
    assert b.r_tools == cb.FALLBACK_TOOL_TOKENS

    cb.record_tool_tokens(7777)
    assert cb.default_budget().r_tools == 7777, "observed value must take over"


# --- A10 ------------------------------------------------------------------

@pytest.mark.parametrize("window", [8000, 32000, 128000, 1_000_000])
def test_a10_slot_table_is_internally_consistent(window):
    """Cross-window sanity table: the invariants must hold at every size."""
    set_cfg(window=window, max_tokens=1024)
    b = cb.compute(prefix_tokens=1500, tool_tokens=2048)

    assert b.window == window
    assert b.reserved + b.persistent == b.total
    assert 0 < b.persistent <= b.total
    # RAG gets 2.5x memory's share by construction (25% vs 10%).
    assert b.r_rag >= b.r_memory * 2
    assert b.l0_cap() < b.persistent


# --- field-budget soft warnings (Layer I-w1..I-w6, unit half) --------------

def test_iw1_check_field_budget_flags_oversized_field():
    set_cfg(window=8000)
    limit = cb.field_warn_limit()
    assert limit == min(8000 * cb.FIELD_WARN_PCT // 100, cb.FIELD_WARN_ABS)

    warns = cb.check_field_budget("word " * (limit * 2), "system_prompt")
    assert len(warns) == 1
    w = warns[0]
    assert w["code"] == "PROMPT_FIELD_TOO_LARGE"
    assert w["params"]["field"] == "system_prompt"
    assert w["params"]["tok"] > w["params"]["limit"] == limit
    assert w["params"]["cw"] == 8000


def test_iw5_field_limit_switches_between_pct_and_absolute():
    set_cfg(window=8000)
    assert cb.field_warn_limit() == 800, "small window -> percentage binds"

    set_cfg(window=1_000_000)
    assert cb.field_warn_limit() == cb.FIELD_WARN_ABS, "huge window -> absolute binds"


def test_iw6_empty_and_small_fields_are_silent():
    set_cfg(window=8000)
    assert cb.check_field_budget(None, "kb_prompt") == []
    assert cb.check_field_budget("", "kb_prompt") == []
    assert cb.check_field_budget("   \n  ", "kb_prompt") == []
    assert cb.check_field_budget("a short instruction", "kb_prompt") == []

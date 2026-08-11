"""Layer D -- Gate B, the assembly-point hard ceiling (``fit_assembly_context``).

Pure-transient: this function is the ONLY hard ceiling in the pipeline and it
runs before every LLM call with the COMPLETE payload in hand. The tests here
drive it with a prefix-free ``build_messages`` stand-in (``simple_build_messages``)
so token math stays readable, plus a prefixed variant to exercise the
sacred-prefix conservation (I10) and the exhausted-branch code split (D18).

Because the trim planner is O(n) single-scan + a bounded fix-up, assertions lean
on STRUCTURAL invariants -- head/tail retention, no orphaned tool messages,
byte-equality of untouched components, idempotency -- rather than exact `k`
counts that depend on BPE seam drift.
"""

from __future__ import annotations

import pytest

from app.services import conversation_summary as cs
from app.services import context_budget as cb
from app.services.conversation_summary import (
    CONTEXT_PREFIX_TOO_LARGE,
    QUERY_TOO_LONG,
    ContextWindowExceeded,
    MEM_CHUNK_DELIM,
    RAG_CHUNK_DELIM,
    SUMMARY_SEGMENT_DELIM,
)
from app.services.token_count import count_messages_tokens, count_tools_tokens

from helpers import (
    assert_no_orphan_tool_messages,
    make_history,
    make_tool_payload,
    set_cfg,
    simple_build_messages,
)


# --- build_messages variants -------------------------------------------------

def prefixed_build_messages(prefix_text: str):
    """Wrap the prefix-free builder so a FIXED system prefix is always present,
    independent of the droppable components. Used to test I10 (sacred prefix is
    never touched by fit) and D18 (the exhausted branch measures ``prefix_only``)."""

    def _bm(summary, history, rag, payload, query, mem):
        msgs = [{"role": "system", "content": prefix_text}]
        msgs.extend(simple_build_messages(summary, history, rag, payload, query, mem))
        return msgs

    return _bm


def _split(field: str, delim: str, n: int, words: int = 40, tag: str = "X"):
    return delim.join(f"{tag}{i} " + f"{tag.lower()}{i}w " * words for i in range(n))


# --- D1 / D2 ---------------------------------------------------------------

def test_d1_returns_seven_tuple():
    set_cfg(window=32000, max_tokens=4096)
    out = cs.fit_assembly_context(
        "", make_history(2), "", "", [], "hi", "messages",
        simple_build_messages, budget=100000,
    )
    assert isinstance(out, tuple) and len(out) == 7


def test_d2_fits_returns_components_unchanged():
    set_cfg(window=32000, max_tokens=4096)
    hist = make_history(3)
    rag = _split("rag", RAG_CHUNK_DELIM, 4)
    mem = _split("mem", MEM_CHUNK_DELIM, 4)
    summary = _split("sum", SUMMARY_SEGMENT_DELIM, 4)
    payload = make_tool_payload(2)
    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        summary, hist, rag, mem, payload, "hello", "messages",
        simple_build_messages, budget=200000,
    )
    assert dropped is False
    assert s == summary and h == hist and r == rag and m == mem and p == payload and q == "hello"


# --- D3: idempotency (plan converged; re-fitting its own output is a no-op) ---

def test_d3_idempotent_on_trimmed_output():
    set_cfg(window=32000, max_tokens=4096)
    rag = _split("rag", RAG_CHUNK_DELIM, 12, words=60)
    mem = _split("mem", MEM_CHUNK_DELIM, 12, words=60)
    summary = _split("sum", SUMMARY_SEGMENT_DELIM, 12, words=60)
    hist = make_history(8, words=60)
    payload = make_tool_payload(6, words=60)
    out = cs.fit_assembly_context(
        summary, hist, rag, mem, payload, "question", "messages",
        simple_build_messages, budget=4000,
    )
    # Re-feed the returned components: a converged plan must be a fixed point.
    out2 = cs.fit_assembly_context(*out[:6], "messages", simple_build_messages, budget=4000)
    assert out2[6] is False  # nothing more to drop
    assert out2[:6] == out[:6]


# --- D4: trim ORDER rag -> memory -> summary -> history -> tool_payload -------

def test_d4_order_drops_enhancements_before_fallback():
    """Overflow covered by rag + memory + summary only: history and tool_payload
    must come back BYTE-IDENTICAL, proving trimming stopped before them, while
    the most-relevant head of rag/memory (tail-kept) and newest summary survive."""
    set_cfg(window=32000, max_tokens=4096)
    rag = _split("rag", RAG_CHUNK_DELIM, 10, words=50)
    mem = _split("mem", MEM_CHUNK_DELIM, 10, words=50)
    summary = _split("sum", SUMMARY_SEGMENT_DELIM, 10, words=50)
    hist = make_history(4, words=40)
    payload = make_tool_payload(3, words=40)
    seg_r = rag.split(RAG_CHUNK_DELIM)
    seg_m = mem.split(MEM_CHUNK_DELIM)
    seg_s = summary.split(SUMMARY_SEGMENT_DELIM)

    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        summary, hist, rag, mem, payload, "q", "messages",
        simple_build_messages, budget=4500,
    )
    assert dropped is True
    # enhancements trimmed, fallbacks untouched
    assert h == hist, "history must be untouched (lowest priority)"
    assert p == payload, "tool payload must be untouched (lowest priority)"
    assert r != rag and r.startswith(seg_r[0]), "RAG keeps most-relevant head"
    assert m != mem and m.startswith(seg_m[0]), "memory keeps most-relevant head"
    # At this budget only rag+mem paid; history/payload staying byte-identical is
    # the real ORDER proof (trimming never reached the lowest-priority components).
    assert s == summary, "summary untouched (enhancements covered the overflow)"


# --- D5 / D6 / D7: which component pays when it is the bulk -----------------

def test_d5_memory_is_trimmed_when_it_is_the_bulk():
    set_cfg(window=32000, max_tokens=4096)
    rag = _split("rag", RAG_CHUNK_DELIM, 1, words=20)
    mem = _split("mem", MEM_CHUNK_DELIM, 12, words=80)
    summary = _split("sum", SUMMARY_SEGMENT_DELIM, 2, words=20)
    hist = make_history(2, words=20)
    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        summary, hist, rag, mem, [], "q", "messages",
        simple_build_messages, budget=1200,
    )
    assert dropped is True
    assert r == rag and s == summary and h == hist  # these fit
    assert m != mem  # memory paid


def test_d6_rag_tiny_memory_pays_before_summary():
    set_cfg(window=32000, max_tokens=4096)
    rag = _split("rag", RAG_CHUNK_DELIM, 1, words=20)
    mem = _split("mem", MEM_CHUNK_DELIM, 10, words=70)
    summary = _split("sum", SUMMARY_SEGMENT_DELIM, 10, words=70)
    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        summary, [], rag, mem, [], "q", "messages",
        simple_build_messages, budget=1200,
    )
    assert dropped is True
    assert r == rag  # rag already minimal, untouched
    assert m != mem  # memory paid first among the bulk
    assert s != summary  # summary also trimmed once memory exhausted


def test_d7_summary_pays_when_rag_and_memory_are_tiny():
    set_cfg(window=32000, max_tokens=4096)
    rag = _split("rag", RAG_CHUNK_DELIM, 1, words=20)
    mem = _split("mem", MEM_CHUNK_DELIM, 1, words=20)
    summary = _split("sum", SUMMARY_SEGMENT_DELIM, 14, words=80)
    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        summary, [], rag, mem, [], "q", "messages",
        simple_build_messages, budget=2500,
    )
    assert dropped is True
    assert r == rag and m == mem
    assert s != summary  # only summary had mass to give


# --- D8: history keeps NEWEST (drops OLDEST) --------------------------------

def test_d8_history_keeps_newest_drops_oldest():
    set_cfg(window=32000, max_tokens=4096)
    hist = make_history(6, words=60)
    s, rh, r, m, p, q, dropped = cs.fit_assembly_context(
        "", hist, "", "", [], "q", "messages",
        simple_build_messages, budget=1200,
    )
    assert dropped is True
    # returned history is a strict suffix of the original
    assert rh and hist[-1] in rh, "newest message must survive"
    assert hist[0] not in rh, "oldest message must be dropped"
    # find how many were dropped from the front
    k = next(i for i, msg in enumerate(hist) if msg in rh)
    assert k >= 1 and rh == hist[k:]


# --- D9 / D10: tool payload dropped in PAIRS (no orphan) --------------------

def test_d9_tool_payload_dropped_in_pairs_no_orphan():
    set_cfg(window=32000, max_tokens=4096)
    payload = make_tool_payload(6, words=50)
    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        "", [], "", "", payload, "q", "messages",
        simple_build_messages, budget=700,
    )
    assert dropped is True
    assert_no_orphan_tool_messages(p)
    # newest units (tail) retained, oldest dropped
    assert p and payload[-1] in p


def test_d10_messages_payload_keeps_newest_units():
    set_cfg(window=32000, max_tokens=4096)
    payload = make_tool_payload(5, words=60)
    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        "", [], "", "", payload, "q", "messages",
        simple_build_messages, budget=650,
    )
    assert dropped is True
    assert_no_orphan_tool_messages(p)
    # the final tool result (pair of the newest unit) is retained
    assert payload[-1] in p


# --- D11: Phase-2 safety net empties a single oversized tool result ---------

def test_d11_single_oversized_tool_result_emptied_without_orphan():
    set_cfg(window=32000, max_tokens=4096)
    payload = make_tool_payload(1, words=2000)  # ~ one giant result unit
    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        "", [], "", "", payload, "q", "messages",
        simple_build_messages, budget=1500,
    )
    assert dropped is True
    # Phase-2 dropped the only survivor; no orphan because the whole unit left.
    assert p == []
    assert_no_orphan_tool_messages(p)


# --- D12: memory_context=None passes through --------------------------------

def test_d12_none_memory_passthrough():
    set_cfg(window=32000, max_tokens=4096)
    out = cs.fit_assembly_context(
        "", make_history(2), "", None, [], "hi", "messages",
        simple_build_messages, budget=200000,
    )
    assert out[3] is None


# --- D13: query is NEVER trimmed -------------------------------------------

def test_d13_query_never_trimmed():
    set_cfg(window=32000, max_tokens=4096)
    big_query = "alpha " * 600  # clearly oversized vs a tight budget
    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        "", [], "", "", [], big_query, "messages",
        simple_build_messages, budget=1500,
    )
    assert q == big_query  # unchanged even though components were dropped


# --- D14: tool schemas are reserved from the budget -------------------------

def test_d14_tools_reserved_from_budget():
    set_cfg(window=32000, max_tokens=4096)
    hist = make_history(3, words=20)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "x" * 800,
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    tools_tok = count_tools_tokens(tools)
    assert tools_tok > 50

    # Baseline size of the (small) assembly with no tools.
    total = count_messages_tokens(simple_build_messages("", hist, "", [], "hi", ""))
    # Budget just above the baseline: fits without tools, but reserving the tool
    # schemas' cost pushes eff_budget below the baseline -> overflow.
    budget = total + tools_tok // 2

    no_tools = cs.fit_assembly_context(
        "", hist, "", "", [], "hi", "messages",
        simple_build_messages, budget=budget, tools=None,
    )
    assert no_tools[6] is False

    with_tools = cs.fit_assembly_context(
        "", hist, "", "", [], "hi", "messages",
        simple_build_messages, budget=budget, tools=tools,
    )
    assert with_tools[6] is True
    assert cb._observed_tool_tokens == tools_tok


# --- D18: exhausted branch raises with the RIGHT code -----------------------

def test_d18_exhausted_prefix_too_large():
    set_cfg(window=32000, max_tokens=4096)
    prefix = "PREFIXBLOCK " * 2000  # ~2000-tok fixed prefix, untrimmable
    bm = prefixed_build_messages(prefix)
    with pytest.raises(ContextWindowExceeded) as exc:
        cs.fit_assembly_context(
            "", [], "", "", [], "", "messages", bm, budget=1000,
        )
    assert exc.value.args[0] == CONTEXT_PREFIX_TOO_LARGE


def test_d18_exhausted_query_too_long():
    set_cfg(window=32000, max_tokens=4096)
    prefix = "small " * 20  # fits within budget
    bm = prefixed_build_messages(prefix)
    with pytest.raises(ContextWindowExceeded) as exc:
        cs.fit_assembly_context(
            "", [], "", "", [], "QUERYBLOCK " * 4000, "messages", bm, budget=1000,
        )
    assert exc.value.args[0] == QUERY_TOO_LONG


# --- D19: sacred prefix conservation (I10) ----------------------------------

def test_d19_sacred_prefix_bytes_untouched_under_trim():
    """fit only ever receives summary/history/rag/mem/payload/query -- never the
    fixed prefix. Even under heavy trimming that drops every droppable component,
    the prefix survives byte-for-byte in the final assembly."""
    set_cfg(window=32000, max_tokens=4096)
    marker = "SACRED_TASK_BACKGROUND_MARKER_42"
    prefix = marker + " " + "p " * 800
    bm = prefixed_build_messages(prefix)
    rag = _split("rag", RAG_CHUNK_DELIM, 10, words=80)
    mem = _split("mem", MEM_CHUNK_DELIM, 10, words=80)
    summary = _split("sum", SUMMARY_SEGMENT_DELIM, 10, words=80)
    hist = make_history(6, words=80)
    payload = make_tool_payload(5, words=80)

    s, h, r, m, p, q, dropped = cs.fit_assembly_context(
        summary, hist, rag, mem, payload, "q", "messages", bm, budget=3000,
    )
    assert dropped is True
    # Reassemble from the returned components and confirm the prefix is intact.
    final = bm(s, h, r, p, q, m)
    prefix_msgs = [mm for mm in final if mm.get("role") == "system" and marker in mm["content"]]
    assert prefix_msgs, "sacred prefix disappeared from the assembly"
    assert prefix_msgs[0]["content"] == prefix

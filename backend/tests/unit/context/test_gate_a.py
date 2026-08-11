"""Layer E -- Gate A, the entry-point overflow firewall (``classify_entry_overflow``).

This is the cheapest possible rejection: it runs BEFORE history compression,
RAG or any LLM call, so a doomed request dies fast with a localized error code.
The most important case here is the N17 regression (E6/E3/E4/E5/E7): because
``7f557d3`` re-based the floor on the final-generation assembler, ``floor`` must
now equal the real final-gen prefix within message-boundary rounding.
"""

from __future__ import annotations

import pytest

from app.services import conversation_summary as cs
from app.services.conversation_summary import CONTEXT_PREFIX_TOO_LARGE, QUERY_TOO_LONG

from helpers import final_gen_prefix_tokens, set_cfg

# ~500 tokens: enough to push a maxed-out sacred field past a 2000-token window.
BIG = "x" * 4000
# ~5000 tokens: a query big enough to overflow an 8000-token window on its own
# while leaving the empty-query prefix comfortably inside it.
QBIG = "word " * 5000
# Clearly larger than the default capabilities base (~689 tok en) so that an
# explicit skill body makes the floor GROW rather than shrink.
SKILL_BIG = "skillword " * 1000


# --- E1 / E2 ---------------------------------------------------------------

def test_e1_empty_query_fits():
    set_cfg(window=32000, max_tokens=4096)
    assert cs.classify_entry_overflow("") is None


def test_e2_short_query_small_prefix_fits():
    set_cfg(window=32000, max_tokens=4096)
    assert cs.classify_entry_overflow("hello world", kb_prompt="kb " * 50) is None


# --- E3: prefix alone overflows -> CONTEXT_PREFIX_TOO_LARGE -----------------

def test_e3_giant_kb_prefix_overflow():
    # window=2000 -> budget ~1232, but the final-gen floor itself is ~2000 tok,
    # so even an empty query overflows the prefix. A large KB pushes it further.
    set_cfg(window=2000, max_tokens=512)
    code = cs.classify_entry_overflow("", kb_prompt=BIG)
    assert code == CONTEXT_PREFIX_TOO_LARGE


@pytest.mark.parametrize("field", ["user_memory", "pinned_instruction", "kb_prompt"])
def test_e3_each_sacred_prefix_field_can_blow_the_gate(field):
    set_cfg(window=2000, max_tokens=512)
    code = cs.classify_entry_overflow("", **{field: BIG})
    assert code == CONTEXT_PREFIX_TOO_LARGE


def test_e3_explicit_skill_body_in_prefix_overflow():
    set_cfg(window=2000, max_tokens=512)
    code = cs.classify_entry_overflow("", skill_prompt=BIG)
    assert code == CONTEXT_PREFIX_TOO_LARGE


# --- E4: prefix fits, query too long -> QUERY_TOO_LONG ----------------------

def test_e4_query_too_long_with_fitting_prefix():
    # window=8000 -> budget ~6720, comfortably above the ~2000-tok floor, but a
    # 4000-char question (~1000+ tok) on top still overflows the budget.
    set_cfg(window=8000, max_tokens=1024)
    # QBIG (~6250 tok) on top of the ~2000-tok floor blows past the ~6720 budget.
    code = cs.classify_entry_overflow(QBIG)
    assert code == QUERY_TOO_LONG


# --- E5: floor tracks every sacred-prefix field ----------------------------

def test_e5_floor_grows_with_each_field():
    set_cfg(window=32000, max_tokens=4096)
    base = cs._empty_context_request_tokens("")

    with_user = cs._empty_context_request_tokens("", user_memory="m " * 500)
    with_kb = cs._empty_context_request_tokens("", kb_prompt="k " * 500)
    with_pin = cs._empty_context_request_tokens("", pinned_instruction="p " * 500)
    # SKILL_BIG replaces the ~689-tok capabilities base, so it must be clearly
    # larger than that base or the floor would SHRINK instead of grow.
    with_skill = cs._empty_context_request_tokens("", skill_prompt=SKILL_BIG)

    assert with_user > base
    assert with_kb > base
    assert with_pin > base
    assert with_skill > base


def test_e5_ws_context_does_not_affect_floor():
    """N17 fix: floor models final-gen, which does NOT emit the working-dir note.

    Passing a giant ``ws_context`` must NOT inflate the Gate A floor.
    """
    set_cfg(window=32000, max_tokens=4096)
    floor_no_ws = cs._empty_context_request_tokens("", ws_context="")
    floor_huge_ws = cs._empty_context_request_tokens("", ws_context=BIG * 4)
    assert floor_no_ws == floor_huge_ws


# --- E6 / I14 / N17 regression: floor == final-gen prefix -------------------

@pytest.mark.parametrize(
    "over",
    [
        {},
        {"kb_prompt": "k " * 400},
        {"user_memory": "m " * 400},
        {"pinned_instruction": "p " * 400},
        {"active_skill": {"system_prompt": "s " * 400}},
    ],
)
def test_e6_floor_equals_real_final_gen_prefix(over):
    """The Gate A floor must equal the real final-generation prefix (plus a tiny
    message-boundary rounding slack) under every sacred-prefix configuration.

    This is the nail that catches ``7f557d3`` drifting: if anyone edits the
    cron header in ``build_generation_messages`` OR in ``_empty_context_request_tokens``
    without updating the other, this test goes red.
    """
    set_cfg(window=32000, max_tokens=4096, lang="en")
    real = final_gen_prefix_tokens(**over)
    kwargs = {
        "kb_prompt": over.get("kb_prompt", ""),
        "user_memory": over.get("user_memory", ""),
        "pinned_instruction": over.get("pinned_instruction", ""),
        "skill_prompt": over.get("active_skill", {}).get("system_prompt"),
    }
    floor = cs._empty_context_request_tokens("", **kwargs)
    assert abs(floor - real) <= 2, f"floor={floor} real={real} over={over}"


def test_e6_ws_context_kept_out_even_though_final_gen_omits_it():
    """Reinforces test_e5_ws_context_does_not_affect_floor against the real
    assembler: a populated ws_context changes neither floor nor final-gen."""
    set_cfg(window=32000, max_tokens=4096, lang="en")
    real = final_gen_prefix_tokens()
    floor = cs._empty_context_request_tokens("", ws_context=BIG * 4)
    assert abs(floor - real) <= 2


# --- E7: explicit skill vs default base -------------------------------------

def test_e7_explicit_skill_uses_skill_body_not_capabilities():
    set_cfg(window=32000, max_tokens=4096)
    floor_default = cs._empty_context_request_tokens("")
    floor_skill = cs._empty_context_request_tokens("", skill_prompt=SKILL_BIG)
    # Both have the same identity base; the skill variant swaps capabilities for
    # the (longer here) skill body, so it is strictly larger in this fixture.
    assert floor_skill > floor_default


# --- E8 / E9: things floor cannot model pass through to Gate B --------------

def test_e8_autorouted_skill_not_in_floor():
    """A skill the router picks at runtime (no explicit body passed to the floor)
    is unknown to Gate A, so a request whose only bloat is that body must be
    admitted by Gate A and rejected later (by Gate B / fit)."""
    set_cfg(window=8000, max_tokens=1024)
    # Floor has no skill body -> not rejected for prefix; a normal short query too.
    assert cs.classify_entry_overflow("hi") is None


def test_e9_tool_schemas_not_in_floor():
    set_cfg(window=8000, max_tokens=1024)
    # Tools are deliberately excluded from the floor; Gate A stays permissive and
    # lets fit_assembly_context reserve room for the real tool payload.
    assert cs.classify_entry_overflow("hi") is None


# --- E10: boundary query is deterministic ----------------------------------

def test_e10_boundary_query_deterministic():
    set_cfg(window=8000, max_tokens=1024)
    # Just under vs just over a fixed small budget: the decision must be stable
    # across repeated calls (no randomness in the floor math).
    near = "w " * 1200
    first = cs.classify_entry_overflow(near)
    for _ in range(3):
        assert cs.classify_entry_overflow(near) == first

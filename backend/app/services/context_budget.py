"""Single source of truth for slicing the LLM context window into budget slots.

Every layer that needs to answer "how many tokens may X occupy" reads it from
here. Before this module the summary layer guessed a flat
``SUMMARY_FIXED_OVERHEAD_TOKENS = 16000`` for "system prompt + RAG + memory +
tool defs" because it could not see any of those components. They are explicit
slots now, so the guess is retired.

Layout of one request::

    window   = llm_context_window                        # model hard limit
    total    = window - (max_tokens + SAFETY_MARGIN)     # max input tokens
    avail    = total - r_prefix - r_tools                # incompressible head off
    r_rag    = RAG_BUDGET_PCT    % of avail
    r_memory = MEMORY_BUDGET_PCT % of avail
    P        = avail - r_rag - r_memory                  # the persistent slot

``P`` is the only slot that accumulates ACROSS turns: un-summarized history plus
the L0 rolling summary. RAG and memory are re-retrieved every turn, and prefix /
tool schemas are fixed per configuration, so none of them can grow unbounded --
which is exactly why the compaction watermarks are measured against ``P`` rather
than against ``window``. Applying 70% / 80% to ``window`` would double-count
every other slot and fire far too late.

Why the enhancement percentages are cut from ``avail`` and not from ``total``:
the prefix and the tool schemas are incompressible. Taking the shares off what
is left AFTER them keeps ``P`` non-negative and monotonic at every window size,
including an 8k window where prefix + tools alone can eat a third of the budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.services.config_manager import config_manager, SUMMARY_SAFETY_MARGIN
from app.services.i18n import t as _t
from app.services.token_count import count_messages_tokens, count_text_tokens

# Share of ``avail`` reserved for the two query-driven enhancements. Both are
# re-retrieved from scratch every turn, so over-reserving only wastes headroom
# for a single turn while under-reserving makes fit_assembly_context trim on
# every turn. Kept as module constants (not Settings fields) until the UI
# contract is finalized -- see Step 7 of the context refactor plan.
RAG_BUDGET_PCT = 25
MEMORY_BUDGET_PCT = 10

# Compaction watermarks, measured against P (never against window).
ASYNC_HI_FRAC = 0.70  # kick off summarization in the background
SYNC_HI_FRAC = 0.80  # block the turn on summarization

# Tool-schema reserve used before the process has ever seen a real ``tools=``
# payload (first turn after boot, config-time validation). Once the assembly
# point reports a measurement via record_tool_tokens() that number takes over.
FALLBACK_TOOL_TOKENS = 2048

# High-water mark of every tool payload measured so far this process. Kept as a
# plain module global: a lone int assignment is atomic under CPython and the
# value is an approximation feeding a pre-assembly estimate, so a lock would buy
# nothing. MAX rather than "most recent" because the tool set varies per
# conversation (skills add tools) and under-reserving is the harmful direction:
# it inflates P, which delays compaction until fit_assembly_context has to trim.
_observed_tool_tokens: int = 0


@dataclass(frozen=True)
class ContextBudget:
    """One computed slicing of the context window. Immutable snapshot."""

    window: int
    total: int  # window - (max_tokens + SAFETY_MARGIN)
    r_prefix: int
    r_tools: int
    r_rag: int
    r_memory: int
    persistent: int  # == P

    @property
    def reserved(self) -> int:
        """Everything that is NOT the persistent slot.

        This is the measured replacement for the retired
        ``SUMMARY_FIXED_OVERHEAD_TOKENS`` constant.
        """
        return self.r_prefix + self.r_tools + self.r_rag + self.r_memory

    @property
    def async_hi(self) -> int:
        """Persistent-block level above which summarization runs in background."""
        return int(self.persistent * ASYNC_HI_FRAC)

    @property
    def sync_hi(self) -> int:
        """Persistent-block level above which summarization blocks the turn."""
        return int(self.persistent * SYNC_HI_FRAC)

    def l0_cap(self, pct: int | None = None) -> int:
        """Token ceiling for the L0 rolling summary before the archive chain fires.

        ``summary_archive_high_pct`` (default 40) keeps its config key but its
        denominator changed from ``window`` to ``P``: an L0 that owns 40% of the
        persistent block leaves 60% for verbatim history, which is the ratio the
        setting was always meant to express.
        """
        share = config_manager.summary_archive_high_pct if pct is None else pct
        return int(self.persistent * (max(0, share) / 100.0))


def total_budget() -> int:
    """Max input tokens allowed: window minus the reserved output allowance.

    Pure arithmetic, no tokenization -- safe to call from hot loops. This is the
    canonical definition of ``total``; ``conversation_summary._budget()``
    delegates here so the two can never drift.
    """
    return config_manager.context_window - (
        config_manager.max_tokens + SUMMARY_SAFETY_MARGIN
    )


@lru_cache(maxsize=8)
def _prefix_tokens_cached(lang: str, system_prompt: str) -> int:
    tool_sys = _t("tool_system", lang, tool_desc="")
    task_bg = "## Task Background (reference only)\n" + system_prompt
    return count_messages_tokens(
        [
            {"role": "system", "content": tool_sys},
            {"role": "system", "content": task_bg},
        ]
    )


def estimate_prefix_tokens() -> int:
    """Token cost of the unconditional system prefix emitted by ``_assemble``.

    Mirrors the head of ``agent_nodes._assemble`` (tool-mode system prompt + the
    "Task Background" block built from the configured system prompt). This is a
    FLOOR: an active skill body, a KB blurb or user memory adds more on top, and
    the assembly point passes its own measured value. Memoized on (language,
    system prompt) so callers pay tokenization once per configuration.
    """
    return _prefix_tokens_cached(
        config_manager.prompt_language,
        (config_manager.system_prompt_identity + "\n\n" + config_manager.system_prompt_capabilities) or "",
    )


def compute(*, prefix_tokens: int, tool_tokens: int) -> ContextBudget:
    """Slice the window into slots.

    ``prefix_tokens`` / ``tool_tokens`` are the MEASURED costs at the assembly
    point -- both are in hand there, so nothing is estimated. Call sites that
    run before assembly use :func:`default_budget` instead.
    """
    total = total_budget()
    r_prefix = max(0, int(prefix_tokens))
    r_tools = max(0, int(tool_tokens))
    # Clamped at 0: a misconfigured window (max_tokens >= window) makes `total`
    # negative. Keep the raw `total` on the snapshot for diagnostics but never
    # let a negative propagate into the percentage math below.
    avail = max(0, total - r_prefix - r_tools)
    r_rag = avail * RAG_BUDGET_PCT // 100
    r_memory = avail * MEMORY_BUDGET_PCT // 100
    return ContextBudget(
        window=config_manager.context_window,
        total=total,
        r_prefix=r_prefix,
        r_tools=r_tools,
        r_rag=r_rag,
        r_memory=r_memory,
        persistent=avail - r_rag - r_memory,
    )


def record_tool_tokens(n: int) -> None:
    """Feed the assembly point's MEASURED tool-schema cost back to the budget
    center, so pre-assembly call sites stop guessing after the first turn.

    Called from ``fit_assembly_context``, which already counts the payload it is
    about to send -- the measurement is free there.
    """
    global _observed_tool_tokens
    if n > _observed_tool_tokens:
        _observed_tool_tokens = int(n)


def default_tool_tokens() -> int:
    """Best available tool-schema reserve for pre-assembly call sites."""
    return _observed_tool_tokens or FALLBACK_TOOL_TOKENS


def default_budget() -> ContextBudget:
    """Budget with a measured prefix and the best available tool reserve.

    For call sites that run BEFORE the agent graph is assembled (turn-start
    compaction planning, config-time validation) where the real ``tools=``
    payload does not exist yet. Far better than a flat constant: it tracks the
    configured model, language, system prompt, and -- after the first turn --
    the tool payload actually observed on the wire.
    """
    return compute(
        prefix_tokens=estimate_prefix_tokens(), tool_tokens=default_tool_tokens()
    )


# ---------------------------------------------------------------------------
# Config-time validation of user-authored prompt text
# ---------------------------------------------------------------------------

# Soft ceiling for ONE hand-written field (system prompt, KB instruction,
# profile memory, pinned instruction). Every one of these rides in the request
# on every single turn, so a bloated field permanently shrinks the persistent
# slot: history gets compacted sooner and RAG returns fewer chunks.
#
# min(percentage, absolute) rather than a bare percentage because the two
# degenerate at opposite ends of the window range. On a 1M-token window 10%
# would be 100k tokens -- no hand-written instruction should ever approach
# that, so the absolute cap becomes the binding one. On an 8k window the
# absolute cap never fires and the percentage does the work.
FIELD_WARN_PCT = 10
FIELD_WARN_ABS = 10_000


def field_warn_limit() -> int:
    """Token count above which a single authored field is flagged."""
    return min(
        config_manager.context_window * FIELD_WARN_PCT // 100, FIELD_WARN_ABS
    )


def check_field_budget(text: str | None, field: str) -> list[dict]:
    """Config-time soft check for one user-authored prompt field.

    Returns a list of structured warnings (empty = OK) shaped exactly like
    :meth:`config_manager.validate_compression_budget` output, i.e.
    ``{"code": <BARE_CODE>, "params": {...}}``. Per project convention the
    backend never bakes user-facing copy -- the frontend maps ``code`` (and the
    ``field`` param) to localized text.

    This is NON-BLOCKING by design: the save always succeeds. Runtime safety is
    already guaranteed elsewhere (Gate A rejects a prefix that cannot fit, and
    ``fit_assembly_context`` trims whatever still overflows). The point here is
    to make the cost visible AT EDIT TIME, where the author can act on it,
    instead of surfacing it as a mid-inference failure hours later.

    ``field`` is a bare identifier (``system_prompt`` / ``kb_prompt`` /
    ``user_memory`` / ``pinned_instruction``), not a display label.
    """
    text = (text or "").strip()
    if not text:
        return []
    tok = count_text_tokens(text)
    limit = field_warn_limit()
    if tok <= limit:
        return []
    window = max(1, config_manager.context_window)
    return [
        {
            "code": "PROMPT_FIELD_TOO_LARGE",
            "params": {
                "field": field,
                "tok": tok,
                "limit": limit,
                "pct": round(tok * 100 / window, 1),
                "cw": config_manager.context_window,
            },
        }
    ]

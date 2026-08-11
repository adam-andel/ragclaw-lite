"""Scaffolding (plan S0) for the context-compression test suite.

Design rules that make this suite fast and deterministic:

* **Single LLM seam.** The only outbound model call in the compression pipeline
  is ``llm_client.chat``. Every test that needs a summary patches that one
  function, so no test ever touches the network.
* **Shrink the window, not the data.** Overflow is provoked by pushing
  ``llm_context_window`` down to a few thousand tokens rather than by building
  hundreds of kilobytes of fake history. Same code path, ~1000x cheaper.
* **Zero global bleed.** ``config_manager`` is a process singleton and the
  budget center keeps a module-level tool-token high-water mark plus an
  ``lru_cache``; both are snapshotted and restored around every test.

Builders and assertion helpers live in ``helpers.py`` so test modules can import
them without fighting pytest's import-mode resolution.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import context_budget as cb  # noqa: E402
from app.services import conversation_summary as cs  # noqa: E402
from app.services.config_manager import config_manager  # noqa: E402

from helpers import set_cfg  # noqa: E402


# ---------------------------------------------------------------------------
# Global-state isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def ctx_env():
    """Give each test a pristine config + budget center, restore afterwards.

    ``config_manager.init()`` is async and hits the DB / encrypted key file;
    ``_build_defaults()`` is the synchronous half that produces exactly the
    values a fresh install would run with, which is all these tests need.
    """
    had_defaults = hasattr(config_manager, "_defaults")
    prev_defaults = getattr(config_manager, "_defaults", None)
    prev_config = getattr(config_manager, "_config", None)

    config_manager._defaults = config_manager._build_defaults()
    config_manager._config = dict(config_manager._defaults)

    prev_tool_tokens = cb._observed_tool_tokens
    cb._observed_tool_tokens = 0
    cb._prefix_tokens_cached.cache_clear()
    prev_inflight = set(cs._INFLIGHT)
    cs._INFLIGHT.clear()

    yield

    cb._observed_tool_tokens = prev_tool_tokens
    cb._prefix_tokens_cached.cache_clear()
    cs._INFLIGHT.clear()
    cs._INFLIGHT.update(prev_inflight)
    if had_defaults:
        config_manager._defaults = prev_defaults
        config_manager._config = prev_config
    else:  # pragma: no cover - only when the singleton was never initialized
        del config_manager._defaults
        if prev_config is None:
            del config_manager._config


@pytest.fixture
def cfg():
    """Expose :func:`set_cfg` as a fixture for readability inside tests."""
    return set_cfg


@pytest.fixture
def tiny_window():
    """A window small enough that a handful of paragraphs overflows it.

    2000 total window minus (512 output + safety margin) leaves roughly a
    thousand input tokens -- enough for a realistic prefix-free assembly test
    while keeping every fixture string short.
    """
    set_cfg(window=2000, max_tokens=512)
    return 2000


class FakeLLM:
    """Records calls and returns canned summaries; substitutes ``llm_client``."""

    def __init__(self, reply: str = "SUMMARY", fail: bool = False):
        self.reply = reply
        self.fail = fail
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self.fail:
            raise RuntimeError("llm boom")
        if callable(self.reply):
            return self.reply(messages, **kwargs)
        return self.reply

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def fake_llm(monkeypatch):
    """Patch the single LLM seam. Returns a factory so a test can pick the
    canned reply / failure mode, e.g. ``fake_llm(reply='X')``."""

    def _install(reply: str = "SUMMARY", fail: bool = False) -> FakeLLM:
        stub = FakeLLM(reply, fail)
        monkeypatch.setattr(cs.llm_client, "chat", stub.chat, raising=True)
        return stub

    return _install

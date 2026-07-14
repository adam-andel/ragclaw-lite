"""Unit tests for ConfigManager.context_window property.

The ConfigManager is a process-wide singleton; tests save/restore its
internal _config dict to stay isolated from each other and from the app.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services.config_manager import config_manager


@pytest.fixture
def isolated_config():
    """Save and restore config_manager._config around a test."""
    saved = config_manager._config
    config_manager._config = {}
    try:
        yield
    finally:
        config_manager._config = saved


class TestContextWindowProperty:
    def test_default_when_unset(self, isolated_config):
        assert config_manager.context_window == 128000

    def test_returns_stored_value(self, isolated_config):
        config_manager._config = {"llm_context_window": 64000}
        assert config_manager.context_window == 64000

    def test_handles_non_int_gracefully(self, isolated_config):
        config_manager._config = {"llm_context_window": "not-a-number"}
        assert config_manager.context_window == 128000

    def test_handles_none_gracefully(self, isolated_config):
        config_manager._config = {"llm_context_window": None}
        assert config_manager.context_window == 128000

    def test_seeded_default_is_128000(self, isolated_config):
        from app.services.config_manager import DEFAULT_SYSTEM_PROMPT
        defaults = config_manager._build_non_sensitive_defaults()
        assert defaults["llm_context_window"] == 128000
        assert DEFAULT_SYSTEM_PROMPT  # sanity: module import intact

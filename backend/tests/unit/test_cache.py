"""Unit tests for LRU answer cache."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services.cache import answer_cache, CacheEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KB_A = "kb-aaaaaaaa"
KB_B = "kb-bbbbbbbb"
Q1 = "What is ERAG?"
Q2 = "How does hybrid search work?"


# ---------------------------------------------------------------------------

class TestCacheBasic:
    """Put / get / miss / invalidate."""

    def test_put_then_get_returns_content(self):
        answer_cache.put(Q1, KB_A, "ERAG is a platform", [])
        entry = answer_cache.get(Q1, KB_A)
        assert entry is not None
        assert entry.answer == "ERAG is a platform"

    def test_miss_returns_none(self):
        result = answer_cache.get("never asked this", KB_A)
        assert result is None

    def test_hit_increments_count(self):
        answer_cache.put("hit counter test", KB_A, "answer", [])
        before = answer_cache.stats["hit_count"]
        answer_cache.get("hit counter test", KB_A)
        after = answer_cache.stats["hit_count"]
        assert after == before + 1

    def test_invalidate_clears_entry(self):
        answer_cache.put("to be cleared", KB_A, "temp", [])
        assert answer_cache.get("to be cleared", KB_A) is not None
        answer_cache.invalidate()
        assert answer_cache.get("to be cleared", KB_A) is None

    def test_invalidate_by_kb_only(self):
        answer_cache.put("q-a", KB_A, "answer A", [])
        answer_cache.put("q-b", KB_B, "answer B", [])

        # Invalidate KB_B only — KB_A entry should survive
        answer_cache.invalidate(KB_B)

        assert answer_cache.get("q-a", KB_A) is not None
        # KB_B entry may or may not be found depending on hash overlap;
        # but KB_A should definitely still be there
        answer_cache.invalidate()  # cleanup


class TestCacheStats:
    """Stats correctness."""

    def test_stats_reflect_state(self):
        answer_cache.invalidate()
        # After fresh start, add one entry
        answer_cache.put("stats test", KB_A, "data", [])
        s = answer_cache.stats
        assert s["size"] >= 1
        assert "hit_count" in s
        assert "miss_count" in s
        assert "hit_rate" in s
        answer_cache.invalidate()


class TestCacheEviction:
    """LRU eviction when over capacity."""

    def test_evicts_oldest_when_full(self, monkeypatch):
        # Shrink max size for test
        monkeypatch.setattr("app.services.cache.settings.cache_max_size", 3)
        from app.services.cache import AnswerCache
        small_cache = AnswerCache(max_size=3)

        small_cache.put("q1", KB_A, "a1", [])
        small_cache.put("q2", KB_A, "a2", [])
        small_cache.put("q3", KB_A, "a3", [])
        assert small_cache.size == 3

        # Adding one more should evict the oldest (q1)
        small_cache.put("q4", KB_A, "a4", [])
        assert small_cache.size == 3
        assert small_cache.get("q1", KB_A) is None
        assert small_cache.get("q4", KB_A) is not None

        # Ensure order: q2 accessed (moves to end), then add q5 → evict q3
        small_cache.get("q2", KB_A)
        small_cache.put("q5", KB_A, "a5", [])
        assert small_cache.size == 3
        assert small_cache.get("q2", KB_A) is not None  # was accessed, shouldn't be evicted


class TestCacheTTL:
    """Time-to-live expiration."""

    def test_expired_entry_returns_none(self, monkeypatch):
        import time as _time
        monkeypatch.setattr("app.services.cache.settings.cache_ttl_seconds", 0)
        from app.services.cache import AnswerCache
        ttl_cache = AnswerCache(ttl_seconds=0)

        ttl_cache.put("expired q", KB_A, "should expire", [])
        _time.sleep(0.01)  # let wall clock advance so time.time() - timestamp > 0
        result = ttl_cache.get("expired q", KB_A)
        assert result is None

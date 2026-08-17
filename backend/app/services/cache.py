"""Simple LRU answer cache to reduce LLM token costs."""

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock

from app.config import settings


@dataclass
class CacheEntry:
    answer: str
    citations: list[dict]
    timestamp: float = field(default_factory=time.time)
    hit_count: int = 0


class AnswerCache:
    """Thread-safe LRU cache mapping query → answer."""

    def __init__(self, max_size: int | None = None, ttl_seconds: int | None = None):
        self.max_size = max_size or settings.cache_max_size
        # ttl_seconds=0 is a valid "expire immediately" value, so it must NOT be
        # swallowed by a truthiness check (the previous `or` clause dropped it).
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.cache_ttl_seconds
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._kb_index: dict[str, set[str]] = {}
        self._lock = Lock()
        self._hit_count: int = 0
        self._miss_count: int = 0

    def _make_key(self, query: str, kb_id: str, skill_id: str = "", kb_prompt: str = "") -> str:
        """Generate cache key from query + kb_id + optional skill_id + KB prompt.

        kb_prompt is included so that editing a KB's instruction busts the
        cached answers (they were generated under the old instruction).
        """
        raw = f"{skill_id}:{kb_id}:{kb_prompt}:{query.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _remove_from_index(self, key: str) -> None:
        """Drop a key from the kb_id → keys index (best effort)."""
        for keys in self._kb_index.values():
            keys.discard(key)

    def get(self, query: str, kb_id: str, skill_id: str = "", kb_prompt: str = "") -> CacheEntry | None:
        """Look up a cached answer. Returns None on miss or expiry."""
        if not settings.cache_enabled:
            return None

        key = self._make_key(query, kb_id, skill_id, kb_prompt)

        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._miss_count += 1
                return None

            if (time.time() - entry.timestamp) > self.ttl_seconds:
                del self._store[key]
                self._remove_from_index(key)
                self._miss_count += 1
                return None

            # Move to end (LRU)
            self._store.move_to_end(key)
            entry.hit_count += 1
            self._hit_count += 1
            return entry

    def put(self, query: str, kb_id: str, answer: str, citations: list[dict], skill_id: str = "", kb_prompt: str = ""):
        """Store an answer in the cache."""
        if not settings.cache_enabled:
            return

        key = self._make_key(query, kb_id, skill_id, kb_prompt)

        with self._lock:
            # Evict oldest if at capacity
            while len(self._store) >= self.max_size:
                old_key, _ = self._store.popitem(last=False)
                self._remove_from_index(old_key)

            self._store[key] = CacheEntry(answer=answer, citations=citations)
            self._kb_index.setdefault(kb_id, set()).add(key)

    def invalidate(self, kb_id: str | None = None):
        """Invalidate cache entries. If kb_id is None, clear all."""
        with self._lock:
            if kb_id is None:
                self._store.clear()
                self._kb_index.clear()
                return

            for key in self._kb_index.pop(kb_id, set()):
                self._store.pop(key, None)

    @property
    def hit_rate(self) -> float:
        total = self._hit_count + self._miss_count
        if total == 0:
            return 0.0
        return self._hit_count / total

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)

    @property
    def stats(self) -> dict:
        return {
            "size": self.size,
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "hit_rate": self.hit_rate,
        }


# Singleton
answer_cache = AnswerCache()

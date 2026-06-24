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

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > settings.cache_ttl_seconds


class AnswerCache:
    """Thread-safe LRU cache mapping query → answer."""

    def __init__(self, max_size: int | None = None, ttl_seconds: int | None = None):
        self.max_size = max_size or settings.cache_max_size
        self.ttl_seconds = ttl_seconds or settings.cache_ttl_seconds
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = Lock()
        self._hit_count: int = 0
        self._miss_count: int = 0

    def _make_key(self, query: str, kb_id: str, skill_id: str = "") -> str:
        """Generate cache key from query + kb_id + optional skill_id."""
        raw = f"{skill_id}:{kb_id}:{query.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, query: str, kb_id: str, skill_id: str = "") -> CacheEntry | None:
        """Look up a cached answer. Returns None on miss or expiry."""
        if not settings.cache_enabled:
            return None

        key = self._make_key(query, kb_id, skill_id)

        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._miss_count += 1
                return None

            if entry.is_expired:
                del self._store[key]
                self._miss_count += 1
                return None

            # Move to end (LRU)
            self._store.move_to_end(key)
            entry.hit_count += 1
            self._hit_count += 1
            return entry

    def put(self, query: str, kb_id: str, answer: str, citations: list[dict], skill_id: str = ""):
        """Store an answer in the cache."""
        if not settings.cache_enabled:
            return

        key = self._make_key(query, kb_id, skill_id)

        with self._lock:
            # Evict oldest if at capacity
            while len(self._store) >= self.max_size:
                self._store.popitem(last=False)

            self._store[key] = CacheEntry(answer=answer, citations=citations)

    def invalidate(self, kb_id: str | None = None):
        """Invalidate cache entries. If kb_id is None, clear all."""
        with self._lock:
            if kb_id is None:
                self._store.clear()
                return

            to_remove = [
                k for k in self._store
                if kb_id in k  # key format: sha256(kb_id:query)
            ]
            for k in to_remove:
                del self._store[k]

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

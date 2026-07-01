"""Process-wide concurrency limiter for LLM API calls.

Provides a FIFO queue with real-time position notifications so that callers
can stream "you are N positions away" updates to clients while waiting for a
token. Tokens are held for the entire request lifecycle (including all internal
LLM calls made by the agent graph) and released only after the final answer has
been produced.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Awaitable, Callable

PositionCallback = Callable[[int], Awaitable[None]]


@dataclass
class _Waiter:
    callback: PositionCallback | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    woken: bool = False


class LLMConcurrencyLimiter:
    """Async concurrency limiter with queue position notifications.

    Usage:
        async def on_position(pos: int) -> None:
            # pos == 0 means token acquired; pos > 0 means N ahead in queue
            await send_sse({"type": "queue", "position": pos})

        async with llm_limiter.acquire(on_position):
            # token held - safe to call LLM
            ...
    """

    def __init__(self, max_concurrency: int = 3):
        self._max = max(max_concurrency, 1)
        self._active = 0
        self._waiters: list[_Waiter] = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, on_position: PositionCallback | None = None):
        """Acquire a token; yield once held. Releases on exit or cancellation."""
        waiter: _Waiter | None = None
        slot_taken = False
        try:
            # Phase 1: reserve a slot or join the queue while holding the lock.
            async with self._lock:
                if self._active < self._max:
                    self._active += 1
                    slot_taken = True
                    if on_position:
                        await on_position(0)
                else:
                    waiter = _Waiter(callback=on_position)
                    self._waiters.append(waiter)
                    await self._notify_all_locked()

            if slot_taken:
                yield
                return

            # Phase 2: wait for a token without holding the lock so other tasks
            # can enter/leave the queue and we can be cancelled cleanly.
            await waiter.event.wait()

            # Woken up: slot already reserved for us in _wake_next_locked.
            slot_taken = True
            if waiter.callback:
                await waiter.callback(0)
            yield
        except asyncio.CancelledError:
            async with self._lock:
                if waiter is not None:
                    if waiter in self._waiters:
                        # Cancelled while waiting; just leave the queue.
                        self._waiters.remove(waiter)
                        await self._notify_all_locked()
                    elif waiter.woken:
                        # Slot was reserved for us but we were cancelled before
                        # using it; give it back to the next waiter.
                        self._active = max(0, self._active - 1)
                        slot_taken = False
                        await self._wake_next_locked()
                elif slot_taken:
                    # Immediate acquire path cancelled before or during yield.
                    self._active = max(0, self._active - 1)
                    slot_taken = False
                    await self._wake_next_locked()
            raise
        finally:
            if slot_taken:
                async with self._lock:
                    self._active = max(0, self._active - 1)
                    await self._wake_next_locked()

    async def _wake_next_locked(self) -> None:
        """Hand a released slot to the next waiter and update positions."""
        if self._waiters and self._active < self._max:
            waiter = self._waiters.pop(0)
            waiter.woken = True
            self._active += 1
            waiter.event.set()
            # The waiter itself will call callback(0) after waking up, so we
            # only need to update the positions of the remaining queue.
            await self._notify_all_locked()

    async def _notify_all_locked(self) -> None:
        """Notify queued waiters of their current position (1-based)."""
        for i, waiter in enumerate(self._waiters, start=1):
            if waiter.callback:
                await waiter.callback(i)

    async def update_max(self, max_concurrency: int) -> None:
        """Change the concurrency limit. Does not revoke active tokens.

        New requests and queued waiters are affected immediately. If the limit
        is increased, extra waiters are woken up to fill the new slots.
        """
        async with self._lock:
            old_max = self._max
            self._max = max(max_concurrency, 1)
            extra_slots = self._max - old_max
            for _ in range(extra_slots):
                if self._active < self._max and self._waiters:
                    await self._wake_next_locked()
                else:
                    break

    @property
    def max_concurrency(self) -> int:
        return self._max

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def waiting_count(self) -> int:
        return len(self._waiters)


# Singleton instance wired to runtime config.
llm_limiter = LLMConcurrencyLimiter()

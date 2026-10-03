"""Small async TTL cache with single-flight: concurrent identical requests share one result."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class TTLCache:
    def __init__(self, max_entries: int = 2000) -> None:
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Future[Any]] = {}  # key → running fetch task
        self._max = max_entries

    def get(self, key: Hashable) -> tuple[bool, Any]:
        item = self._data.get(key)
        if item is None:
            return False, None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return False, None
        return True, value

    def set(self, key: Hashable, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        if len(self._data) >= self._max:
            now = time.monotonic()
            for k in [k for k, (exp, _) in self._data.items() if exp < now]:
                self._data.pop(k, None)
            if len(self._data) >= self._max:
                # Drop the oldest third.
                for k in list(self._data)[: self._max // 3]:
                    self._data.pop(k, None)
        self._data[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        self._data.clear()
        self._inflight.clear()

    async def get_or_fetch(self, key: Hashable, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
        hit, value = self.get(key)
        if hit:
            return value
        task = self._inflight.get(key)
        if task is None or task.done():
            # The fetch runs as its own task: cancelling one caller never cancels the others.
            task = asyncio.ensure_future(self._run(key, ttl, fetch))
            task.add_done_callback(_consume_exception)
            self._inflight[key] = task
        return await asyncio.shield(task)

    async def _run(self, key: Hashable, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
        try:
            value = await fetch()
            self.set(key, value, ttl)
            return value
        finally:
            current = asyncio.current_task()
            if self._inflight.get(key) is current:
                self._inflight.pop(key, None)


def _consume_exception(task: asyncio.Future[Any]) -> None:
    """Mark the exception as retrieved so an abandoned fetch does not log 'never retrieved'."""
    if not task.cancelled():
        task.exception()

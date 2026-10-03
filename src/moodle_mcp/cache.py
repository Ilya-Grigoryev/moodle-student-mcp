"""Small async TTL cache with single-flight: concurrent identical requests share one result."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class TTLCache:
    def __init__(self, max_entries: int = 2000) -> None:
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Future[Any]] = {}
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

    async def get_or_fetch(self, key: Hashable, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
        hit, value = self.get(key)
        if hit:
            return value
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._inflight[key] = future
        try:
            value = await fetch()
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()  # mark retrieved: waiters re-raise it themselves
            raise
        else:
            self.set(key, value, ttl)
            if not future.done():
                future.set_result(value)
            return value
        finally:
            self._inflight.pop(key, None)

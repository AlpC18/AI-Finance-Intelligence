"""Async cache abstraction. In-memory now; swap to Redis for multi-worker."""
import asyncio
import time
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable

from app.core.metrics import record_cache_hit, record_cache_miss


@runtime_checkable
class CacheBackend(Protocol):
    async def get(self, key: str) -> Optional[Any]: ...
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None: ...
    async def get_or_set(
        self, key: str, factory: Callable[[], Awaitable[Any]], ttl: Optional[int] = None
    ) -> Any: ...


class InMemoryTTLCache:
    """Process-local TTL cache. NOT shared across Uvicorn workers -> use Redis then."""

    _BACKEND = "memory"

    def __init__(self, default_ttl: int) -> None:
        self._default_ttl = default_ttl
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, value = item
            if time.monotonic() > expires_at:
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic() + (ttl or self._default_ttl), value)

    async def get_or_set(
        self, key: str, factory: Callable[[], Awaitable[Any]], ttl: Optional[int] = None
    ) -> Any:
        cached = await self.get(key)
        if cached is not None:
            record_cache_hit(self._BACKEND)
            return cached
        record_cache_miss(self._BACKEND)
        value = await factory()
        await self.set(key, value, ttl)
        return value

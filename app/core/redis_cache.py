"""Redis-backed CacheBackend. Falls back to in-memory on any Redis error."""
import logging
import pickle
from typing import Any, Awaitable, Callable, Optional

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.cache import CacheBackend
from app.core.metrics import record_cache_hit, record_cache_miss

logger = logging.getLogger("redis_cache")
_BACKEND = "redis"


class RedisCache:
    """Distributed cache. Shared across Uvicorn workers, unlike the in-memory one."""

    _PREFIX = ""

    def __init__(self, url: str, default_ttl: int, fallback: CacheBackend) -> None:
        self._client: Redis = Redis.from_url(url)
        self._ttl = default_ttl
        self._fallback = fallback

    async def get(self, key: str) -> Optional[Any]:
        try:
            raw = await self._client.get(key)
            # B301 is suppressed on the line below: values are written only by
            # this app to a private Redis instance (trusted provider output),
            # never untrusted external input.
            return pickle.loads(raw) if raw is not None else None  # nosec B301
        except (RedisError, pickle.PickleError, OSError) as exc:
            logger.warning("Redis get failed (%s); using fallback.", exc)
            return await self._fallback.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            await self._client.set(key, pickle.dumps(value), ex=ttl or self._ttl)
        except (RedisError, pickle.PickleError, OSError) as exc:
            logger.warning("Redis set failed (%s); using fallback.", exc)
            await self._fallback.set(key, value, ttl)

    async def get_or_set(
        self, key: str, factory: Callable[[], Awaitable[Any]], ttl: Optional[int] = None
    ) -> Any:
        try:
            raw = await self._client.get(key)
            if raw is not None:
                record_cache_hit(_BACKEND)
                # B301 suppressed as in get(): cache payloads are app-produced.
                return pickle.loads(raw)  # nosec B301
            record_cache_miss(_BACKEND)
            value = await factory()
            await self._client.set(key, pickle.dumps(value), ex=ttl or self._ttl)
            return value
        except (RedisError, pickle.PickleError, OSError) as exc:
            logger.warning("Redis get_or_set failed (%s); using fallback.", exc)
            return await self._fallback.get_or_set(key, factory, ttl)

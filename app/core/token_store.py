"""JWT revocation blocklist. Redis-backed with in-memory fallback (fail-open)."""
import logging
import time
from typing import Protocol

logger = logging.getLogger("token_store")


class TokenBlocklist(Protocol):
    async def block(self, jti: str, ttl_seconds: int) -> None: ...
    async def is_blocked(self, jti: str) -> bool: ...


class InMemoryBlocklist:
    """Process-local. Fine for a single worker / tests; not shared across workers."""

    def __init__(self) -> None:
        self._store: dict[str, float] = {}

    async def block(self, jti: str, ttl_seconds: int) -> None:
        self._store[jti] = time.monotonic() + max(ttl_seconds, 0)

    async def is_blocked(self, jti: str) -> bool:
        exp = self._store.get(jti)
        if exp is None:
            return False
        if time.monotonic() > exp:
            self._store.pop(jti, None)
            return False
        return True


class RedisTokenStore:
    """Shared blocklist across workers. Falls back to in-memory on Redis errors."""

    _PREFIX = "bl:"

    def __init__(self, url: str, fallback: TokenBlocklist) -> None:
        from redis.asyncio import Redis

        self._client = Redis.from_url(url)
        self._fallback = fallback

    async def block(self, jti: str, ttl_seconds: int) -> None:
        from redis.exceptions import RedisError

        try:
            await self._client.set(self._PREFIX + jti, "1", ex=max(ttl_seconds, 1))
        except (RedisError, OSError) as exc:
            logger.warning("Redis block failed (%s); using fallback.", exc)
            await self._fallback.block(jti, ttl_seconds)

    async def is_blocked(self, jti: str) -> bool:
        from redis.exceptions import RedisError

        try:
            return await self._client.exists(self._PREFIX + jti) == 1
        except (RedisError, OSError) as exc:
            # Fail-open: a Redis outage must not lock every user out.
            logger.warning("Redis is_blocked failed (%s); fallback only.", exc)
            return await self._fallback.is_blocked(jti)

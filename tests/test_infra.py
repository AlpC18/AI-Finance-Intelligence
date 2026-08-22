"""Infra coverage: in-memory cache, blocklist, Redis fail-open, DI singletons."""
import pytest
from redis.exceptions import RedisError

from app.core import deps
from app.core.cache import InMemoryTTLCache
from app.core.redis_cache import RedisCache
from app.core.token_store import InMemoryBlocklist, RedisTokenStore


@pytest.mark.asyncio
async def test_inmemory_cache_hit_miss_expiry():
    c = InMemoryTTLCache(default_ttl=60)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return "v"

    assert await c.get("k") is None
    assert await c.get_or_set("k", factory) == "v"   # miss -> factory
    assert await c.get_or_set("k", factory) == "v"   # hit -> cached
    assert calls["n"] == 1
    import time
    c._store["x"] = (time.monotonic() - 1, "y")      # force-expire an entry
    assert await c.get("x") is None


@pytest.mark.asyncio
async def test_inmemory_blocklist():
    b = InMemoryBlocklist()
    assert await b.is_blocked("j") is False
    await b.block("j", 60)
    assert await b.is_blocked("j") is True
    await b.block("z", 0)                             # expires immediately
    assert await b.is_blocked("z") is False


class _BadRedis:
    async def get(self, *a, **k): raise RedisError("down")
    async def set(self, *a, **k): raise RedisError("down")
    async def exists(self, *a, **k): raise RedisError("down")


@pytest.mark.asyncio
async def test_rediscache_fails_open_to_memory():
    fallback = InMemoryTTLCache(60)
    rc = RedisCache("redis://localhost:6379/0", 60, fallback)
    rc._client = _BadRedis()

    assert await rc.get("k") is None
    await rc.set("k", "v")

    async def factory():
        return "fresh"

    assert await rc.get_or_set("k2", factory) == "fresh"
    assert await fallback.get("k") == "v"             # write landed in fallback


@pytest.mark.asyncio
async def test_redis_token_store_fails_open():
    fallback = InMemoryBlocklist()
    ts = RedisTokenStore("redis://localhost:6379/0", fallback)
    ts._client = _BadRedis()
    await ts.block("j", 60)
    assert await ts.is_blocked("j") is True           # served from fallback


def test_di_singletons_construct():
    assert deps.get_cache() is not None
    assert deps.get_token_store() is not None
    assert deps.get_ai_service() is not None
    assert deps.get_market_service() is not None
    assert deps.get_news_service() is not None
    assert deps.get_portfolio_service() is not None
    assert deps.get_alert_service() is not None

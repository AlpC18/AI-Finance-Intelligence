"""Redis leader-election lock: single-worker execution across a job sweep."""
import pytest

from app.core import distributed_lock
from app.core.config import Settings
from app.core.distributed_lock import with_leader_lock


class _FakeLock:
    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired
        self.released = False

    async def acquire(self) -> bool:
        return self._acquired

    async def release(self) -> None:
        self.released = True


class _FakeRedis:
    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired

    def lock(self, name, timeout=None, blocking=False):
        return _FakeLock(self._acquired)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_memory_backend_runs_unguarded(monkeypatch):
    monkeypatch.setattr(distributed_lock, "get_settings", lambda: Settings(cache_backend="memory"))
    calls = []

    @with_leader_lock("job", 10)
    async def job() -> None:
        calls.append(1)

    await job()
    assert calls == [1]


@pytest.mark.asyncio
async def test_redis_leader_runs_when_lock_acquired(monkeypatch):
    monkeypatch.setattr(distributed_lock, "get_settings", lambda: Settings(cache_backend="redis"))

    async def _client(url):
        return _FakeRedis(acquired=True)

    monkeypatch.setattr(distributed_lock, "_lock_client", _client)
    calls = []

    @with_leader_lock("job", 10)
    async def job() -> None:
        calls.append(1)

    await job()
    assert calls == [1]


@pytest.mark.asyncio
async def test_redis_follower_skips_when_lock_held(monkeypatch):
    monkeypatch.setattr(distributed_lock, "get_settings", lambda: Settings(cache_backend="redis"))

    async def _client(url):
        return _FakeRedis(acquired=False)

    monkeypatch.setattr(distributed_lock, "_lock_client", _client)
    calls = []

    @with_leader_lock("job", 10)
    async def job() -> None:
        calls.append(1)

    await job()
    assert calls == []  # another worker holds the lock -> skip, no redundant calls


@pytest.mark.asyncio
async def test_redis_unavailable_fails_open(monkeypatch):
    monkeypatch.setattr(distributed_lock, "get_settings", lambda: Settings(cache_backend="redis"))

    async def _boom(url):
        raise RuntimeError("redis down")

    monkeypatch.setattr(distributed_lock, "_lock_client", _boom)
    calls = []

    @with_leader_lock("job", 10)
    async def job() -> None:
        calls.append(1)

    await job()
    assert calls == [1]  # fail-open: job still runs when Redis is unreachable

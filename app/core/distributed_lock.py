"""Redis leader-election lock so only one worker runs a scheduled sweep.

`with_leader_lock` wraps an async job: under a Redis cache backend it acquires an
auto-expiring, non-blocking distributed lock before running; if another worker
holds it, this worker skips the sweep silently (no redundant provider calls). In
single-worker/dev mode (memory backend) it runs unguarded, and if Redis is
unreachable it fails OPEN (runs best-effort) rather than dropping the job.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable, TypeVar

from typing_extensions import ParamSpec

from app.core.config import get_settings

logger = logging.getLogger("leader_lock")

P = ParamSpec("P")
_T = TypeVar("_T")
_LOCK_PREFIX = "afi:lock:"

AsyncJob = Callable[P, Awaitable[None]]


async def _lock_client(url: str) -> Any:
    """Build a redis.asyncio client. Isolated so tests can monkeypatch it."""
    from redis.asyncio import Redis

    return Redis.from_url(url)


async def _close(client: Any) -> None:
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001 - closing must never raise into the caller
        pass


def with_leader_lock(
    lock_name: str, ttl_seconds: int
) -> Callable[[AsyncJob], AsyncJob]:
    """Decorate an async job so at most one worker executes it per interval."""

    def decorator(job: AsyncJob) -> AsyncJob:
        @functools.wraps(job)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
            settings = get_settings()
            if settings.cache_backend != "redis":
                await job(*args, **kwargs)  # single worker: no coordination needed
                return

            try:
                client = await _lock_client(settings.redis_url)
                lock = client.lock(
                    _LOCK_PREFIX + lock_name, timeout=ttl_seconds, blocking=False
                )
                acquired = await lock.acquire()
            except Exception as exc:  # noqa: BLE001 - Redis down -> fail OPEN
                logger.warning(
                    "Leader lock '%s' unavailable (%s); running unguarded.",
                    lock_name, exc,
                )
                await job(*args, **kwargs)
                return

            if not acquired:
                logger.debug("Leader lock '%s' held elsewhere; skipping sweep.", lock_name)
                await _close(client)
                return

            try:
                await job(*args, **kwargs)
            finally:
                try:
                    await lock.release()
                except Exception:  # noqa: BLE001 - lock may have TTL-expired mid-job
                    pass
                await _close(client)

        return wrapper

    return decorator

"""Redis Streams work queue with an explicit local-safe fallback.

The queue transports only task names; task inputs are intentionally not accepted
from HTTP clients. Scheduled handlers remain server-owned, idempotent, and
leader-locked, so a Redis redelivery cannot turn into a duplicate execution.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from app.core.config import Settings

logger = logging.getLogger("task_queue")
_STREAM = "afi:jobs"
_GROUP = "afi-workers"


class TaskQueue(Protocol):
    async def enqueue(self, task_name: str) -> None: ...


class LocalTaskQueue:
    """Development adapter: execute the trusted handler asynchronously."""

    async def enqueue(self, task_name: str) -> None:
        asyncio.create_task(execute_task(task_name))


class RedisTaskQueue:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def enqueue(self, task_name: str) -> None:
        if task_name not in _handlers():
            raise ValueError(f"Unknown worker task: {task_name}")
        await self._redis.xadd(_STREAM, {"task": task_name}, maxlen=10_000, approximate=True)


def _handlers() -> dict[str, Any]:
    from app.core.scheduler import (
        run_alert_checks, run_event_scan, run_order_reconciliation,
        run_paper_automations, run_position_sync, run_risk_sweep,
    )
    return {
        "alert_checks": run_alert_checks,
        "event_scan": run_event_scan,
        "order_reconciliation": run_order_reconciliation,
        "paper_automations": run_paper_automations,
        "position_sync": run_position_sync,
        "risk_sweep": run_risk_sweep,
    }


async def execute_task(task_name: str) -> None:
    handler = _handlers().get(task_name)
    if handler is None:
        raise ValueError(f"Unknown worker task: {task_name}")
    await handler()


async def run_worker(settings: Settings, stop: asyncio.Event | None = None) -> None:
    """Consume Redis tasks as one named worker; retry is Redis redelivery."""
    if settings.cache_backend != "redis":
        logger.warning("Worker needs CACHE_BACKEND=redis; exiting.")
        return
    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    consumer = f"worker-{id(asyncio.current_task())}"
    try:
        try:
            await redis.xgroup_create(_STREAM, _GROUP, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP is expected after first worker
            if "BUSYGROUP" not in str(exc):
                raise
        while stop is None or not stop.is_set():
            rows = await redis.xreadgroup(
                _GROUP, consumer, {_STREAM: ">"}, count=1, block=1000
            )
            for _, messages in rows:
                for message_id, fields in messages:
                    name = fields.get("task", "")
                    try:
                        await execute_task(name)
                    except Exception:  # noqa: BLE001 - leave pending for operator/retry
                        logger.exception("Worker task failed", extra={"task": name, "id": message_id})
                        continue
                    await redis.xack(_STREAM, _GROUP, message_id)
    finally:
        await redis.aclose()

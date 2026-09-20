import asyncio
import sys
from types import SimpleNamespace

import pytest

from app.core.task_queue import LocalTaskQueue, RedisTaskQueue, execute_task, run_worker
from app.core.config import Settings
from app.core.scheduler import _scheduled_job


@pytest.mark.asyncio
async def test_redis_queue_rejects_unknown_task_without_touching_redis():
    class _Redis:
        async def xadd(self, *args, **kwargs):
            raise AssertionError("must not be called")

    with pytest.raises(ValueError):
        await RedisTaskQueue(_Redis()).enqueue("untrusted_http_task")


@pytest.mark.asyncio
async def test_redis_queue_adds_only_trusted_task(monkeypatch):
    calls = []

    class _Redis:
        async def xadd(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr("app.core.task_queue._handlers", lambda: {"alert_checks": object()})
    await RedisTaskQueue(_Redis()).enqueue("alert_checks")
    assert calls[0][0] == ("afi:jobs", {"task": "alert_checks"})


@pytest.mark.asyncio
async def test_execute_task_runs_registry_handler_and_rejects_unknown(monkeypatch):
    calls = []

    async def handler():
        calls.append("ran")

    monkeypatch.setattr("app.core.task_queue._handlers", lambda: {"test": handler})
    await execute_task("test")
    assert calls == ["ran"]
    with pytest.raises(ValueError):
        await execute_task("nope")


@pytest.mark.asyncio
async def test_local_queue_schedules_trusted_task(monkeypatch):
    called = asyncio.Event()

    async def _run(name):
        assert name == "alert_checks"
        called.set()

    monkeypatch.setattr("app.core.task_queue.execute_task", _run)
    await LocalTaskQueue().enqueue("alert_checks")
    await asyncio.wait_for(called.wait(), timeout=1)


@pytest.mark.asyncio
async def test_queue_scheduler_adapter_enqueues_trusted_name(monkeypatch):
    called = []

    class _Queue:
        async def enqueue(self, name):
            called.append(name)

    monkeypatch.setattr("app.core.deps.get_task_queue", lambda: _Queue())
    job = _scheduled_job(Settings(worker_queue_enabled=True), "event-scan", 10, lambda: None)
    await job()
    assert called == ["event_scan"]


@pytest.mark.asyncio
async def test_worker_consumes_acknowledges_and_closes_redis(monkeypatch):
    stop = asyncio.Event()
    calls = []

    class _Redis:
        async def xgroup_create(self, *args, **kwargs):
            calls.append("group")

        async def xreadgroup(self, *args, **kwargs):
            return [("afi:jobs", [("1-0", {"task": "alert_checks"})])]

        async def xack(self, *args):
            calls.append("ack")
            stop.set()

        async def aclose(self):
            calls.append("close")

    fake = _Redis()
    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *args, **kwargs: fake)

    async def execute(name):
        calls.append(name)

    monkeypatch.setattr("app.core.task_queue.execute_task", execute)
    await run_worker(Settings(cache_backend="redis"), stop)
    assert calls == ["group", "alert_checks", "ack", "close"]

"""Redis Pub/Sub WS fan-out: local delivery, publish, fallback, subscriber dispatch."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.core import scheduler
from app.core.config import Settings
from app.core.ws_broadcaster import WsBroadcaster
from app.models.alert import Alert


class _Manager:
    def __init__(self):
        self.sent = []

    async def send_to_user(self, user_id, frame):
        self.sent.append((user_id, frame))
        return 1


class _FakeRedis:
    """Publish + pubsub double; `messages` are replayed to the subscriber loop."""

    def __init__(self, fail=False, messages=None):
        self.published = []
        self._fail = fail
        self._messages = messages or []
        self.closed = False

    async def publish(self, channel, payload):
        if self._fail:
            raise RuntimeError("redis down")
        self.published.append((channel, payload))

    def pubsub(self):
        return self

    async def subscribe(self, channel):
        self.subscribed = channel

    async def listen(self):
        for m in self._messages:
            yield m
        # then idle until cancelled, like a real subscriber
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_local_only_delivers_without_redis():
    m = _Manager()
    b = WsBroadcaster(m, Settings(cache_backend="memory"))
    await b.start()  # no-op in memory mode
    await b.publish_alert(1, {"type": "alert", "symbol": "AAPL"})
    assert m.sent == [(1, {"type": "alert", "symbol": "AAPL"})]
    await b.stop()


@pytest.mark.asyncio
async def test_redis_publish_path_defers_to_subscribers():
    m = _Manager()
    b = WsBroadcaster(m, Settings(cache_backend="redis"), redis=_FakeRedis())
    await b.publish_alert(7, {"type": "alert", "symbol": "MSFT"})
    assert m.sent == []  # not delivered inline; fan-out happens via subscription
    ch, payload = b._redis.published[0]
    data = json.loads(payload)
    assert data["user_id"] == 7 and data["frame"]["symbol"] == "MSFT"


@pytest.mark.asyncio
async def test_redis_publish_failure_falls_back_to_local():
    m = _Manager()
    b = WsBroadcaster(m, Settings(cache_backend="redis"), redis=_FakeRedis(fail=True))
    await b.publish_alert(3, {"type": "alert"})
    assert m.sent == [(3, {"type": "alert"})]  # defensive local fallback


@pytest.mark.asyncio
async def test_dispatch_delivers_to_local_sockets():
    m = _Manager()
    b = WsBroadcaster(m, Settings())
    await b._dispatch(json.dumps({"user_id": 5, "frame": {"type": "alert", "symbol": "NVDA"}}))
    assert m.sent == [(5, {"type": "alert", "symbol": "NVDA"})]


@pytest.mark.asyncio
async def test_dispatch_ignores_malformed_messages():
    m = _Manager()
    b = WsBroadcaster(m, Settings())
    await b._dispatch(b"not json")
    await b._dispatch(json.dumps({"missing": "keys"}))
    assert m.sent == []


@pytest.mark.asyncio
async def test_start_subscribes_and_delivers_incoming_message():
    m = _Manager()
    msg = {"type": "message", "data": json.dumps({"user_id": 8, "frame": {"type": "alert", "symbol": "TSLA"}})}
    fake = _FakeRedis(messages=[msg])
    b = WsBroadcaster(m, Settings(cache_backend="redis"), redis=fake)
    await b.start()
    await asyncio.sleep(0.02)  # let the subscriber loop process the queued message
    await b.stop()
    assert m.sent == [(8, {"type": "alert", "symbol": "TSLA"})]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_scheduler_push_alert_builds_and_publishes_frame():
    m = _Manager()
    b = WsBroadcaster(m, Settings(cache_backend="memory"))
    alert = Alert(id=1, user_id=9, symbol="AAPL", condition_type="PRICE_ABOVE",
                  threshold_value=150.0, is_active=True)
    await scheduler._push_alert(b, alert, datetime.now(timezone.utc))
    assert m.sent and m.sent[0][0] == 9
    frame = m.sent[0][1]
    assert frame["type"] == "alert" and frame["symbol"] == "AAPL" and frame["alert_id"] == 1

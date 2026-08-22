"""Multi-channel alert delivery — a critical alert must survive a closed browser.

Before this, a kill-switch trip reached only a live WebSocket. The contract now:
  * when the user has a live socket, deliver there and do NOT escalate,
  * when the user is offline (locally or per cross-worker presence), escalate to
    the configured out-of-band sinks,
  * a sink failure is logged and swallowed — the alert path never raises.
"""
from __future__ import annotations

import json
import smtplib

import pytest

from app.core.config import Settings
from app.core.notifications import NotificationContact, NotificationService
from app.core.ws_broadcaster import WsBroadcaster, presence_key


class _Manager:
    """Local socket registry stub: ``sent`` is the delivered-socket count."""

    def __init__(self, sent: int = 0):
        self.sent = sent
        self.frames: list[tuple[int, dict]] = []

    async def send_to_user(self, user_id, message):
        self.frames.append((user_id, message))
        return self.sent

    async def broadcast(self, message):
        self.frames.append((-1, message))
        return self.sent

    def user_count(self, user_id):
        return self.sent


class _Notifier:
    def __init__(self, result: bool | Exception = True):
        self.result = result
        self.calls: list[tuple] = []

    async def notify(self, contact, frame):
        self.calls.append((contact, frame))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Redis:
    def __init__(self, presence: dict | None = None):
        self.presence = presence or {}
        self.published: list[tuple] = []

    async def publish(self, channel, payload):
        self.published.append((channel, payload))

    async def get(self, key):
        return self.presence.get(key)


FRAME = {"type": "kill_switch", "symbol": "AAPL", "severity": "critical"}


# --- NotificationService: sink selection ---
def test_disabled_without_any_configured_sink():
    assert NotificationService(Settings()).enabled is False


def test_enabled_with_a_webhook_sink():
    assert NotificationService(Settings(notify_webhook_url="http://sink")).enabled is True


def test_enabled_with_smtp_configured():
    settings = Settings(notify_email_enabled=True, smtp_host="smtp.example.com")
    assert NotificationService(settings).enabled is True


def test_email_alone_is_not_enough_without_a_host():
    assert NotificationService(Settings(notify_email_enabled=True)).enabled is False


@pytest.mark.asyncio
async def test_notify_posts_to_the_webhook_sink(monkeypatch):
    service = NotificationService(Settings(notify_webhook_url="http://sink"))
    posted: list[tuple] = []

    async def _fake_post(url, frame):
        posted.append((url, frame))
        return True

    monkeypatch.setattr(service, "_post_webhook", _fake_post)

    assert await service.notify(NotificationContact(), FRAME) is True
    assert posted == [("http://sink", FRAME)]


@pytest.mark.asyncio
async def test_per_contact_webhook_overrides_the_global_sink(monkeypatch):
    service = NotificationService(Settings(notify_webhook_url="http://global"))
    urls: list[str] = []
    monkeypatch.setattr(
        service, "_post_webhook", lambda url, frame: _true(urls.append(url))
    )

    await service.notify(NotificationContact(webhook_url="http://personal"), FRAME)
    assert urls == ["http://personal"]


@pytest.mark.asyncio
async def test_notify_returns_false_when_nothing_is_configured():
    assert await NotificationService(Settings()).notify(NotificationContact(), FRAME) is False


@pytest.mark.asyncio
async def test_webhook_failure_is_swallowed_and_reported_as_not_delivered(monkeypatch):
    """A dead sink must not raise into the alert path."""
    import httpx

    service = NotificationService(Settings(notify_webhook_url="http://sink"))

    class _Boom:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def post(self, *a, **kw): raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)

    assert await service.notify(NotificationContact(), FRAME) is False


@pytest.mark.asyncio
async def test_email_sink_is_used_when_smtp_configured(monkeypatch):
    settings = Settings(
        notify_email_enabled=True, smtp_host="smtp.example.com", smtp_from="a@b.c"
    )
    service = NotificationService(settings)
    sent: list[tuple] = []
    monkeypatch.setattr(service, "_send_email_sync", lambda to, frame: sent.append((to, frame)))

    assert await service.notify(NotificationContact(email="u@example.com"), FRAME) is True
    assert sent[0][0] == "u@example.com"


@pytest.mark.asyncio
async def test_email_is_skipped_without_a_contact_address(monkeypatch):
    settings = Settings(notify_email_enabled=True, smtp_host="smtp.example.com")
    service = NotificationService(settings)
    monkeypatch.setattr(
        service, "_send_email_sync", lambda *a: pytest.fail("must not send")
    )

    assert await service.notify(NotificationContact(email=""), FRAME) is False


@pytest.mark.asyncio
async def test_smtp_failure_is_swallowed(monkeypatch):
    settings = Settings(notify_email_enabled=True, smtp_host="smtp.example.com")
    service = NotificationService(settings)

    def _boom(*a):
        raise OSError("smtp unreachable")

    monkeypatch.setattr(service, "_send_email_sync", _boom)

    assert await service.notify(NotificationContact(email="u@example.com"), FRAME) is False


# --- Real sink transports (no monkeypatched internals) ---
@pytest.mark.asyncio
async def test_webhook_sink_posts_the_frame_as_json():
    import httpx

    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    service = NotificationService(
        Settings(notify_webhook_url="http://sink/hook"),
        transport=httpx.MockTransport(_handler),
    )

    assert await service.notify(NotificationContact(), FRAME) is True
    assert str(seen[0].url) == "http://sink/hook"
    assert json.loads(seen[0].content) == FRAME


@pytest.mark.asyncio
async def test_webhook_sink_treats_5xx_as_undelivered():
    import httpx

    service = NotificationService(
        Settings(notify_webhook_url="http://sink"),
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )

    assert await service.notify(NotificationContact(), FRAME) is False


def test_email_message_is_well_formed_and_uses_tls_login(monkeypatch):
    """Covers the real SMTP send: TLS negotiation, auth, and message shape."""
    settings = Settings(
        notify_email_enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="pass",
        smtp_from="alerts@ai-finance.local",
        smtp_use_tls=True,
    )
    calls: dict = {"starttls": 0, "login": None, "message": None}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls["endpoint"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls["starttls"] += 1

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, msg):
            calls["message"] = msg

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    NotificationService(settings)._send_email_sync("u@example.com", FRAME)

    assert calls["endpoint"] == ("smtp.example.com", 587)
    assert calls["starttls"] == 1
    assert calls["login"] == ("user", "pass")
    msg = calls["message"]
    assert msg["To"] == "u@example.com"
    assert msg["From"] == "alerts@ai-finance.local"
    assert "kill_switch" in msg["Subject"] and "AAPL" in msg["Subject"]
    assert json.loads(msg.get_content()) == FRAME


def test_email_skips_tls_and_login_when_not_configured(monkeypatch):
    settings = Settings(
        notify_email_enabled=True, smtp_host="smtp.example.com", smtp_use_tls=False
    )
    calls = {"starttls": 0, "login": 0}

    class _FakeSMTP:
        def __init__(self, *a, **kw): ...
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def starttls(self): calls["starttls"] += 1
        def login(self, *a): calls["login"] += 1
        def send_message(self, msg): ...

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    NotificationService(settings)._send_email_sync("u@example.com", FRAME)

    assert calls == {"starttls": 0, "login": 0}  # no anonymous-relay surprises


# --- Broadcaster escalation policy ---
@pytest.mark.asyncio
async def test_online_user_gets_the_socket_and_no_fallback():
    manager, notifier = _Manager(sent=1), _Notifier()
    bc = WsBroadcaster(manager, Settings(), notifier=notifier)

    escalated = await bc.deliver_alert(1, FRAME, NotificationContact(email="u@x.com"))

    assert escalated is False
    assert notifier.calls == []
    assert manager.frames == [(1, FRAME)]


@pytest.mark.asyncio
async def test_offline_user_escalates_to_the_fallback_sink():
    manager, notifier = _Manager(sent=0), _Notifier(result=True)
    bc = WsBroadcaster(manager, Settings(), notifier=notifier)

    escalated = await bc.deliver_alert(1, FRAME, NotificationContact(email="u@x.com"))

    assert escalated is True
    assert notifier.calls[0][1] == FRAME


@pytest.mark.asyncio
async def test_no_contact_means_no_escalation():
    notifier = _Notifier()
    bc = WsBroadcaster(_Manager(sent=0), Settings(), notifier=notifier)

    assert await bc.deliver_alert(1, FRAME, None) is False
    assert notifier.calls == []


@pytest.mark.asyncio
async def test_fallback_exception_never_escapes_the_alert_path():
    notifier = _Notifier(result=RuntimeError("sink exploded"))
    bc = WsBroadcaster(_Manager(sent=0), Settings(), notifier=notifier)

    assert await bc.deliver_alert(1, FRAME, NotificationContact(email="u@x.com")) is False


@pytest.mark.asyncio
async def test_cross_worker_presence_suppresses_escalation():
    """The user is connected to ANOTHER worker — do not double-notify."""
    redis = _Redis(presence={presence_key(7): b"2"})
    notifier = _Notifier()
    bc = WsBroadcaster(
        _Manager(sent=0), Settings(cache_backend="redis"), redis=redis, notifier=notifier
    )

    assert await bc.deliver_alert(7, FRAME, NotificationContact(email="u@x.com")) is False
    assert notifier.calls == []
    assert redis.published, "frame must still be published for the other worker"


@pytest.mark.asyncio
async def test_zero_presence_refcount_escalates():
    redis = _Redis(presence={presence_key(7): b"0"})
    notifier = _Notifier(result=True)
    bc = WsBroadcaster(
        _Manager(sent=0), Settings(cache_backend="redis"), redis=redis, notifier=notifier
    )

    assert await bc.deliver_alert(7, FRAME, NotificationContact(email="u@x.com")) is True


@pytest.mark.asyncio
async def test_unknown_presence_fails_open_and_escalates():
    """Fail-open: if presence can't be read, escalate rather than lose the alert."""
    redis = _Redis(presence={})
    notifier = _Notifier(result=True)
    bc = WsBroadcaster(
        _Manager(sent=0), Settings(cache_backend="redis"), redis=redis, notifier=notifier
    )

    assert await bc.deliver_alert(7, FRAME, NotificationContact(email="u@x.com")) is True


@pytest.mark.asyncio
async def test_escalation_updates_the_operator_metric():
    from prometheus_client import REGISTRY

    def value(result: str) -> float:
        return REGISTRY.get_sample_value(
            "alert_fallback_deliveries_total", {"result": result}
        ) or 0.0

    ok_before, fail_before = value("delivered"), value("failed")

    await WsBroadcaster(_Manager(0), Settings(), notifier=_Notifier(True)).deliver_alert(
        1, FRAME, NotificationContact(email="u@x.com")
    )
    await WsBroadcaster(_Manager(0), Settings(), notifier=_Notifier(False)).deliver_alert(
        1, FRAME, NotificationContact(email="u@x.com")
    )

    assert value("delivered") == ok_before + 1
    assert value("failed") == fail_before + 1


async def _true(_):
    return True

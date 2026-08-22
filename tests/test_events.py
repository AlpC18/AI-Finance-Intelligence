"""Event intelligence: classification, impact scoring, blocking, WS fan-out."""
import json

import pytest

from app.core.config import Settings
from app.core.ws_broadcaster import WsBroadcaster
from app.services.ai_service import AIService
from app.services.event_intelligence_service import (
    EventIntelligenceService,
    classify_text,
    process_batch,
    score_event,
)


# --- Classification ---
def test_classify_each_category():
    assert classify_text("SEC files lawsuit against crypto exchange") == "LEGAL_RISK"
    assert classify_text("FED signals rate hike as CPI inflation climbs") == "MACRO_POLICY"
    assert classify_text("Nvidia unveils new AI chip in product launch") == "TECH_CATALYST"
    assert classify_text("Elon Musk tweeted a statement on the CEO transition") == "EXECUTIVE_SENTIMENT"


def test_classify_discards_noise_and_empty():
    assert classify_text("You won't believe these top 10 shocking stocks") is None
    assert classify_text("   ") is None
    assert classify_text("a quiet uneventful afternoon") is None


# --- Impact scoring ---
def test_legal_hearing_today_is_high_severity_and_blocking():
    text = "SEC lawsuit hearing scheduled today within hours; ruling expected"
    cat = classify_text(text)
    assert cat == "LEGAL_RISK"
    severity, orientation = score_event(text, cat)
    assert severity >= 8
    assert orientation == "HIGH_VOLATILITY"


def test_macro_rate_decision_high_volatility():
    text = "FOMC rate decision announcement today"
    sev, orient = score_event(text, "MACRO_POLICY")
    assert sev >= 8 and orient == "HIGH_VOLATILITY"


def test_tech_launch_is_bullish_moderate():
    text = "Company unveils product launch and record partnership"
    sev, orient = score_event(text, "TECH_CATALYST")
    assert 1 <= sev <= 10 and orient == "BULLISH"


def test_process_batch_sorts_by_severity_and_sets_blocking():
    raw = [
        {"title": "Nvidia unveils new chip launch", "source": "x"},
        {"title": "SEC lawsuit hearing today within hours, ruling imminent", "source": "sec"},
        {"title": "You won't believe this shocking clickbait", "source": "spam"},
    ]
    events = process_batch(raw)
    assert len(events) == 2  # clickbait discarded
    assert events[0].severity >= events[1].severity
    assert events[0].category == "LEGAL_RISK" and events[0].blocking is True


# --- Service (async) ---
class _Provider:
    def __init__(self, raw=None, fail=False):
        self._raw = raw or []
        self._fail = fail

    async def fetch_raw(self, limit=40):
        if self._fail:
            raise RuntimeError("feed down")
        return self._raw


@pytest.mark.asyncio
async def test_service_degrades_when_feeds_fail():
    svc = EventIntelligenceService(_Provider(fail=True))
    assert await svc.get_events() == []


@pytest.mark.asyncio
async def test_service_ai_context_emits_blocking_directive():
    raw = [{"title": "SEC lawsuit hearing today within hours; verdict imminent", "source": "sec"}]
    svc = EventIntelligenceService(_Provider(raw))
    payload, blocking = await svc.ai_context()
    assert payload and payload[0]["category"] == "LEGAL_RISK"
    assert blocking is not None and blocking.startswith("DO NOT TRADE")


@pytest.mark.asyncio
async def test_high_severity_filters_threshold():
    raw = [
        {"title": "FOMC rate decision today", "source": "fed"},
        {"title": "minor product upgrade", "source": "x"},
    ]
    svc = EventIntelligenceService(_Provider(raw))
    hi = await svc.high_severity(8)
    assert all(e.severity >= 8 for e in hi) and hi


# --- AI injection ---
@pytest.mark.asyncio
async def test_market_signal_injects_events_and_blocking():
    ai = AIService(Settings(anthropic_api_key=""))
    captured = {}

    async def _cap(system, user, schema):
        captured["system"] = system
        captured["user"] = user
        return None

    ai._complete = _cap  # type: ignore[assignment]
    await ai.market_signal(
        "AAPL", {"price": 1.0}, {"rsi_14": 50},
        events=[{"category": "MACRO_POLICY", "severity": 9, "orientation": "HIGH_VOLATILITY", "title": "FOMC today"}],
        blocking="DO NOT TRADE: MACRO_POLICY hike today",
    )
    assert "blocking_directive" in captured["user"] and "DO NOT TRADE" in captured["user"]
    assert "events" in captured["user"]
    assert "BLOCKING" in captured["system"]


# --- WS event fan-out ---
class _Manager:
    def __init__(self):
        self.broadcast_calls = []
        self.sent = []

    async def send_to_user(self, user_id, frame):
        self.sent.append((user_id, frame))
        return 1

    async def broadcast(self, frame):
        self.broadcast_calls.append(frame)


@pytest.mark.asyncio
async def test_publish_event_local_broadcasts():
    m = _Manager()
    b = WsBroadcaster(m, Settings(cache_backend="memory"))
    await b.publish_event({"type": "event", "severity": 9})
    assert m.broadcast_calls == [{"type": "event", "severity": 9}]


@pytest.mark.asyncio
async def test_dispatch_broadcast_frame_calls_manager_broadcast():
    m = _Manager()
    b = WsBroadcaster(m, Settings())
    await b._dispatch(json.dumps({"broadcast": True, "frame": {"type": "event", "title": "FOMC"}}))
    assert m.broadcast_calls == [{"type": "event", "title": "FOMC"}]
    assert m.sent == []


@pytest.mark.asyncio
async def test_run_event_scan_publishes_high_severity(monkeypatch):
    import app.core.deps as deps
    from app.core import scheduler as sch
    from app.models.event import MarketEvent

    pushed = []

    class _B:
        async def publish_event(self, frame):
            pushed.append(frame)

    class _S:
        async def high_severity(self, threshold=8):
            return [MarketEvent(title="FOMC rate decision today", category="MACRO_POLICY",
                                severity=9, orientation="HIGH_VOLATILITY", blocking=True)]

    monkeypatch.setattr(deps, "get_event_service", lambda: _S())
    monkeypatch.setattr(deps, "get_ws_broadcaster", lambda: _B())
    await sch.run_event_scan()
    assert pushed and pushed[0]["type"] == "event" and pushed[0]["severity"] == 9


@pytest.mark.asyncio
async def test_market_service_get_signal_uses_event_context():
    import numpy as np
    import pandas as pd
    from app.services.market_service import MarketService

    class _Mkt:
        async def get_history(self, s, period="6mo"):
            return pd.DataFrame({"Close": np.linspace(100, 110, 30)})

        async def get_price(self, s):
            return 110.0

    class _Cache:
        async def get_or_set(self, k, f, ttl=None):
            return await f()

    class _Events:
        async def ai_context(self):
            return ([{"category": "MACRO_POLICY", "severity": 9,
                      "orientation": "HIGH_VOLATILITY", "title": "FOMC"}],
                    "DO NOT TRADE: FOMC")

    svc = MarketService(_Mkt(), AIService(Settings(anthropic_api_key="")), _Cache(), events=_Events())
    sig = await svc.get_signal("AAPL")
    # Blocking event trips the circuit breaker: HOLD with the directive, no AI call.
    assert sig.action == "HOLD" and "DO NOT TRADE" in sig.rationale


@pytest.mark.asyncio
async def test_event_feed_provider_parses_rss_over_mock_transport():
    import httpx
    from app.providers.event_feed_provider import EventFeedProvider

    rss = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
           b'<item><title>SEC lawsuit hearing scheduled today</title>'
           b'<link>http://x/1</link><description>ruling imminent</description></item>'
           b'</channel></rss>')

    def _handler(request):
        return httpx.Response(200, content=rss)

    prov = EventFeedProvider(transport=httpx.MockTransport(_handler))
    items = await prov.fetch_raw(limit=1)
    assert items and "SEC lawsuit" in items[0]["title"] and items[0]["source"]


@pytest.mark.asyncio
async def test_blocking_event_circuit_breaks_before_ai():
    import numpy as np
    import pandas as pd
    from app.services.market_service import MarketService
    from app.models.market import SignalOut

    class _Mkt:
        async def get_history(self, s, period="6mo"):
            return pd.DataFrame({"Close": np.linspace(100, 110, 30)})

        async def get_price(self, s):
            return 110.0

    class _Cache:
        async def get_or_set(self, k, f, ttl=None):
            return await f()

    class _Events:
        async def ai_context(self):
            return ([{"category": "LEGAL_RISK", "severity": 9,
                      "orientation": "HIGH_VOLATILITY", "title": "SEC hearing"}],
                    "HALT: SEC Lawsuit decision pending within 1 hour")

    class _AI:
        def __init__(self):
            self.called = False

        async def market_signal(self, *a, **k):
            self.called = True
            return SignalOut(action="BUY", confidence=0.9, rationale="ignored")

    ai = _AI()
    svc = MarketService(_Mkt(), ai, _Cache(), events=_Events())
    sig = await svc.get_signal("AAPL")
    assert sig.action == "HOLD"
    assert "HALT" in sig.rationale
    assert sig.degraded is False
    assert ai.called is False  # circuit breaker fired before the LLM was consulted

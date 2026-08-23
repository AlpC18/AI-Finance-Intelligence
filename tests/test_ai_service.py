"""AI layer — circuit breaking, strict validation, grounding and degradation.

The AI service is the only component allowed to be *wrong*, so every path here
is about containment. Contract under test:

  * a model failure trips the breaker and returns ``None`` - callers fall back
    to pure quantitative data rather than surfacing an error,
  * output is STRICT-validated: extra keys, bad enums and out-of-range
    confidence are rejected, not coerced,
  * RAG retrieval is best-effort; a dead vector store degrades to no context,
  * streaming always yields *something* usable, never an empty socket.
"""
from __future__ import annotations

import json

import pytest
import structlog

from app.core.circuit_breaker import CircuitBreaker
from app.core.config import Settings
from app.core.vector_store import RetrievedChunk
from app.services.ai_service import INSUFFICIENT, AIService, _extract_json


# --------------------------- fake Anthropic surface -------------------------

class _Block:
    def __init__(self, text: str, type_: str = "text") -> None:
        self.text, self.type = text, type_


class _Msg:
    def __init__(self, blocks: list[_Block]) -> None:
        self.content = blocks


class _Stream:
    def __init__(self, chunks: list[str], fail_at: int | None = None) -> None:
        self._chunks, self._fail_at = chunks, fail_at

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def _gen():
            for i, c in enumerate(self._chunks):
                if self._fail_at is not None and i == self._fail_at:
                    raise RuntimeError("stream died mid-flight")
                yield c

        return _gen()


class _Messages:
    def __init__(self, reply="", raise_exc=None, chunks=None, fail_at=None) -> None:
        self._reply, self._raise = reply, raise_exc
        self._chunks, self._fail_at = chunks or [], fail_at
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append(kw)
        if self._raise:
            raise self._raise
        return _Msg([_Block(self._reply)])

    def stream(self, **kw):
        self.calls.append(kw)
        if self._raise:
            raise self._raise
        return _Stream(self._chunks, self._fail_at)


class _FakeClient:
    def __init__(self, **kw) -> None:
        self.messages = _Messages(**kw)


def _svc(**client_kw) -> AIService:
    """An AIService wired to a fake client, bypassing the real SDK import."""
    svc = AIService(Settings(anthropic_api_key=""))
    svc._client = _FakeClient(**client_kw)
    return svc


_GOOD_SIGNAL = json.dumps(
    {"action": "BUY", "confidence": 0.7, "rationale": "Guclu momentum", "citations": ["R"]}
)


# --------------------------- breaker + helpers ------------------------------

def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=999)
    assert cb.allow()
    cb.record_failure()
    assert cb.allow()  # 1 failure, still closed
    cb.record_failure()
    assert not cb.allow()  # 2 failures -> open
    cb.record_success()
    assert cb.allow()  # reset


def test_extract_json_tolerates_prose_wrapping():
    assert _extract_json('noise {"a": 1} tail') == {"a": 1}
    assert _extract_json("not json") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 2}\n```', {"a": 2}),
        ('prose {"a": 3} more', {"a": 3}),
        ("{ broken json {", None),   # braces present, still unparseable
        ("}{", None),                # end before start
        ("", None),
    ],
    ids=["bare", "fenced", "wrapped", "broken", "reversed", "empty"],
)
def test_extract_json_boundaries(text, expected):
    assert _extract_json(text) == expected


# --------------------------- degraded mode ----------------------------------

async def test_disabled_ai_returns_none_not_crash():
    svc = AIService(Settings(anthropic_api_key=""))
    assert svc.enabled is False
    assert await svc.market_signal("AAPL", {}, {}) is None


async def test_missing_sdk_degrades_instead_of_raising(monkeypatch):
    """A configured key with no `anthropic` package installed must not boot-fail."""
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *a, **kw):
        if name == "anthropic":
            raise ImportError("no anthropic here")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    svc = AIService(Settings(anthropic_api_key="sk-test-key"))
    assert svc.enabled is False and svc.available is False


async def test_available_is_false_while_breaker_is_open():
    svc = _svc(reply=_GOOD_SIGNAL)
    assert svc.available is True
    for _ in range(3):
        svc._breaker.record_failure()
    assert svc.enabled is True and svc.available is False


async def test_open_breaker_short_circuits_without_calling_the_model():
    svc = _svc(reply=_GOOD_SIGNAL)
    for _ in range(3):
        svc._breaker.record_failure()
    assert await svc.market_signal("AAPL", {}, {}) is None
    assert svc._client.messages.calls == []  # never reached the network


# --------------------------- strict validation ------------------------------

async def test_market_signal_parses_valid_output():
    svc = _svc(reply=_GOOD_SIGNAL)
    out = await svc.market_signal("AAPL", {"price": 1}, {"rsi": 55})
    assert out is not None and out.action == "BUY" and out.confidence == 0.7


@pytest.mark.parametrize(
    "reply",
    [
        json.dumps({"action": "BUY", "confidence": 0.5, "rationale": "x", "extra": 1}),
        json.dumps({"action": "MAYBE", "confidence": 0.5, "rationale": "x"}),
        json.dumps({"action": "BUY", "confidence": 9.9, "rationale": "x"}),
        json.dumps({"action": "BUY", "confidence": 0.5, "rationale": ""}),
        json.dumps({"confidence": 0.5, "rationale": "x"}),
        "the model just wrote prose today",
    ],
    ids=["extra-key", "bad-enum", "confidence-range", "empty-rationale", "missing-action", "non-json"],
)
async def test_malformed_model_output_is_rejected(reply):
    """Never coerce: a schema miss returns None so the caller degrades cleanly."""
    assert await _svc(reply=reply).market_signal("AAPL", {}, {}) is None


async def test_api_failure_trips_breaker_and_returns_none():
    svc = _svc(raise_exc=RuntimeError("429 overloaded"))
    for _ in range(3):
        assert await svc.market_signal("AAPL", {}, {}) is None
    assert svc._breaker.state == "open"


async def test_success_resets_a_partial_failure_streak():
    svc = _svc(reply=_GOOD_SIGNAL)
    svc._breaker.record_failure()
    assert await svc.market_signal("AAPL", {}, {}) is not None
    assert svc._breaker.state == "closed"


# --------------------------- prompt construction ----------------------------

async def test_blocking_directive_is_passed_to_the_model():
    """The breaker already forces HOLD; the model must still be told why."""
    svc = _svc(reply=_GOOD_SIGNAL)
    await svc.market_signal("AAPL", {}, {}, blocking="FOMC in 10 minutes")
    payload = json.loads(svc._client.messages.calls[0]["messages"][0]["content"])
    assert payload["blocking_directive"] == "FOMC in 10 minutes"


async def test_fundamentals_and_events_default_to_empty_containers():
    svc = _svc(reply=_GOOD_SIGNAL)
    await svc.market_signal("AAPL", {}, {})
    payload = json.loads(svc._client.messages.calls[0]["messages"][0]["content"])
    assert payload["fundamentals"] == {} and payload["events"] == []


async def test_request_id_is_forwarded_as_a_header():
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-123")
    try:
        svc = _svc(reply=_GOOD_SIGNAL)
        await svc.market_signal("AAPL", {}, {})
        assert svc._client.messages.calls[0]["extra_headers"] == {"X-Request-Id": "req-123"}
    finally:
        structlog.contextvars.clear_contextvars()


async def test_no_request_id_sends_no_header():
    structlog.contextvars.clear_contextvars()
    svc = _svc(reply=_GOOD_SIGNAL)
    await svc.market_signal("AAPL", {}, {})
    assert svc._client.messages.calls[0]["extra_headers"] is None


# --------------------------- RAG grounding ----------------------------------

class _Retriever:
    def __init__(self, chunks=None, exc=None) -> None:
        self._chunks, self._exc = chunks or [], exc
        self.queries: list[str] = []

    async def search(self, query, k):
        self.queries.append(query)
        if self._exc:
            raise self._exc
        return self._chunks


async def test_retrieved_context_reaches_the_prompt():
    chunks = [RetrievedChunk(text="t", headline="Apple beats", source="Reuters")]
    svc = _svc(reply=_GOOD_SIGNAL)
    svc._retriever = _Retriever(chunks)
    await svc.market_signal("AAPL", {}, {})
    payload = json.loads(svc._client.messages.calls[0]["messages"][0]["content"])
    assert payload["context"] == [{"headline": "Apple beats", "source": "Reuters"}]


async def test_retrieval_failure_degrades_to_empty_context():
    """A dead vector store must not break signal generation."""
    svc = _svc(reply=_GOOD_SIGNAL)
    svc._retriever = _Retriever(exc=RuntimeError("pgvector unreachable"))
    out = await svc.market_signal("AAPL", {}, {})
    payload = json.loads(svc._client.messages.calls[0]["messages"][0]["content"])
    assert out is not None and payload["context"] == []


async def test_blank_query_skips_retrieval_entirely():
    svc = _svc(reply=_GOOD_SIGNAL)
    retriever = _Retriever([RetrievedChunk(text="t")])
    svc._retriever = retriever
    await svc.portfolio_advice({"symbols": []})
    assert retriever.queries == []


# --------------------------- news + advice ----------------------------------

async def test_news_insight_validates_sentiment_enum():
    good = json.dumps({"summary": "Ozet", "sentiment": "positive", "key_points": ["a"]})
    out = await _svc(reply=good).news_insight("AAPL", ["h1"])
    assert out is not None and out.sentiment == "positive"

    bad = json.dumps({"summary": "Ozet", "sentiment": "euphoric"})
    assert await _svc(reply=bad).news_insight("AAPL", ["h1"]) is None


async def test_portfolio_advice_builds_query_from_symbols():
    svc = _svc(reply=json.dumps({"narrative": "Dengeli", "suggestions": [], "citations": []}))
    retriever = _Retriever([])
    svc._retriever = retriever
    out = await svc.portfolio_advice({"value": 100}, symbols=["AAPL", "MSFT"])
    assert out is not None and retriever.queries == ["AAPL MSFT"]


async def test_portfolio_advice_falls_back_to_payload_symbols():
    svc = _svc(reply=json.dumps({"narrative": "Dengeli"}))
    retriever = _Retriever([])
    svc._retriever = retriever
    await svc.portfolio_advice({"symbols": ["TSLA"]})
    assert retriever.queries == ["TSLA"]


# --------------------------- streaming --------------------------------------

async def _drain(agen) -> str:
    return "".join([c async for c in agen])


async def test_stream_yields_tokens_and_records_success():
    svc = _svc(chunks=["Apple ", "yukselis ", "egiliminde."])
    chunk = [RetrievedChunk(text="t", headline="h", source="s")]
    assert await _drain(svc.stream_insight("AAPL", {}, {}, chunk)) == "Apple yukselis egiliminde."
    assert svc._breaker.state == "closed"


async def test_stream_without_context_returns_the_insufficient_phrase():
    """No grounding must produce the refusal phrase, never an invented narrative."""
    svc = _svc(chunks=["should not be reached"])
    assert await _drain(svc.stream_insight("AAPL", {}, {}, [])) == INSUFFICIENT
    assert svc._client.messages.calls == []


async def test_stream_in_degraded_mode_yields_a_usable_line():
    svc = AIService(Settings(anthropic_api_key=""))
    out = await _drain(svc.stream_insight("AAPL", {}, {}, []))
    assert "AI devre disi" in out


async def test_stream_with_open_breaker_yields_the_degraded_line():
    svc = _svc(chunks=["x"])
    for _ in range(3):
        svc._breaker.record_failure()
    out = await _drain(svc.stream_insight("AAPL", {}, {}, [RetrievedChunk(text="t")]))
    assert "AI devre disi" in out


async def test_stream_failure_midway_appends_an_error_marker_and_trips_breaker():
    svc = _svc(chunks=["ilk ", "ikinci ", "ucuncu"], fail_at=2)
    out = await _drain(svc.stream_insight("AAPL", {}, {}, [RetrievedChunk(text="t")]))
    assert out.startswith("ilk ikinci ") and "AI akisi kesildi" in out
    assert svc._breaker._failures == 1

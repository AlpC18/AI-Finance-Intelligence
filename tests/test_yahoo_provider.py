"""Yahoo market-data provider — parsing, degradation and failure boundaries.

This provider sits on the network edge, so the contract under test is mostly
about *malformed reality*: Yahoo returns 200 with holes in it far more often
than it returns a clean error. Rules being pinned here:

  * price history raises ``ProviderError`` (never returns junk) when the payload
    cannot yield at least one usable close,
  * ``None`` closes (market holidays / halted sessions) are dropped, not
    forwarded as NaN into the indicator maths,
  * fundamentals are *best effort*: any failure degrades to an empty
    ``Fundamentals`` so a summary outage can never block the signal path.
"""
from __future__ import annotations

import httpx
import pytest
from tenacity import wait_none

from app.core.errors import ProviderError
from app.models.market import Fundamentals
from app.providers.market_provider import (
    YahooMarketProvider,
    _num,
    _parse_fundamentals,
    _raw,
    _walk,
)


def _chart(closes: list | None) -> dict:
    return {"chart": {"result": [{"indicators": {"quote": [{"close": closes}]}}]}}


def _provider(handler, **kw) -> YahooMarketProvider:
    return YahooMarketProvider(transport=httpx.MockTransport(handler), **kw)


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Keep tenacity's backoff out of the test clock without disabling retries."""
    monkeypatch.setattr(YahooMarketProvider._fetch_chart.retry, "wait", wait_none())


# --------------------------- history ---------------------------------------

async def test_get_history_returns_close_frame():
    provider = _provider(lambda r: httpx.Response(200, json=_chart([10.0, 11.0, 12.5])))
    frame = await provider.get_history("AAPL")
    assert list(frame["Close"]) == [10.0, 11.0, 12.5]


async def test_get_history_drops_null_closes():
    """A halted/holiday session arrives as null and must not become NaN."""
    provider = _provider(lambda r: httpx.Response(200, json=_chart([10.0, None, 12.0])))
    frame = await provider.get_history("AAPL")
    assert list(frame["Close"]) == [10.0, 12.0]


async def test_get_history_raises_when_every_close_is_null():
    provider = _provider(lambda r: httpx.Response(200, json=_chart([None, None])))
    with pytest.raises(ProviderError):
        await provider.get_history("AAPL")


@pytest.mark.parametrize(
    "payload",
    [
        {},                                    # nothing at all
        {"chart": {"result": []}},             # known-symbol shape, empty result
        {"chart": {"result": [{}]}},           # result without indicators
        {"chart": None},                       # null branch -> TypeError
    ],
    ids=["empty", "no-result", "no-indicators", "null-chart"],
)
async def test_get_history_rejects_malformed_payloads(payload):
    provider = _provider(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError):
        await provider.get_history("AAPL")


async def test_get_history_wraps_transport_failure_as_provider_error():
    """A 500 is retried, then surfaces as ProviderError - not a raw httpx error."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, text="upstream boom")

    with pytest.raises(ProviderError):
        await _provider(handler).get_history("AAPL")
    assert calls["n"] == 4  # stop_after_attempt(4)


async def test_get_history_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("flaky link")
        return httpx.Response(200, json=_chart([42.0]))

    frame = await _provider(handler).get_history("AAPL")
    assert list(frame["Close"]) == [42.0] and calls["n"] == 3


@pytest.mark.parametrize(
    "period,expected", [("1mo", "1mo"), ("5d", "5d"), ("1y", "1y"), ("bogus", "6mo")]
)
async def test_period_maps_to_yahoo_range(period, expected):
    seen: dict = {}

    def handler(request):
        seen["range"] = request.url.params.get("range")
        return httpx.Response(200, json=_chart([1.0]))

    await _provider(handler).get_history("AAPL", period=period)
    assert seen["range"] == expected


async def test_get_price_takes_the_latest_close():
    provider = _provider(lambda r: httpx.Response(200, json=_chart([10.0, 20.0, 30.0])))
    assert await provider.get_price("AAPL") == 30.0


# --------------------------- fundamentals ----------------------------------

_SUMMARY_OK = {
    "quoteSummary": {
        "result": [
            {
                "summaryDetail": {"trailingPE": {"raw": 18.5}, "shortRatio": {"raw": 1.2}},
                "defaultKeyStatistics": {"enterpriseToEbitda": {"raw": 12.25}},
                "financialData": {"debtToEquity": {"raw": 55.5}},
                "calendarEvents": {"earnings": {"earningsDate": [{"fmt": "2026-01-30"}]}},
            }
        ]
    }
}


async def test_get_fundamentals_parses_nested_raw_values():
    provider = _provider(lambda r: httpx.Response(200, json=_SUMMARY_OK))
    f = await provider.get_fundamentals("AAPL")
    assert (f.pe_ratio, f.ev_ebitda, f.debt_to_equity) == (18.5, 12.25, 55.5)
    assert f.short_ratio == 1.2 and f.earnings_date == "2026-01-30"


async def test_get_fundamentals_degrades_quietly_on_http_error():
    """Fundamentals must never block a signal: an outage yields empty, not raise."""
    provider = _provider(lambda r: httpx.Response(503, text="down"))
    assert await provider.get_fundamentals("AAPL") == Fundamentals()


async def test_get_fundamentals_degrades_on_transport_error():
    def handler(request):
        raise httpx.ConnectError("dns dead")

    assert await _provider(handler).get_fundamentals("AAPL") == Fundamentals()


@pytest.mark.parametrize(
    "payload", [{}, {"quoteSummary": {"result": []}}, {"quoteSummary": None}]
)
def test_parse_fundamentals_tolerates_malformed_envelope(payload):
    assert _parse_fundamentals(payload) == Fundamentals()


def test_parse_fundamentals_handles_null_sections():
    """Yahoo sends `"financialData": null` for thin tickers - the `or {}` path."""
    out = _parse_fundamentals(
        {"quoteSummary": {"result": [{"summaryDetail": None, "financialData": None}]}}
    )
    assert out == Fundamentals()


@pytest.mark.parametrize(
    "earnings,expected",
    [
        ([{"fmt": "2026-02-01"}], "2026-02-01"),
        ([1738368000], None),   # epoch int instead of a dict
        ([], None),             # present but empty
        ("not-a-list", None),   # wrong type entirely
    ],
    ids=["dict", "epoch-int", "empty", "wrong-type"],
)
def test_earnings_date_extraction(earnings, expected):
    out = _parse_fundamentals(
        {"quoteSummary": {"result": [{"calendarEvents": {"earnings": {"earningsDate": earnings}}}]}}
    )
    assert out.earnings_date == expected


# --------------------------- pure helpers -----------------------------------

def test_raw_unwraps_leaf_and_short_circuits_on_non_dict():
    assert _raw({"a": {"b": {"raw": 7}}}, "a", "b") == 7
    assert _raw({"a": 5}, "a", "b") is None       # mid-path value is not a dict
    assert _raw({"a": {"b": 3}}, "a", "b") == 3   # leaf already scalar
    assert _raw({}, "missing") is None


def test_walk_returns_none_when_path_breaks():
    assert _walk({"a": {"b": 1}}, "a", "b") == 1
    assert _walk({"a": "scalar"}, "a", "b") is None


@pytest.mark.parametrize(
    "value,expected",
    [(1.23456, 1.2346), ("7.5", 7.5), (None, None), ("abc", None), ({}, None)],
)
def test_num_rounds_or_degrades(value, expected):
    assert _num(value) == expected

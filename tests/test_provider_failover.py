"""Market-data provider failover — one upstream outage must not freeze signals.

Contract: try providers in order, first success wins, raise only when every source
fails. Fundamentals are best-effort and never raise. Health is reported without
touching the network so it is safe to expose on /health.
"""
from __future__ import annotations

import httpx
import pandas as pd
import pytest

from app.core.errors import ProviderError
from app.models.market import Fundamentals
from app.providers.failover_market_provider import FailoverMarketProvider
from app.providers.stooq_market_provider import StooqMarketProvider, _stooq_symbol


class _Ok:
    def __init__(self, price: float = 100.0):
        self.price = price
        self.calls = 0

    async def get_history(self, symbol, period="6mo"):
        self.calls += 1
        return pd.DataFrame({"Close": [self.price, self.price + 1]})

    async def get_price(self, symbol):
        self.calls += 1
        return self.price

    async def get_fundamentals(self, symbol):
        self.calls += 1
        return Fundamentals(pe_ratio=18.5)


class _Down:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc or ConnectionError("upstream down")
        self.calls = 0

    async def get_history(self, symbol, period="6mo"):
        self.calls += 1
        raise self.exc

    async def get_price(self, symbol):
        self.calls += 1
        raise self.exc

    async def get_fundamentals(self, symbol):
        self.calls += 1
        raise self.exc


# --- Construction ---
def test_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        FailoverMarketProvider([])


# --- Ordered failover ---
@pytest.mark.asyncio
async def test_primary_success_never_touches_the_fallback():
    primary, fallback = _Ok(), _Ok()
    fo = FailoverMarketProvider([("yahoo", primary), ("stooq", fallback)])

    assert await fo.get_price("AAPL") == 100.0
    assert (primary.calls, fallback.calls) == (1, 0)


@pytest.mark.asyncio
async def test_falls_over_to_the_secondary_on_primary_failure():
    primary, fallback = _Down(), _Ok(price=250.0)
    fo = FailoverMarketProvider([("yahoo", primary), ("stooq", fallback)])

    assert await fo.get_price("AAPL") == 250.0
    assert (primary.calls, fallback.calls) == (1, 1)


@pytest.mark.asyncio
async def test_history_fails_over_too():
    fo = FailoverMarketProvider([("yahoo", _Down()), ("stooq", _Ok(7.0))])
    hist = await fo.get_history("AAPL")
    assert list(hist["Close"]) == [7.0, 8.0]


@pytest.mark.asyncio
async def test_raises_only_when_every_provider_fails():
    fo = FailoverMarketProvider([("yahoo", _Down()), ("stooq", _Down())])

    with pytest.raises(ProviderError) as err:
        await fo.get_price("AAPL")

    assert err.value.status_code == 502
    assert isinstance(err.value.__cause__, ConnectionError)  # root cause preserved


# --- Fundamentals are best-effort ---
@pytest.mark.asyncio
async def test_fundamentals_never_raise_when_all_sources_fail():
    fo = FailoverMarketProvider([("yahoo", _Down()), ("stooq", _Down())])
    assert await fo.get_fundamentals("AAPL") == Fundamentals()


@pytest.mark.asyncio
async def test_fundamentals_fall_over_to_the_secondary():
    fo = FailoverMarketProvider([("yahoo", _Down()), ("stooq", _Ok())])
    assert (await fo.get_fundamentals("AAPL")).pe_ratio == 18.5


# --- Health reporting (no network) ---
@pytest.mark.asyncio
async def test_health_reports_per_provider_state_after_a_failover():
    fo = FailoverMarketProvider([("yahoo", _Down()), ("stooq", _Ok())])
    await fo.get_price("AAPL")

    health = {h.name: h for h in fo.health()}
    assert health["yahoo"].healthy is False
    assert health["yahoo"].failures == 1
    assert "upstream down" in health["yahoo"].last_error
    assert health["stooq"].healthy is True
    assert health["stooq"].last_error is None


@pytest.mark.asyncio
async def test_health_recovers_when_the_primary_comes_back():
    flaky = _Down()
    fo = FailoverMarketProvider([("yahoo", flaky), ("stooq", _Ok())])
    await fo.get_price("AAPL")
    assert fo.health()[0].healthy is False

    fo._providers[0] = ("yahoo", _Ok())  # upstream recovers
    await fo.get_price("AAPL")

    assert fo.health()[0].healthy is True
    assert fo.health()[0].failures == 1  # cumulative count is retained


def test_health_is_reported_before_any_call():
    fo = FailoverMarketProvider([("yahoo", _Ok())])
    assert [(h.name, h.healthy, h.failures) for h in fo.health()] == [("yahoo", True, 0)]


@pytest.mark.asyncio
async def test_failover_increments_the_operator_metric():
    from prometheus_client import REGISTRY

    def value() -> float:
        return REGISTRY.get_sample_value(
            "market_provider_failovers_total", {"provider": "yahoo"}
        ) or 0.0

    before = value()
    fo = FailoverMarketProvider([("yahoo", _Down()), ("stooq", _Ok())])
    await fo.get_price("AAPL")
    assert value() == before + 1


# --- The fallback source itself ---
def test_stooq_symbol_mapping():
    assert _stooq_symbol("AAPL") == "aapl.us"
    assert _stooq_symbol("BRK-B") == "brk-b"     # already qualified, left alone
    assert _stooq_symbol("^spx") == "^spx.us"


@pytest.mark.asyncio
async def test_stooq_parses_daily_csv():
    csv = "Date,Open,High,Low,Close,Volume\n2026-08-20,1,2,0,10.5,100\n2026-08-21,1,2,0,11.5,100\n"
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=csv))
    provider = StooqMarketProvider(transport=transport)

    assert list((await provider.get_history("AAPL"))["Close"]) == [10.5, 11.5]
    assert await provider.get_price("AAPL") == 11.5


@pytest.mark.asyncio
async def test_stooq_skips_unparsable_rows():
    csv = "Date,Close\n2026-08-20,N/D\n2026-08-21,12.0\n2026-08-22,\n"
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=csv))

    hist = await StooqMarketProvider(transport=transport).get_history("AAPL")
    assert list(hist["Close"]) == [12.0]


@pytest.mark.asyncio
async def test_stooq_raises_provider_error_on_empty_data():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="Date,Close\n"))

    with pytest.raises(ProviderError):
        await StooqMarketProvider(transport=transport).get_history("NOPE")


@pytest.mark.asyncio
async def test_stooq_raises_provider_error_on_http_failure():
    transport = httpx.MockTransport(lambda req: httpx.Response(503))

    with pytest.raises(ProviderError):
        await StooqMarketProvider(transport=transport).get_history("AAPL")


@pytest.mark.asyncio
async def test_stooq_exposes_no_fundamentals():
    assert await StooqMarketProvider().get_fundamentals("AAPL") == Fundamentals()


def test_deps_wires_yahoo_primary_with_stooq_fallback():
    from app.core.deps import get_failover_market_provider

    get_failover_market_provider.cache_clear()
    try:
        names = [name for name, _ in get_failover_market_provider()._providers]
        assert names == ["yahoo", "stooq"]
    finally:
        get_failover_market_provider.cache_clear()

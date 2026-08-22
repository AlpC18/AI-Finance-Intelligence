"""Fundamental metrics: quoteSummary parsing, provider degrade, AI context injection."""
import pytest

from app.core.config import Settings
from app.models.market import Fundamentals
from app.providers.market_provider import _parse_fundamentals
from app.services.ai_service import AIService
from app.services.market_service import MarketService

_SAMPLE = {
    "quoteSummary": {"result": [{
        "summaryDetail": {"trailingPE": {"raw": 25.31}, "shortRatio": {"raw": 1.2}},
        "defaultKeyStatistics": {"enterpriseToEbitda": {"raw": 18.4}},
        "financialData": {"debtToEquity": {"raw": 150.2}},
        "calendarEvents": {"earnings": {"earningsDate": [{"raw": 1730000000, "fmt": "2024-10-27"}]}},
    }]}
}


def test_parse_fundamentals_extracts_all_fields():
    f = _parse_fundamentals(_SAMPLE)
    assert f.pe_ratio == 25.31
    assert f.ev_ebitda == 18.4
    assert f.debt_to_equity == 150.2
    assert f.short_ratio == 1.2
    assert f.earnings_date == "2024-10-27"


def test_parse_fundamentals_missing_data_is_all_none():
    f = _parse_fundamentals({"quoteSummary": {"result": []}})
    assert f == Fundamentals()
    assert _parse_fundamentals({}).pe_ratio is None


class _NoopCache:
    async def get_or_set(self, key, factory, ttl=None):
        return await factory()


class _ProviderNoFundamentals:
    async def get_history(self, symbol, period="6mo"):
        import numpy as np, pandas as pd
        return pd.DataFrame({"Close": np.linspace(100, 110, 30)})

    async def get_price(self, symbol):
        return 110.0


class _ProviderWithFundamentals(_ProviderNoFundamentals):
    async def get_fundamentals(self, symbol):
        return Fundamentals(pe_ratio=25.0, ev_ebitda=12.0)


@pytest.mark.asyncio
async def test_market_service_degrades_without_fundamentals():
    svc = MarketService(_ProviderNoFundamentals(), AIService(Settings(anthropic_api_key="")), _NoopCache())
    data = await svc.get_market_data("AAPL")
    assert data.fundamentals is None  # provider lacks the method -> no crash


@pytest.mark.asyncio
async def test_market_service_includes_fundamentals():
    svc = MarketService(_ProviderWithFundamentals(), AIService(Settings(anthropic_api_key="")), _NoopCache())
    data = await svc.get_market_data("AAPL")
    assert data.fundamentals is not None and data.fundamentals.pe_ratio == 25.0


@pytest.mark.asyncio
async def test_ai_market_signal_injects_fundamentals_into_prompt():
    ai = AIService(Settings(anthropic_api_key=""))
    captured = {}

    async def _capture(system, user, schema):
        captured["system"] = system
        captured["user"] = user
        return None

    ai._complete = _capture  # type: ignore[assignment]
    await ai.market_signal("AAPL", {"price": 1.0}, {"rsi_14": 50}, fundamentals={"pe_ratio": 25.31})
    assert "fundamentals" in captured["user"] and "25.31" in captured["user"]
    assert "FUNDAMENTALS" in captured["system"]

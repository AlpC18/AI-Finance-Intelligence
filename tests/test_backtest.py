"""Backtesting engine: bar replay, metrics, equity curve, endpoint contract."""
import numpy as np
import pandas as pd
import pytest

from app.models.backtest import BacktestRequest
from app.services.backtest_service import BacktestService


class _FakeProvider:
    """Down-then-up price path so the RSI rules produce at least one round-trip."""

    async def get_history(self, symbol, period="6mo"):
        down = np.linspace(120, 80, 40)
        up = np.linspace(80.5, 125, 40)
        return pd.DataFrame({"Close": np.concatenate([down, up])})


@pytest.mark.asyncio
async def test_backtest_runs_and_produces_metrics_and_curve():
    report = await BacktestService(_FakeProvider()).run(
        BacktestRequest(symbol="aapl", period="1y", warmup=20)
    )
    assert report.symbol == "AAPL"
    assert report.bars == 80
    assert len(report.equity_curve) == 80
    assert report.metrics.trades >= 1
    assert report.metrics.final_equity > 0
    # every metric field is populated (structured payload for the frontend)
    m = report.metrics
    for field in ("hit_rate_pct", "expectancy", "max_drawdown_pct",
                  "sharpe_ratio", "total_return_pct"):
        assert isinstance(getattr(m, field), float)


@pytest.mark.asyncio
async def test_backtest_max_drawdown_is_non_positive():
    report = await BacktestService(_FakeProvider()).run(BacktestRequest(symbol="AAPL"))
    assert report.metrics.max_drawdown_pct <= 0.0


def test_backtest_endpoint_returns_report(client, auth_headers):
    from app.core.deps import get_backtest_service

    client.app.dependency_overrides[get_backtest_service] = lambda: BacktestService(_FakeProvider())
    r = client.post("/api/backtest/run", headers=auth_headers,
                    json={"symbol": "AAPL", "period": "1y"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert "metrics" in body and "equity_curve" in body and "trades" in body
    assert body["bars"] == len(body["equity_curve"])


def test_backtest_rejects_inverted_rsi_thresholds(client, auth_headers):
    r = client.post("/api/backtest/run", headers=auth_headers,
                    json={"symbol": "AAPL", "rsi_buy": 80, "rsi_sell": 70})
    assert r.status_code == 422


def test_backtest_requires_auth(client):
    assert client.post("/api/backtest/run", json={"symbol": "AAPL"}).status_code == 401

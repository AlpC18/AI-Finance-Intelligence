"""Quant engine: correlation matrix + Markowitz max-Sharpe optimization."""
import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.core.errors import AppError
from app.models.transaction import Transaction
from app.models.quant import MonteCarloRequest
from app.services.portfolio_service import PortfolioService
from app.services.quant_service import QuantService, _current_weights, _monte_carlo, _optimize


def _engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


class _Prov:
    """Distinct, weakly-correlated price paths per symbol (deterministic)."""

    async def get_history(self, symbol, period="1y"):
        n = 160
        base = np.linspace(100, 120, n)
        rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
        noise = np.cumsum(rng.normal(0, 0.6, n))
        return pd.DataFrame({"Close": base + noise})


def _seed(session, *symbols):
    for i, sym in enumerate(symbols, start=1):
        session.add(Transaction(user_id=1, symbol=sym, action="BUY", quantity=i, price=100.0))
    session.commit()


@pytest.mark.asyncio
async def test_correlation_matrix_shape_and_diagonal():
    engine = _engine()
    with Session(engine) as s:
        _seed(s, "AAPL", "MSFT", "NVDA")
        quant = QuantService(_Prov(), PortfolioService(None, None))
        corr = await quant.correlation(s, 1)
        assert corr.symbols == ["AAPL", "MSFT", "NVDA"]
        assert len(corr.matrix) == 3 and all(len(row) == 3 for row in corr.matrix)
        for i in range(3):
            assert abs(corr.matrix[i][i] - 1.0) < 1e-6  # self-correlation == 1


@pytest.mark.asyncio
async def test_optimize_weights_are_valid_simplex():
    engine = _engine()
    with Session(engine) as s:
        _seed(s, "AAPL", "MSFT", "NVDA")
        quant = QuantService(_Prov(), PortfolioService(None, None))
        report = await quant.optimize(s, 1, weight_cap=0.6, samples=3000)
        w = [x.target_weight for x in report.weights]
        assert abs(sum(w) - 1.0) < 1e-3            # weights sum to 1
        assert all(x >= 0 for x in w)              # long-only
        assert all(x <= 0.6 + 1e-6 for x in w)     # concentration cap respected
        assert 0 < report.concentration_hhi <= 1.0
        assert np.isfinite(report.sharpe_ratio)
        assert report.method == "monte_carlo_max_sharpe"


@pytest.mark.asyncio
async def test_optimize_single_holding_is_full_weight():
    engine = _engine()
    with Session(engine) as s:
        _seed(s, "AAPL")
        quant = QuantService(_Prov(), PortfolioService(None, None))
        report = await quant.optimize(s, 1)
        assert report.method == "single_asset"
        assert report.weights[0].target_weight == 1.0


@pytest.mark.asyncio
async def test_empty_portfolio_raises():
    engine = _engine()
    with Session(engine) as s:
        quant = QuantService(_Prov(), PortfolioService(None, None))
        with pytest.raises(AppError) as exc:
            await quant.correlation(s, 1)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_monte_carlo_reports_bounded_loss_distribution():
    engine = _engine()
    with Session(engine) as s:
        _seed(s, "AAPL", "MSFT", "NVDA")
        quant = QuantService(_Prov(), PortfolioService(None, None))
        report = await quant.monte_carlo(
            s,
            1,
            MonteCarloRequest(horizon_days=20, simulations=500, target_return_pct=2),
        )
        assert report.method == "historical_bootstrap"
        assert report.simulations == 500
        assert report.percentile_05_value <= report.median_terminal_value <= report.percentile_95_value
        assert 0 <= report.probability_of_loss_pct <= 100
        assert 0 <= report.probability_of_target_pct <= 100
        assert report.value_at_risk_95_pct >= 0


@pytest.mark.asyncio
async def test_quant_rejects_unavailable_and_short_price_history():
    class _Unavailable:
        async def get_history(self, symbol, period="1y"):
            raise RuntimeError("upstream unavailable")

    class _Short:
        async def get_history(self, symbol, period="1y"):
            return pd.DataFrame({"Close": [100, 101]})

    engine = _engine()
    with Session(engine) as s:
        _seed(s, "AAPL")
        with pytest.raises(AppError) as unavailable:
            await QuantService(_Unavailable(), PortfolioService(None, None)).correlation(s, 1)
        assert unavailable.value.status_code == 502
        with pytest.raises(AppError) as short:
            await QuantService(_Short(), PortfolioService(None, None)).correlation(s, 1)
        assert short.value.status_code == 422


def test_quant_fallback_and_invalid_simulation_inputs():
    assert np.allclose(_current_weights(np.array([0.0, 0.0]), np.array([1.0, 2.0])), [0.5, 0.5])
    report = _optimize(
        np.array([[0.01, 0.02], [0.02, 0.01], [0.01, 0.03]]),
        ["AAPL", "MSFT"],
        np.array([0.5, 0.5]),
        weight_cap=0.1,
        samples=5,
    )
    assert report.samples_evaluated == 7
    with pytest.raises(AppError) as exc:
        _monte_carlo(
            np.array([[np.nan]]), ["AAPL"], np.array([1.0]), 100.0,
            MonteCarloRequest(horizon_days=5, simulations=500),
        )
    assert exc.value.status_code == 422

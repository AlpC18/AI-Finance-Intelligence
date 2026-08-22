"""Quant engine: correlation matrix + Markowitz max-Sharpe optimization."""
import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.core.errors import AppError
from app.models.transaction import Transaction
from app.services.portfolio_service import PortfolioService
from app.services.quant_service import QuantService


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

"""Correlation matrix + Markowitz (max-Sharpe) optimization over active holdings.

Price fetches are concurrent (async); the NumPy math is offloaded to a thread via
`run_in_threadpool` so it never blocks Uvicorn's event loop.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.core.errors import AppError
from app.models.quant import CorrelationMatrix, OptimizationReport, OptimizedWeight
from app.providers.base import MarketDataProvider
from app.services.portfolio_service import PortfolioService

_TRADING_DAYS = 252
_MIN_ROWS = 5


class QuantService:
    def __init__(
        self, provider: MarketDataProvider, portfolio: PortfolioService
    ) -> None:
        self._provider = provider
        self._portfolio = portfolio

    async def _price_frame(
        self, session: Session, user_id: int
    ) -> tuple[pd.DataFrame, dict[str, float]]:
        """Aligned Close-price frame + qty-per-symbol for active holdings."""
        holdings = self._portfolio.active_holdings(session, user_id)
        if not holdings:
            raise AppError("Aktif pozisyon yok; analiz yapilamaz.", status_code=400)
        qty = {h.symbol.upper(): h.quantity for h in holdings}
        symbols = list(qty.keys())
        histories = await asyncio.gather(
            *[self._provider.get_history(s, period="1y") for s in symbols],
            return_exceptions=True,
        )
        closes: dict[str, pd.Series] = {}
        for sym, hist in zip(symbols, histories):
            if isinstance(hist, Exception):
                continue
            closes[sym] = hist["Close"].reset_index(drop=True)
        if not closes:
            raise AppError("Fiyat verisi alinamadi.", status_code=502)
        frame = pd.DataFrame(closes).dropna()
        if len(frame) < _MIN_ROWS:
            raise AppError("Yetersiz fiyat gecmisi.", status_code=422)
        return frame, {s: qty[s] for s in frame.columns}

    async def correlation(self, session: Session, user_id: int) -> CorrelationMatrix:
        frame, _ = await self._price_frame(session, user_id)
        symbols = list(frame.columns)
        matrix = await run_in_threadpool(_correlation, frame)
        return CorrelationMatrix(symbols=symbols, matrix=matrix)

    async def optimize(
        self,
        session: Session,
        user_id: int,
        weight_cap: float = 0.4,
        samples: int = 8000,
    ) -> OptimizationReport:
        frame, qty = await self._price_frame(session, user_id)
        symbols = list(frame.columns)
        last_prices = frame.iloc[-1].to_numpy(dtype=float)
        current = _current_weights(np.array([qty[s] for s in symbols]), last_prices)
        returns = frame.pct_change().dropna().to_numpy(dtype=float)
        return await run_in_threadpool(
            _optimize, returns, symbols, current, weight_cap, samples
        )


def _correlation(frame: pd.DataFrame) -> list[list[float]]:
    corr = frame.pct_change().dropna().corr().to_numpy()
    return np.nan_to_num(np.round(corr, 4)).tolist()


def _current_weights(qtys: np.ndarray, last_prices: np.ndarray) -> np.ndarray:
    values = qtys * last_prices
    total = values.sum()
    return values / total if total > 0 else np.full(len(qtys), 1.0 / len(qtys))


def _optimize(
    returns: np.ndarray,
    symbols: list[str],
    current: np.ndarray,
    weight_cap: float,
    samples: int,
) -> OptimizationReport:
    n = len(symbols)
    mu = returns.mean(axis=0) * _TRADING_DAYS
    cov = np.cov(returns, rowvar=False) * _TRADING_DAYS
    cov = np.atleast_2d(cov)

    if n == 1:
        target = np.array([1.0])
        ret, vol, sharpe = _portfolio_stats(target, mu, cov)
        return _report("single_asset", symbols, current, target, ret, vol, sharpe, 1)

    rng = np.random.default_rng(0)  # deterministic for reproducible tests
    weights = rng.dirichlet(np.ones(n), size=samples)
    # Seed the search with equal-weight and current allocations as candidates.
    weights = np.vstack([weights, np.full(n, 1.0 / n), current])
    capped = weights[weights.max(axis=1) <= weight_cap]
    if capped.shape[0] == 0:
        capped = weights  # cap too tight for this book; fall back to all samples

    port_ret = capped @ mu
    port_var = np.einsum("ij,jk,ik->i", capped, cov, capped)
    port_vol = np.sqrt(np.clip(port_var, 1e-12, None))
    sharpe = port_ret / port_vol
    best = int(np.argmax(sharpe))
    target = capped[best]
    return _report(
        "monte_carlo_max_sharpe", symbols, current, target,
        float(port_ret[best]), float(port_vol[best]), float(sharpe[best]),
        int(capped.shape[0]),
    )


def _portfolio_stats(w: np.ndarray, mu: np.ndarray, cov: np.ndarray) -> tuple[float, float, float]:
    ret = float(w @ mu)
    vol = float(np.sqrt(max(w @ cov @ w, 1e-12)))
    return ret, vol, (ret / vol if vol else 0.0)


def _report(
    method: str, symbols: list[str], current: np.ndarray, target: np.ndarray,
    ret: float, vol: float, sharpe: float, samples: int,
) -> OptimizationReport:
    weights = [
        OptimizedWeight(
            symbol=sym,
            current_weight=round(float(current[i]), 4),
            target_weight=round(float(target[i]), 4),
        )
        for i, sym in enumerate(symbols)
    ]
    return OptimizationReport(
        method=method,
        symbols=symbols,
        weights=weights,
        expected_annual_return=round(ret, 4),
        annual_volatility=round(vol, 4),
        sharpe_ratio=round(sharpe, 4),
        concentration_hhi=round(float(np.sum(target ** 2)), 4),
        samples_evaluated=samples,
    )

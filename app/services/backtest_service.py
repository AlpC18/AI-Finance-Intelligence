"""Historical simulation: replay OHLCV bars through the indicator/signal rules.

Deterministic (no LLM) RSI mean-reversion rules over an expanding Close window,
producing quant performance metrics + an equity curve for frontend plotting.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.models.backtest import (
    BacktestMetrics,
    BacktestReport,
    BacktestRequest,
    BacktestTrade,
    EquityPoint,
)
from app.providers.base import MarketDataProvider
from app.services.indicators import compute_indicators


class BacktestService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    async def run(self, req: BacktestRequest) -> BacktestReport:
        hist = await self._provider.get_history(
            req.symbol.upper().strip(), period=req.period
        )
        close = hist["Close"].reset_index(drop=True)
        return _simulate(req, close)


def _rule_signal(rsi: float | None, req: BacktestRequest) -> str:
    if rsi is None:
        return "HOLD"
    if rsi <= req.rsi_buy:
        return "BUY"
    if rsi >= req.rsi_sell:
        return "SELL"
    return "HOLD"


def _simulate(req: BacktestRequest, close: pd.Series) -> BacktestReport:
    n = len(close)
    warmup = max(req.warmup, 15)
    cash = req.initial_capital
    shares = 0.0
    entry_price = 0.0
    curve: list[EquityPoint] = []
    trades: list[BacktestTrade] = []

    for i in range(n):
        price = float(close.iloc[i])
        if i >= warmup and price > 0:
            rsi = compute_indicators(close.iloc[: i + 1]).rsi_14
            signal = _rule_signal(rsi, req)
            if signal == "BUY" and shares == 0.0:
                shares = cash / price
                entry_price = price
                cash = 0.0
            elif signal == "SELL" and shares > 0.0:
                trades.append(_close_trade(entry_price, price, shares, i))
                cash = shares * price
                shares = 0.0
                entry_price = 0.0
        curve.append(EquityPoint(index=i, equity=round(cash + shares * price, 2)))

    if shares > 0.0 and n:  # mark-to-market any position still open at the last bar
        last = float(close.iloc[-1])
        trades.append(_close_trade(entry_price, last, shares, n - 1))

    return BacktestReport(
        symbol=req.symbol.upper().strip(),
        bars=n,
        strategy="rsi",
        metrics=_metrics(req, curve, trades),
        equity_curve=curve,
        trades=trades,
    )


def _close_trade(entry: float, exit_price: float, qty: float, index: int) -> BacktestTrade:
    return BacktestTrade(
        entry_price=round(entry, 4),
        exit_price=round(exit_price, 4),
        quantity=round(qty, 6),
        pnl=round((exit_price - entry) * qty, 2),
        return_pct=round((exit_price - entry) / entry * 100, 2) if entry else 0.0,
        exit_index=index,
    )


def _metrics(
    req: BacktestRequest, curve: list[EquityPoint], trades: list[BacktestTrade]
) -> BacktestMetrics:
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = len(pnls)
    hit_rate = (len(wins) / total * 100) if total else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor: float | None = round(gross_win / gross_loss, 4)
    else:
        profit_factor = None  # undefined: no losing trades
    expectancy = (sum(pnls) / total) if total else 0.0

    equities = np.array([p.equity for p in curve], dtype=float)
    final_equity = float(equities[-1]) if equities.size else req.initial_capital
    total_return = (
        (final_equity - req.initial_capital) / req.initial_capital * 100
        if req.initial_capital
        else 0.0
    )
    return BacktestMetrics(
        trades=total,
        hit_rate_pct=round(hit_rate, 2),
        profit_factor=profit_factor,
        expectancy=round(expectancy, 2),
        max_drawdown_pct=round(_max_drawdown(equities), 2),
        sharpe_ratio=round(_sharpe(equities), 4),
        total_return_pct=round(total_return, 2),
        final_equity=round(final_equity, 2),
    )


def _max_drawdown(equities: np.ndarray) -> float:
    if equities.size < 2:
        return 0.0
    peak = np.maximum.accumulate(equities)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, (equities - peak) / peak, 0.0)
    return float(np.nanmin(dd) * 100)


def _sharpe(equities: np.ndarray) -> float:
    if equities.size < 3:
        return 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.diff(equities) / equities[:-1]
    rets = rets[np.isfinite(rets)]
    if rets.size < 2 or rets.std() == 0:
        return 0.0
    return float(rets.mean() / rets.std() * np.sqrt(252))

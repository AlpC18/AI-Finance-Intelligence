"""Historical simulation: replay OHLCV bars through the indicator/signal rules.

Deterministic (no LLM) RSI mean-reversion rules over an expanding Close window,
producing quant performance metrics + an equity curve for frontend plotting.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.errors import NotFoundError
from app.models.backtest import (
    BacktestComparison,
    BacktestMetrics,
    BacktestReport,
    BacktestRequest,
    BacktestRun,
    BacktestRunPage,
    BacktestRunSummary,
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


    # --- Persistence -------------------------------------------------------
    # Runs are kept per user under a hard cap. History is a convenience, not a
    # system of record, and an uncapped table grows without anyone deciding to
    # let it: the endpoint is authenticated and rate-limited, but a user who
    # keeps backtesting for a year should not silently accumulate a table.
    MAX_RUNS_PER_USER = 100

    async def run_and_save(
        self, session: Session, user_id: int, req: BacktestRequest
    ) -> BacktestReport:
        """Simulate, then persist the result and return it carrying its id."""
        report = await self.run(req)
        run = _to_row(user_id, req, report)
        session.add(run)
        session.commit()
        session.refresh(run)
        self._prune(session, user_id)
        return report.model_copy(
            update={"id": run.id, "created_at": run.created_at}
        )

    def list_runs(
        self,
        session: Session,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        symbol: Optional[str] = None,
    ) -> BacktestRunPage:
        """Saved runs, newest first. Scoped to ``user_id`` in the query itself."""
        conditions = [BacktestRun.user_id == user_id]
        if symbol:
            conditions.append(BacktestRun.symbol == symbol.upper().strip())

        total = session.exec(
            select(func.count()).select_from(BacktestRun).where(*conditions)
        ).one()
        rows = session.exec(
            select(BacktestRun)
            .where(*conditions)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return BacktestRunPage(
            total=total, limit=limit, offset=offset,
            items=[_summary(r) for r in rows],
        )

    def get_run(self, session: Session, user_id: int, run_id: int) -> BacktestReport:
        """The full stored report, curve and trades included."""
        row = self._find(session, user_id, run_id)
        if row is None:
            raise NotFoundError("Backtest kaydi bulunamadi.")
        return _to_report(row)

    def delete_run(self, session: Session, user_id: int, run_id: int) -> None:
        row = self._find(session, user_id, run_id)
        if row is None:
            raise NotFoundError("Backtest kaydi bulunamadi.")
        session.delete(row)
        session.commit()

    def compare(
        self, session: Session, user_id: int, run_ids: list[int]
    ) -> BacktestComparison:
        """Several runs side by side, in the order asked for.

        Every id must resolve for THIS user: silently dropping one would show a
        comparison that looks complete while missing the run the caller most
        likely cared about.
        """
        rows = {
            r.id: r
            for r in session.exec(
                select(BacktestRun).where(
                    BacktestRun.user_id == user_id, BacktestRun.id.in_(run_ids)
                )
            ).all()
        }
        missing = [i for i in run_ids if i not in rows]
        if missing:
            raise NotFoundError(
                f"Backtest kaydi bulunamadi: {', '.join(str(i) for i in missing)}."
            )
        ordered = [rows[i] for i in run_ids]
        return BacktestComparison(
            runs=[_summary(r) for r in ordered], best=_best_by_metric(ordered)
        )

    def _find(
        self, session: Session, user_id: int, run_id: int
    ) -> Optional[BacktestRun]:
        return session.exec(
            select(BacktestRun).where(
                BacktestRun.id == run_id, BacktestRun.user_id == user_id
            )
        ).first()

    def _prune(self, session: Session, user_id: int) -> None:
        """Drop the oldest runs beyond the per-user cap."""
        stale = session.exec(
            select(BacktestRun)
            .where(BacktestRun.user_id == user_id)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .offset(self.MAX_RUNS_PER_USER)
        ).all()
        if not stale:
            return
        for row in stale:
            session.delete(row)
        session.commit()


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


def _to_row(user_id: int, req: BacktestRequest, report: BacktestReport) -> BacktestRun:
    m = report.metrics
    return BacktestRun(
        user_id=user_id,
        symbol=report.symbol,
        strategy=report.strategy,
        period=req.period,
        initial_capital=req.initial_capital,
        rsi_buy=req.rsi_buy,
        rsi_sell=req.rsi_sell,
        warmup=req.warmup,
        bars=report.bars,
        trades=m.trades,
        hit_rate_pct=m.hit_rate_pct,
        profit_factor=m.profit_factor,
        expectancy=m.expectancy,
        max_drawdown_pct=m.max_drawdown_pct,
        sharpe_ratio=m.sharpe_ratio,
        total_return_pct=m.total_return_pct,
        final_equity=m.final_equity,
        report_json=report.model_dump_json(),
    )


def _metrics_from_row(row: BacktestRun) -> BacktestMetrics:
    return BacktestMetrics(
        trades=row.trades,
        hit_rate_pct=row.hit_rate_pct,
        profit_factor=row.profit_factor,
        expectancy=row.expectancy,
        max_drawdown_pct=row.max_drawdown_pct,
        sharpe_ratio=row.sharpe_ratio,
        total_return_pct=row.total_return_pct,
        final_equity=row.final_equity,
    )


def _summary(row: BacktestRun) -> BacktestRunSummary:
    """Built from the columns, never by parsing report_json - that is the whole
    reason the metrics are stored twice."""
    return BacktestRunSummary(
        id=row.id,
        symbol=row.symbol,
        strategy=row.strategy,
        period=row.period,
        initial_capital=row.initial_capital,
        rsi_buy=row.rsi_buy,
        rsi_sell=row.rsi_sell,
        bars=row.bars,
        metrics=_metrics_from_row(row),
        created_at=row.created_at,
    )


def _to_report(row: BacktestRun) -> BacktestReport:
    report = BacktestReport.model_validate_json(row.report_json)
    return report.model_copy(update={"id": row.id, "created_at": row.created_at})


# (metric, higher_is_better). Drawdown is stored as a negative percentage, so
# "best" is the value closest to zero - which max() already gives.
_RANKED = (
    ("total_return_pct", True),
    ("sharpe_ratio", True),
    ("expectancy", True),
    ("hit_rate_pct", True),
    ("profit_factor", True),
    ("max_drawdown_pct", True),
)


def _best_by_metric(rows: list[BacktestRun]) -> dict[str, int]:
    """Winning run id per metric. A metric no run can be ranked on is omitted
    rather than awarded to an arbitrary run - profit_factor is None whenever a
    run had no losing trade, and None is 'undefined', not 'worst'."""
    best: dict[str, int] = {}
    for name, higher_is_better in _RANKED:
        scored = [r for r in rows if getattr(r, name) is not None]
        if not scored:
            continue
        pick = max if higher_is_better else min
        best[name] = pick(scored, key=lambda r: getattr(r, name)).id
    return best

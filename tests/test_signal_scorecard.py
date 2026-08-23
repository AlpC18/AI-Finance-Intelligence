"""Scoring the AI's live signals — the claim the whole product rests on.

The scorecard's job is to be *disconfirmable*: it must be able to report that
the signals lost money, that confidence is anti-correlated with accuracy, or
that there is not enough evidence to say. These tests spend most of their
effort on exactly those cases, because a scorecard that can only report good
news is worse than none.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models.market import Indicators, MarketData, Quote
from app.models.order import TradeAuditLog, TradeOrder
from app.services.signal_performance_service import SignalPerformanceService


@contextmanager
def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


class _Quotes:
    """Quotes from a fixed table; a symbol mapped to None raises like a dead feed."""

    def __init__(self, **prices) -> None:
        self.prices = prices

    async def get_market_data(self, symbol: str) -> MarketData:
        price = self.prices.get(symbol)
        if price is None:
            raise RuntimeError(f"no quote for {symbol}")
        return MarketData(quote=Quote(symbol=symbol, price=price), indicators=Indicators())


def _signal(
    s: Session,
    *,
    user_id: int = 1,
    order_id: str = "o1",
    symbol: str = "AAPL",
    side: str = "buy",
    action: str = "BUY",
    confidence: float | None = 0.8,
    fill_price: float | None = 100.0,
    quantity: float = 10.0,
    status: str = "filled",
) -> None:
    """One executed signal: the audit row plus the order it produced."""
    s.add(TradeAuditLog(
        user_id=user_id, order_id=order_id, signal_type=action,
        confidence=confidence, execution_timestamp=datetime.now(timezone.utc),
    ))
    s.add(TradeOrder(
        user_id=user_id, broker_order_id=order_id, symbol=symbol, side=side,
        quantity=quantity, status=status,
        filled_quantity=quantity if fill_price else 0.0,
        filled_avg_price=fill_price,
    ))
    s.commit()


# ============================ DIRECTIONAL SCORING ===========================

async def test_a_buy_that_rose_is_scored_correct():
    with _db() as s:
        _signal(s, side="buy", action="BUY", fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        assert card.evaluated == 1
        assert card.hit_rate_pct == 100.0
        assert card.avg_return_pct == pytest.approx(10.0)
        assert card.total_pnl == pytest.approx(100.0), "10% of a 1000 notional"


async def test_a_buy_that_fell_is_scored_wrong():
    with _db() as s:
        _signal(s, side="buy", action="BUY", fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=90.0)).scorecard(s, 1)

        assert card.hit_rate_pct == 0.0
        assert card.avg_return_pct == pytest.approx(-10.0)
        assert card.total_pnl == pytest.approx(-100.0)


async def test_a_sell_that_fell_is_scored_correct():
    """The short side profits when price drops — the sign must be flipped for it."""
    with _db() as s:
        _signal(s, side="sell", action="SELL", fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=90.0)).scorecard(s, 1)

        assert card.hit_rate_pct == 100.0, "a correct short is not a loss"
        assert card.avg_return_pct == pytest.approx(10.0)


async def test_a_sell_that_rose_is_scored_wrong():
    with _db() as s:
        _signal(s, side="sell", action="SELL", fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        assert card.hit_rate_pct == 0.0
        assert card.avg_return_pct == pytest.approx(-10.0)


async def test_a_flat_market_counts_as_wrong_not_right():
    """No move is not a win; scoring it as one would inflate every hit rate."""
    with _db() as s:
        _signal(s, fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=100.0)).scorecard(s, 1)

        assert card.hit_rate_pct == 0.0


# ======================== WHAT IS *NOT* SCORED ==============================

async def test_an_unfilled_signal_is_set_aside_not_counted_as_a_loss():
    """It never took a position, so it is neither right nor wrong."""
    with _db() as s:
        _signal(s, order_id="filled", fill_price=100.0)
        _signal(s, order_id="pending", fill_price=None, status="accepted")
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        assert card.evaluated == 1
        assert card.skipped_unfilled == 1
        assert card.hit_rate_pct == 100.0, "the unfilled signal must not dilute this"


async def test_an_unquotable_symbol_degrades_the_report_instead_of_failing_it():
    with _db() as s:
        _signal(s, order_id="a", symbol="AAPL", fill_price=100.0)
        _signal(s, order_id="b", symbol="DEAD", fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        assert card.evaluated == 1
        assert card.skipped_unpriced == 1
        assert card.degraded is True


async def test_a_total_feed_outage_returns_an_empty_card_not_an_error():
    with _db() as s:
        _signal(s, fill_price=100.0)
        card = await SignalPerformanceService(_Quotes()).scorecard(s, 1)

        assert card.evaluated == 0 and card.degraded is True
        assert card.hit_rate_pct == 0.0


async def test_a_user_with_no_signals_gets_an_explicit_empty_card():
    with _db() as s:
        card = await SignalPerformanceService(_Quotes()).scorecard(s, 1)

        assert card.evaluated == 0
        assert card.buckets == [] and card.best is None
        assert "sinyal yok" in card.calibration_note


async def test_one_users_signals_never_score_into_anothers_card():
    with _db() as s:
        _signal(s, user_id=1, order_id="u1", fill_price=100.0)
        _signal(s, user_id=2, order_id="u2", fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 2)

        assert card.evaluated == 1


async def test_the_card_can_be_narrowed_to_one_symbol():
    with _db() as s:
        _signal(s, order_id="a", symbol="AAPL", fill_price=100.0)
        _signal(s, order_id="b", symbol="MSFT", fill_price=100.0)
        card = await SignalPerformanceService(
            _Quotes(AAPL=110.0, MSFT=50.0)
        ).scorecard(s, 1, symbol="aapl")

        assert card.evaluated == 1 and card.hit_rate_pct == 100.0


# ============================== CALIBRATION =================================

async def test_calibration_refuses_to_judge_a_thin_sample():
    """Five signals cannot calibrate anything; claiming otherwise is the exact
    false confidence this report exists to catch."""
    with _db() as s:
        for i in range(5):
            _signal(s, order_id=f"o{i}", fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        assert "yetersiz ornek" in card.calibration_note


async def test_calibration_reports_when_high_confidence_actually_wins():
    with _db() as s:
        for i in range(6):  # high confidence, all correct
            _signal(s, order_id=f"hi{i}", confidence=0.9, fill_price=100.0, symbol="AAPL")
        for i in range(6):  # low confidence, all wrong
            _signal(s, order_id=f"lo{i}", confidence=0.6, fill_price=100.0, symbol="MSFT")
        card = await SignalPerformanceService(
            _Quotes(AAPL=110.0, MSFT=90.0)
        ).scorecard(s, 1)

        assert "daha isabetli" in card.calibration_note


async def test_calibration_warns_when_confidence_is_backwards():
    """The finding that matters most: the gate is letting the WRONG trades through."""
    with _db() as s:
        for i in range(6):  # high confidence, all wrong
            _signal(s, order_id=f"hi{i}", confidence=0.9, fill_price=100.0, symbol="AAPL")
        for i in range(6):  # low confidence, all correct
            _signal(s, order_id=f"lo{i}", confidence=0.6, fill_price=100.0, symbol="MSFT")
        card = await SignalPerformanceService(
            _Quotes(AAPL=90.0, MSFT=110.0)
        ).scorecard(s, 1)

        assert "UYARI" in card.calibration_note
        assert "min_trade_confidence" in card.calibration_note


async def test_signals_are_grouped_into_confidence_buckets():
    with _db() as s:
        _signal(s, order_id="a", confidence=0.62, fill_price=100.0)
        _signal(s, order_id="b", confidence=0.65, fill_price=100.0)
        _signal(s, order_id="c", confidence=0.95, fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        labels = {b.label: b.signals for b in card.buckets}
        assert labels == {"0.60-0.70": 2, "0.90-1.00": 1}


async def test_signals_predating_the_confidence_column_are_left_out_of_buckets():
    """A null confidence has no bucket; inventing one would skew the grouping."""
    with _db() as s:
        _signal(s, order_id="old", confidence=None, fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        assert card.evaluated == 1, "still scored overall"
        assert card.buckets == [], "but not bucketed"


async def test_the_best_and_worst_signals_are_surfaced():
    with _db() as s:
        _signal(s, order_id="win", symbol="AAPL", fill_price=100.0)
        _signal(s, order_id="lose", symbol="MSFT", fill_price=100.0)
        card = await SignalPerformanceService(
            _Quotes(AAPL=150.0, MSFT=80.0)
        ).scorecard(s, 1)

        assert card.best.order_id == "win" and card.best.return_pct == pytest.approx(50.0)
        assert card.worst.order_id == "lose" and card.worst.return_pct == pytest.approx(-20.0)


async def test_the_card_states_that_its_numbers_are_unrealized():
    """Nobody may mistake a marked-to-market reading for booked P&L."""
    with _db() as s:
        _signal(s, fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        assert "unrealized" in card.basis


# ============================== HTTP SURFACE ================================

def test_the_scorecard_endpoint_requires_authentication(client):
    assert client.get("/api/trade/signal-scorecard").status_code in (401, 403)


def test_the_scorecard_endpoint_answers_for_a_user_with_no_history(client, auth_headers):
    res = client.get("/api/trade/signal-scorecard", headers=auth_headers)

    assert res.status_code == 200
    assert res.json()["evaluated"] == 0


async def test_calibration_declines_when_every_signal_sits_in_one_band():
    """With no spread in confidence there is nothing to compare against."""
    with _db() as s:
        for i in range(12):
            _signal(s, order_id=f"o{i}", confidence=0.9, fill_price=100.0)
        card = await SignalPerformanceService(_Quotes(AAPL=110.0)).scorecard(s, 1)

        assert "cok dar" in card.calibration_note


async def test_calibration_says_so_when_confidence_makes_no_difference():
    """Equal hit rates either side of the line: the gate is not discriminating."""
    with _db() as s:
        # 6 high-confidence and 6 low, each split 50/50 between winners and losers.
        for i in range(3):
            _signal(s, order_id=f"hw{i}", confidence=0.9, fill_price=100.0, symbol="AAPL")
            _signal(s, order_id=f"hl{i}", confidence=0.9, fill_price=100.0, symbol="MSFT")
            _signal(s, order_id=f"lw{i}", confidence=0.6, fill_price=100.0, symbol="AAPL")
            _signal(s, order_id=f"ll{i}", confidence=0.6, fill_price=100.0, symbol="MSFT")
        card = await SignalPerformanceService(
            _Quotes(AAPL=110.0, MSFT=90.0)
        ).scorecard(s, 1)

        assert "anlamli fark yok" in card.calibration_note
        assert card.hit_rate_pct == 50.0

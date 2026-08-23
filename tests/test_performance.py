"""The equity curve, and the honesty of its gaps.

Snapshots are captured lazily — one per UTC day the user was active — so the
series is sparse by construction. The property worth defending is that a gap
is reported as *unobserved* rather than smoothed into a flat line, because a
flat line reads as "held steady" when the truth is "we weren't looking".
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models.portfolio import RiskReport
from app.models.risk import EquitySnapshot
from app.services.performance_service import PerformanceService


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


class _Portfolio:
    """Stands in for the live marked portfolio at the right-hand edge of the curve."""

    def __init__(self, value: float, realized: float = 0.0) -> None:
        self.value, self.realized = value, realized

    async def risk_report(self, session, user_id) -> RiskReport:
        return RiskReport(
            total_value=self.value, total_realized_pnl=self.realized,
            total_unrealized_pnl=0.0, positions=[],
        )


def _snap(s: Session, date: str, equity: float, user_id: int = 1) -> None:
    s.add(EquitySnapshot(user_id=user_id, snapshot_date=date, opening_equity=equity))
    s.commit()


def _svc(current: float, realized: float = 0.0) -> PerformanceService:
    return PerformanceService(_Portfolio(current, realized))


# =============================== THE CURVE ==================================

async def test_the_curve_reads_oldest_to_newest():
    with _db() as s:
        _snap(s, "2026-08-03", 1300.0)
        _snap(s, "2026-08-01", 1000.0)
        _snap(s, "2026-08-02", 1100.0)

        report = await _svc(1400.0).report(s, 1)

        assert [p.date for p in report.points] == [
            "2026-08-01", "2026-08-02", "2026-08-03"
        ]
        assert [p.equity for p in report.points] == [1000.0, 1100.0, 1300.0]


async def test_return_is_measured_from_the_first_snapshot_to_live_equity():
    with _db() as s:
        _snap(s, "2026-08-01", 1000.0)

        report = await _svc(1250.0).report(s, 1)

        assert report.start_equity == 1000.0
        assert report.current_equity == 1250.0
        assert report.absolute_return == 250.0
        assert report.return_pct == pytest.approx(25.0)


async def test_realized_pnl_counts_toward_current_equity():
    """Equity is marked positions PLUS realized P&L — a booked gain is not lost."""
    with _db() as s:
        _snap(s, "2026-08-01", 1000.0)

        report = await _svc(800.0, realized=400.0).report(s, 1)

        assert report.current_equity == 1200.0


async def test_a_losing_period_reports_a_negative_return():
    with _db() as s:
        _snap(s, "2026-08-01", 1000.0)

        report = await _svc(750.0).report(s, 1)

        assert report.absolute_return == -250.0
        assert report.return_pct == pytest.approx(-25.0)


# ============================== DRAWDOWN ====================================

async def test_max_drawdown_measures_peak_to_trough_not_start_to_end():
    """A portfolio that recovers still had its drawdown — that is the risk figure."""
    with _db() as s:
        for date, eq in [
            ("2026-08-01", 1000.0), ("2026-08-02", 1500.0),  # peak
            ("2026-08-03", 900.0),                            # trough: -40%
        ]:
            _snap(s, date, eq)

        report = await _svc(1600.0).report(s, 1)  # fully recovered

        assert report.max_drawdown_pct == pytest.approx(40.0)
        assert report.return_pct > 0, "recovered overall, but the drawdown stands"


async def test_a_monotonically_rising_curve_has_no_drawdown():
    with _db() as s:
        _snap(s, "2026-08-01", 1000.0)
        _snap(s, "2026-08-02", 1100.0)

        report = await _svc(1200.0).report(s, 1)

        assert report.max_drawdown_pct == 0.0


async def test_the_best_and_worst_days_are_reported():
    with _db() as s:
        _snap(s, "2026-08-01", 1000.0)
        _snap(s, "2026-08-02", 1200.0)   # +20%
        _snap(s, "2026-08-03", 1080.0)   # -10%

        report = await _svc(1080.0).report(s, 1)

        assert report.best_day_pct == pytest.approx(20.0)
        assert report.worst_day_pct == pytest.approx(-10.0)


# ============================ HONEST GAPS ===================================

async def test_a_gap_in_the_series_is_flagged_sparse_not_interpolated():
    with _db() as s:
        _snap(s, "2026-08-01", 1000.0)
        _snap(s, "2026-08-10", 1100.0)   # nine days later

        report = await _svc(1100.0).report(s, 1)

        assert report.covered_days == 2
        assert report.span_days == 10
        assert report.sparse is True
        assert len(report.points) == 2, "the gap is left empty, never filled in"


async def test_a_complete_run_of_days_is_not_flagged_sparse():
    with _db() as s:
        _snap(s, "2026-08-01", 1000.0)
        _snap(s, "2026-08-02", 1010.0)
        _snap(s, "2026-08-03", 1020.0)

        report = await _svc(1030.0).report(s, 1)

        assert report.covered_days == report.span_days == 3
        assert report.sparse is False


async def test_a_user_with_no_snapshots_gets_todays_standing_not_a_fake_curve():
    with _db() as s:
        report = await _svc(500.0).report(s, 1)

        assert report.points == []
        assert report.covered_days == 0 and report.sparse is False
        assert report.start_equity == report.current_equity == 500.0
        assert report.return_pct == 0.0, "no history means no return to claim"


async def test_a_zero_starting_equity_does_not_divide_by_zero():
    with _db() as s:
        _snap(s, "2026-08-01", 0.0)

        report = await _svc(100.0).report(s, 1)

        assert report.return_pct == 0.0
        assert report.absolute_return == 100.0


async def test_the_window_keeps_the_most_recent_days():
    """`days` must trim the OLD end — a 2-day window means the last 2 days."""
    with _db() as s:
        for day, eq in [("01", 1000.0), ("02", 1100.0), ("03", 1200.0)]:
            _snap(s, f"2026-08-{day}", eq)

        report = await _svc(1300.0).report(s, 1, limit=2)

        assert [p.date for p in report.points] == ["2026-08-02", "2026-08-03"]


async def test_one_users_curve_never_includes_anothers_snapshots():
    with _db() as s:
        _snap(s, "2026-08-01", 1000.0, user_id=1)
        _snap(s, "2026-08-01", 9999.0, user_id=2)

        report = await _svc(1000.0).report(s, 2)

        assert [p.equity for p in report.points] == [9999.0]


# ============================== HTTP SURFACE ================================

def test_the_performance_endpoint_requires_authentication(client):
    assert client.get("/api/portfolio/performance").status_code in (401, 403)


def test_the_performance_endpoint_answers_for_a_new_user(client, auth_headers):
    res = client.get("/api/portfolio/performance", headers=auth_headers)

    assert res.status_code == 200
    body = res.json()
    assert body["points"] == [] and body["covered_days"] == 0


@pytest.mark.parametrize("days", [0, 5000])
def test_the_performance_endpoint_rejects_an_out_of_range_window(
    client, auth_headers, days
):
    res = client.get(
        "/api/portfolio/performance", params={"days": days}, headers=auth_headers
    )
    assert res.status_code == 422

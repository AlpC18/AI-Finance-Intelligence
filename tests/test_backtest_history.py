"""Persisted backtest history — results that survive the response.

A simulation used to be computed, returned once and discarded. Seeing a result
again meant re-running it against whatever bars existed by then, which makes it
a different result: the one number you wanted to compare against was
unreproducible by construction.

Two design choices carry most of the risk and are pinned below:

  * A run is IMMUTABLE. That is what makes it safe to store the headline
    metrics as columns as well as inside the JSON snapshot - the listing never
    parses JSON, and the two copies can never drift because nothing updates a
    row.
  * Everything is scoped to the owning user inside the query. History is the
    record of what someone's strategy did; leaking or deleting across tenants
    is the failure that matters here.
"""
from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.core.errors import AppError
from app.models.backtest import BacktestRequest, BacktestRun
from app.services.backtest_service import BacktestService


class _FakeProvider:
    """Down-then-up path, so the RSI rules produce a real round-trip."""

    async def get_history(self, symbol, period="6mo"):
        down = np.linspace(120, 80, 40)
        up = np.linspace(80.5, 125, 40)
        return pd.DataFrame({"Close": np.concatenate([down, up])})


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


def _svc() -> BacktestService:
    return BacktestService(_FakeProvider())


def _req(**kw) -> BacktestRequest:
    return BacktestRequest(**{"symbol": "AAPL", "period": "1y", "warmup": 20, **kw})


def _rows(s: Session, user_id: int = 1) -> list[BacktestRun]:
    return list(
        s.exec(select(BacktestRun).where(BacktestRun.user_id == user_id)).all()
    )


# ============================ SAVING A RUN =================================

async def test_a_run_is_persisted_and_comes_back_carrying_its_id():
    with _db() as s:
        report = await _svc().run_and_save(s, 1, _req())

        assert report.id is not None
        assert report.created_at is not None
        assert len(_rows(s)) == 1


async def test_the_stored_snapshot_round_trips_the_whole_report():
    """The curve and the trade list are the part that cannot be recomputed."""
    with _db() as s:
        svc = _svc()
        original = await svc.run_and_save(s, 1, _req())

        fetched = svc.get_run(s, 1, original.id)

        assert fetched.equity_curve == original.equity_curve
        assert fetched.trades == original.trades
        assert fetched.metrics == original.metrics
        assert fetched.bars == original.bars


async def test_the_request_parameters_are_stored_with_the_result():
    """A metric with no record of what produced it is unattributable."""
    with _db() as s:
        await _svc().run_and_save(
            s, 1, _req(period="6mo", rsi_buy=25, rsi_sell=75, initial_capital=5000)
        )

        (row,) = _rows(s)
        assert (row.period, row.rsi_buy, row.rsi_sell, row.initial_capital) == (
            "6mo", 25.0, 75.0, 5000.0
        )


async def test_the_listed_metrics_match_the_stored_snapshot():
    """The columns and the JSON are two copies of one immutable fact. If they
    can disagree, ranking history lies about what the run actually did."""
    with _db() as s:
        svc = _svc()
        report = await svc.run_and_save(s, 1, _req())

        listed = svc.list_runs(s, 1).items[0]

        assert listed.metrics == report.metrics


# ============================== LISTING ====================================

async def test_runs_are_listed_newest_first_with_a_total():
    with _db() as s:
        svc = _svc()
        for symbol in ("AAPL", "MSFT", "TSLA"):
            await svc.run_and_save(s, 1, _req(symbol=symbol))

        page = svc.list_runs(s, 1)

        assert page.total == 3
        assert [r.symbol for r in page.items] == ["TSLA", "MSFT", "AAPL"]


async def test_the_total_counts_every_match_not_just_the_page():
    """Otherwise a paginated UI cannot tell that it truncated anything."""
    with _db() as s:
        svc = _svc()
        for _ in range(5):
            await svc.run_and_save(s, 1, _req())

        page = svc.list_runs(s, 1, limit=2)

        assert page.total == 5 and len(page.items) == 2


async def test_listing_can_be_filtered_by_symbol():
    with _db() as s:
        svc = _svc()
        await svc.run_and_save(s, 1, _req(symbol="AAPL"))
        await svc.run_and_save(s, 1, _req(symbol="MSFT"))

        page = svc.list_runs(s, 1, symbol="aapl")

        assert page.total == 1 and page.items[0].symbol == "AAPL"


async def test_a_listing_never_shows_another_users_runs():
    with _db() as s:
        svc = _svc()
        await svc.run_and_save(s, 2, _req(symbol="SECRET"))

        page = svc.list_runs(s, 1)

        assert page.total == 0 and page.items == []


# ====================== FETCH AND DELETE ARE TENANT-SCOPED =================

async def test_fetching_another_users_run_is_a_404():
    with _db() as s:
        svc = _svc()
        theirs = await svc.run_and_save(s, 2, _req())

        with pytest.raises(AppError) as exc:
            svc.get_run(s, 1, theirs.id)

        assert exc.value.status_code == 404


async def test_deleting_another_users_run_is_a_404_and_leaves_it_alone():
    """The dangerous one: a 404 that still deleted would be silent data loss."""
    with _db() as s:
        svc = _svc()
        theirs = await svc.run_and_save(s, 2, _req())

        with pytest.raises(AppError) as exc:
            svc.delete_run(s, 1, theirs.id)

        assert exc.value.status_code == 404
        assert len(_rows(s, user_id=2)) == 1, "their run survived"


async def test_deleting_your_own_run_removes_it():
    with _db() as s:
        svc = _svc()
        report = await svc.run_and_save(s, 1, _req())

        svc.delete_run(s, 1, report.id)

        assert _rows(s) == []
        with pytest.raises(AppError):
            svc.get_run(s, 1, report.id)


async def test_an_unknown_run_id_is_a_404():
    with _db() as s:
        with pytest.raises(AppError) as exc:
            _svc().get_run(s, 1, 4242)

        assert exc.value.status_code == 404


# ============================== COMPARISON =================================

async def test_compare_returns_the_runs_in_the_order_asked_for():
    with _db() as s:
        svc = _svc()
        a = await svc.run_and_save(s, 1, _req(symbol="AAA"))
        b = await svc.run_and_save(s, 1, _req(symbol="BBB"))

        result = svc.compare(s, 1, [b.id, a.id])

        assert [r.id for r in result.runs] == [b.id, a.id]


async def test_compare_names_the_leader_on_each_metric():
    with _db() as s:
        svc = _svc()
        weak = await svc.run_and_save(s, 1, _req())
        strong = await svc.run_and_save(s, 1, _req())
        # Rewrite the stored numbers directly: the point under test is the
        # ranking, not the simulator that produced them.
        _set(s, weak.id, total_return_pct=1.0, sharpe_ratio=0.1, max_drawdown_pct=-30.0)
        _set(s, strong.id, total_return_pct=9.0, sharpe_ratio=2.0, max_drawdown_pct=-2.0)

        best = svc.compare(s, 1, [weak.id, strong.id]).best

        assert best["total_return_pct"] == strong.id
        assert best["sharpe_ratio"] == strong.id
        assert best["max_drawdown_pct"] == strong.id, "shallower drawdown wins"


async def test_an_unrankable_metric_is_omitted_rather_than_awarded():
    """profit_factor is None when a run had no losing trade. None means
    'undefined', not 'worst' - picking a winner from it would be invention."""
    with _db() as s:
        svc = _svc()
        a = await svc.run_and_save(s, 1, _req())
        b = await svc.run_and_save(s, 1, _req())
        _set(s, a.id, profit_factor=None)
        _set(s, b.id, profit_factor=None)

        best = svc.compare(s, 1, [a.id, b.id]).best

        assert "profit_factor" not in best
        assert "total_return_pct" in best, "the rankable metrics still rank"


async def test_compare_refuses_when_any_id_is_not_yours():
    """Dropping it silently would render a comparison that looks complete while
    missing the run the caller most likely cared about."""
    with _db() as s:
        svc = _svc()
        mine = await svc.run_and_save(s, 1, _req())
        theirs = await svc.run_and_save(s, 2, _req())

        with pytest.raises(AppError) as exc:
            svc.compare(s, 1, [mine.id, theirs.id])

        assert exc.value.status_code == 404
        assert str(theirs.id) in exc.value.message


def _set(s: Session, run_id: int, **fields) -> None:
    row = s.get(BacktestRun, run_id)
    for key, value in fields.items():
        setattr(row, key, value)
    s.add(row)
    s.commit()


# ============================ BOUNDED GROWTH ===============================

async def test_history_is_capped_and_drops_the_oldest_first():
    """An endpoint nobody thought about is how a table grows without anyone
    deciding to let it."""
    with _db() as s:
        svc = _svc()
        svc.MAX_RUNS_PER_USER = 3
        try:
            ids = [(await svc.run_and_save(s, 1, _req())).id for _ in range(5)]
        finally:
            del svc.MAX_RUNS_PER_USER

        kept = {r.id for r in _rows(s)}
        assert kept == set(ids[-3:]), "the three newest survived"


async def test_the_cap_is_per_user_not_global():
    with _db() as s:
        svc = _svc()
        svc.MAX_RUNS_PER_USER = 2
        try:
            for _ in range(3):
                await svc.run_and_save(s, 1, _req())
            await svc.run_and_save(s, 2, _req())
        finally:
            del svc.MAX_RUNS_PER_USER

        assert len(_rows(s, user_id=1)) == 2
        assert len(_rows(s, user_id=2)) == 1, "one user's churn cannot evict another's"


# ============================== OVER THE WIRE ==============================

from app.core.deps import get_backtest_service  # noqa: E402


@pytest.fixture
def wired(client):
    client.app.dependency_overrides[get_backtest_service] = lambda: _svc()
    return client


def _run(wired, headers, **kw) -> dict:
    res = wired.post("/api/backtest/run", headers=headers,
                     json={"symbol": "AAPL", "period": "1y", "warmup": 20, **kw})
    assert res.status_code == 200, res.text
    return res.json()


def test_running_a_backtest_saves_it_and_returns_the_id(wired, auth_headers):
    body = _run(wired, auth_headers)

    assert body["id"] is not None
    listed = wired.get("/api/backtest/runs", headers=auth_headers).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == body["id"]


def test_the_listing_reports_the_full_count_in_a_header(wired, auth_headers):
    """Matches how /api/trade/orders signals truncation."""
    for _ in range(3):
        _run(wired, auth_headers)

    res = wired.get("/api/backtest/runs?limit=1", headers=auth_headers)

    assert res.headers["X-Total-Count"] == "3"
    assert len(res.json()["items"]) == 1


def test_a_stored_run_is_fetched_whole(wired, auth_headers):
    saved = _run(wired, auth_headers)

    body = wired.get(f"/api/backtest/runs/{saved['id']}", headers=auth_headers).json()

    assert body["equity_curve"] == saved["equity_curve"]
    assert body["trades"] == saved["trades"]


def test_a_run_can_be_deleted(wired, auth_headers):
    saved = _run(wired, auth_headers)

    assert wired.delete(f"/api/backtest/runs/{saved['id']}",
                        headers=auth_headers).status_code == 204
    assert wired.get(f"/api/backtest/runs/{saved['id']}",
                     headers=auth_headers).status_code == 404


def test_history_endpoints_require_authentication(wired):
    assert wired.get("/api/backtest/runs").status_code == 401
    assert wired.get("/api/backtest/runs/1").status_code == 401
    assert wired.delete("/api/backtest/runs/1").status_code == 401
    assert wired.get("/api/backtest/runs/compare?ids=1").status_code == 401


def test_another_users_run_is_invisible_over_http(wired, auth_headers, make_user):
    saved = _run(wired, auth_headers)
    intruder = make_user("nosy@example.com")

    assert wired.get(f"/api/backtest/runs/{saved['id']}",
                     headers=intruder).status_code == 404
    assert wired.delete(f"/api/backtest/runs/{saved['id']}",
                        headers=intruder).status_code == 404
    assert wired.get("/api/backtest/runs", headers=intruder).json()["total"] == 0


def test_compare_is_not_shadowed_by_the_run_id_route(wired, auth_headers):
    """`/runs/compare` is declared before `/runs/{run_id}`; if that order ever
    flips, this asks for run id "compare" and gets a 422 instead."""
    a, b = _run(wired, auth_headers)["id"], _run(wired, auth_headers)["id"]

    res = wired.get(f"/api/backtest/runs/compare?ids={a},{b}", headers=auth_headers)

    assert res.status_code == 200, res.text
    assert [r["id"] for r in res.json()["runs"]] == [a, b]


@pytest.mark.parametrize(
    "ids, why",
    [
        ("", "empty"),
        ("   ", "whitespace only"),
        ("1,abc", "not numeric"),
        ("1,1", "the same run twice"),
        (",".join(str(i) for i in range(1, 13)), "over the cap"),
    ],
)
def test_compare_rejects_an_unusable_id_list(wired, auth_headers, ids, why):
    """Strict rather than lenient: a comparison that quietly drops what it could
    not parse looks complete while missing a run."""
    res = wired.get(f"/api/backtest/runs/compare?ids={ids}", headers=auth_headers)

    assert res.status_code == 422, why

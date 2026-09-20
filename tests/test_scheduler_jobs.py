"""The unattended sweeps — the code that touches money with nobody watching.

Each job runs on a timer, in a worker nobody is looking at, and one of them
(reconciliation) writes into the ledger. The invariant that matters across all
four is the same: a sweep processes every item it was given, and one bad item
degrades that item only. A sweep that dies halfway is the dangerous failure,
because the half it skipped is silently never retried until the next tick.

The lock TTL tests guard the other half of that promise: a lock that outlives
its interval turns a crashed leader into a job that simply stops running.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.core import scheduler as sched
from app.core.config import Settings, get_settings
from app.models.alert import Alert
from app.models.order import TradeOrder
from app.models.user import User


@contextmanager
def _engine_patched(monkeypatch):
    """Point the jobs' module-level engine at a disposable in-memory DB."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(sched, "engine", engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


class _Broadcaster:
    def __init__(self, fail: bool = False) -> None:
        self.delivered, self.published, self.fail = [], [], fail

    async def deliver_alert(self, user_id, frame, contact=None):
        if self.fail:
            raise RuntimeError("ws fan-out down")
        self.delivered.append((user_id, frame, contact))

    async def publish_event(self, frame):
        if self.fail:
            raise RuntimeError("ws fan-out down")
        self.published.append(frame)


class _AlertEval:
    """Evaluates alerts from a scripted verdict map; 'boom' raises."""

    def __init__(self, verdicts: dict) -> None:
        self.verdicts, self.seen = verdicts, []

    async def evaluate(self, alert: Alert) -> bool:
        self.seen.append(alert.symbol)
        verdict = self.verdicts.get(alert.symbol, False)
        if verdict == "boom":
            raise RuntimeError(f"{alert.symbol} evaluation exploded")
        return bool(verdict)


def _patch_alert_deps(monkeypatch, evaluator, broadcaster) -> None:
    import app.core.deps as deps
    import app.services.alert_service as alert_service

    monkeypatch.setattr(deps, "get_market_service", lambda: object())
    monkeypatch.setattr(deps, "get_ws_broadcaster", lambda: broadcaster)
    monkeypatch.setattr(alert_service, "AlertService", lambda market: evaluator)


def _alert(s: Session, symbol: str, user_id: int = 1, active: bool = True) -> Alert:
    a = Alert(user_id=user_id, symbol=symbol, condition_type="PRICE_ABOVE",
              threshold_value=1.0, is_active=active)
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


# ============================ ALERT SWEEP ==================================

async def test_one_exploding_alert_does_not_abort_the_sweep(monkeypatch):
    """The alert after the failure must still be evaluated and delivered."""
    with _engine_patched(monkeypatch) as s:
        _alert(s, "BOOM")
        _alert(s, "GOOD")
        evaluator = _AlertEval({"BOOM": "boom", "GOOD": True})
        bc = _Broadcaster()
        _patch_alert_deps(monkeypatch, evaluator, bc)

        await sched.run_alert_checks()

        assert evaluator.seen == ["BOOM", "GOOD"], "the sweep continued past the failure"
        assert [d[1]["symbol"] for d in bc.delivered] == ["GOOD"]


async def test_a_triggered_alert_records_when_it_fired(monkeypatch):
    with _engine_patched(monkeypatch) as s:
        alert = _alert(s, "AAPL")
        _patch_alert_deps(monkeypatch, _AlertEval({"AAPL": True}), _Broadcaster())

        await sched.run_alert_checks()

        s.refresh(alert)
        assert alert.last_triggered_at is not None


async def test_an_untriggered_alert_is_left_untouched(monkeypatch):
    with _engine_patched(monkeypatch) as s:
        alert = _alert(s, "AAPL")
        bc = _Broadcaster()
        _patch_alert_deps(monkeypatch, _AlertEval({"AAPL": False}), bc)

        await sched.run_alert_checks()

        s.refresh(alert)
        assert alert.last_triggered_at is None and bc.delivered == []


async def test_inactive_alerts_are_never_evaluated(monkeypatch):
    with _engine_patched(monkeypatch) as s:
        _alert(s, "OFF", active=False)
        _alert(s, "ON")
        evaluator = _AlertEval({"ON": True, "OFF": True})
        _patch_alert_deps(monkeypatch, evaluator, _Broadcaster())

        await sched.run_alert_checks()

        assert evaluator.seen == ["ON"]


async def test_a_delivery_outage_still_records_that_the_alert_fired(monkeypatch):
    """The trigger is a fact about the market; losing the push must not erase it."""
    with _engine_patched(monkeypatch) as s:
        alert = _alert(s, "AAPL")
        _patch_alert_deps(monkeypatch, _AlertEval({"AAPL": True}), _Broadcaster(fail=True))

        await sched.run_alert_checks()

        s.refresh(alert)
        assert alert.last_triggered_at is not None, "the fire is persisted regardless"


async def test_a_users_email_rides_along_for_the_offline_fallback(monkeypatch):
    with _engine_patched(monkeypatch) as s:
        user = User(email="trader@example.com", hashed_password="x")
        s.add(user)
        s.commit()
        s.refresh(user)
        _alert(s, "AAPL", user_id=user.id)
        bc = _Broadcaster()
        _patch_alert_deps(monkeypatch, _AlertEval({"AAPL": True}), bc)

        await sched.run_alert_checks()

        assert bc.delivered[0][2].email == "trader@example.com"


async def test_a_missing_user_does_not_break_delivery(monkeypatch):
    """An alert whose user row is gone still delivers, just without a contact."""
    with _engine_patched(monkeypatch) as s:
        _alert(s, "AAPL", user_id=999)
        bc = _Broadcaster()
        _patch_alert_deps(monkeypatch, _AlertEval({"AAPL": True}), bc)

        await sched.run_alert_checks()

        assert bc.delivered[0][2] is None


async def test_an_empty_alert_table_is_a_no_op(monkeypatch):
    with _engine_patched(monkeypatch):
        bc = _Broadcaster()
        _patch_alert_deps(monkeypatch, _AlertEval({}), bc)

        await sched.run_alert_checks()

        assert bc.delivered == []


# ======================= RECONCILIATION SWEEP ==============================

class _Recon:
    def __init__(self, written=1, raises=None) -> None:
        self.written, self.raises, self.calls = written, raises, 0

    async def reconcile_open_orders(self, session, broker_for):
        self.calls += 1
        if self.raises:
            raise self.raises
        return self.written

    async def position_drift(self, session, user_id, provider):
        if self.raises:
            raise self.raises
        return None


class _Trade:
    def __init__(self, provider=object(), raises=None) -> None:
        self.provider, self.raises, self.asked = provider, raises, []

    def provider_for_user(self, session, user_id):
        self.asked.append(user_id)
        if self.raises:
            raise self.raises
        return self.provider


def _patch_trade_deps(monkeypatch, recon, trade) -> None:
    import app.core.deps as deps

    monkeypatch.setattr(deps, "get_reconciliation_service", lambda: recon)
    monkeypatch.setattr(deps, "get_trade_service", lambda: trade)


async def test_a_failing_reconciliation_sweep_is_contained(monkeypatch):
    """A raising sweep must not escape into the scheduler and kill the job."""
    with _engine_patched(monkeypatch):
        recon = _Recon(raises=RuntimeError("broker API down"))
        _patch_trade_deps(monkeypatch, recon, _Trade())

        await sched.run_order_reconciliation()  # must not raise

        assert recon.calls == 1


async def test_unbuildable_credentials_yield_no_provider_instead_of_raising(monkeypatch):
    """The broker_for callback is handed to the sweep; it must never throw."""
    captured = {}

    class _Capture(_Recon):
        async def reconcile_open_orders(self, session, broker_for):
            captured["provider"] = broker_for(7)  # user with corrupt creds
            return 0

    with _engine_patched(monkeypatch):
        _patch_trade_deps(
            monkeypatch, _Capture(), _Trade(raises=ValueError("undecryptable key"))
        )

        await sched.run_order_reconciliation()

        assert captured["provider"] is None


async def test_a_clean_reconciliation_sweep_runs_to_completion(monkeypatch):
    with _engine_patched(monkeypatch):
        recon = _Recon(written=3)
        _patch_trade_deps(monkeypatch, recon, _Trade())

        await sched.run_order_reconciliation()

        assert recon.calls == 1


# ========================= POSITION SYNC SWEEP =============================

def _order(s: Session, user_id: int) -> None:
    s.add(TradeOrder(user_id=user_id, broker_order_id=f"o{user_id}",
                     symbol="AAPL", side="buy", quantity=1))
    s.commit()


async def test_one_users_drift_failure_does_not_stall_the_other_users(monkeypatch):
    """The core sweep invariant: user 3 is still checked after user 2 fails."""
    seen: list[int] = []

    class _PerUser(_Recon):
        async def position_drift(self, session, user_id, provider):
            seen.append(user_id)
            if user_id == 2:
                raise RuntimeError("broker timeout for user 2")

    with _engine_patched(monkeypatch) as s:
        for uid in (2, 3):
            _order(s, uid)
        _patch_trade_deps(monkeypatch, _PerUser(), _Trade())

        await sched.run_position_sync()

        assert sorted(seen) == [2, 3], "the failure did not end the sweep"


async def test_users_without_a_broker_provider_are_skipped(monkeypatch):
    checked: list[int] = []

    class _Track(_Recon):
        async def position_drift(self, session, user_id, provider):
            checked.append(user_id)

    with _engine_patched(monkeypatch) as s:
        _order(s, 5)
        _patch_trade_deps(monkeypatch, _Track(), _Trade(provider=None))

        await sched.run_position_sync()

        assert checked == []


async def test_each_user_is_synced_once_no_matter_how_many_orders(monkeypatch):
    checked: list[int] = []

    class _Track(_Recon):
        async def position_drift(self, session, user_id, provider):
            checked.append(user_id)

    with _engine_patched(monkeypatch) as s:
        for _ in range(3):
            s.add(TradeOrder(user_id=8, broker_order_id="dup", symbol="AAPL",
                             side="buy", quantity=1))
        s.commit()
        _patch_trade_deps(monkeypatch, _Track(), _Trade())

        await sched.run_position_sync()

        assert checked == [8], "three orders, one sync"


# =========================== EVENT SCAN ====================================

class _Events:
    def __init__(self, events=None, raises=None) -> None:
        self.events, self.raises = events or [], raises

    async def high_severity(self, threshold=8):
        if self.raises:
            raise self.raises
        return self.events


def _event(title: str, blocking: bool = False):
    from app.models.event import MarketEvent

    return MarketEvent(category="MACRO_POLICY", severity=9, orientation="BEARISH",
                       title=title, blocking=blocking)


def _patch_event_deps(monkeypatch, events, broadcaster) -> None:
    import app.core.deps as deps

    monkeypatch.setattr(deps, "get_event_service", lambda: events)
    monkeypatch.setattr(deps, "get_ws_broadcaster", lambda: broadcaster)


async def test_a_dead_event_feed_ends_the_scan_without_publishing(monkeypatch):
    bc = _Broadcaster()
    _patch_event_deps(monkeypatch, _Events(raises=RuntimeError("feed down")), bc)

    await sched.run_event_scan()  # must not raise

    assert bc.published == []


async def test_one_failed_push_does_not_drop_the_remaining_events(monkeypatch):
    pushed = []

    class _FlakyOnce(_Broadcaster):
        async def publish_event(self, frame):
            pushed.append(frame["title"])
            if frame["title"] == "first":
                raise RuntimeError("push failed")

    _patch_event_deps(
        monkeypatch, _Events([_event("first"), _event("second")]), _FlakyOnce()
    )

    await sched.run_event_scan()

    assert pushed == ["first", "second"], "the scan continued past the failed push"


async def test_high_severity_events_are_fanned_out(monkeypatch):
    bc = _Broadcaster()
    _patch_event_deps(monkeypatch, _Events([_event("CPI shock", blocking=True)]), bc)

    await sched.run_event_scan()

    assert bc.published[0]["title"] == "CPI shock"
    assert bc.published[0]["blocking"] is True


# ==================== LOCK TTL / SCHEDULER WIRING ==========================

@pytest.mark.parametrize("interval", [2, 5, 10, 30, 60, 300, 900, 3600])
def test_a_lock_always_expires_before_the_next_run_is_due(interval):
    """The whole point of the TTL: a crashed leader must not eat the next sweep."""
    assert sched._lock_ttl(interval) < interval
    assert sched._lock_ttl(interval) >= 1, "Redis rejects a zero TTL"


def test_a_short_poll_interval_does_not_invert_the_lock_ttl():
    """Regression: `interval - 5` floored at 10 gave a 5s poll a 10s lock."""
    assert sched._lock_ttl(5) == 4


@pytest.mark.parametrize(
    "flag,job_id",
    [
        ("order_reconciliation_enabled", "order-reconciliation"),
        ("position_sync_enabled", "position-sync"),
        ("event_scan_enabled", "event-scan"),
        ("paper_automation_enabled", "paper-automations"),
    ],
)
def test_a_disabled_sweep_is_never_registered(flag, job_id):
    off = Settings(anthropic_api_key="", **{flag: False})
    assert sched.build_scheduler(off).get_job(job_id) is None


def test_every_registered_sweep_refuses_to_overlap_itself():
    """max_instances=1 + coalesce: a slow sweep must not stack copies of itself."""
    scheduler = sched.build_scheduler(get_settings())

    jobs = scheduler.get_jobs()
    assert jobs, "expected at least the alert sweep"
    for job in jobs:
        assert job.max_instances == 1, job.id
        assert job.coalesce is True, job.id

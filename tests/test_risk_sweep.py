"""The automatic drawdown halt, evaluated on a timer instead of on request.

Before this sweep existed, `RiskService.status()` computed drawdown only when
something asked - an execute attempt, or a status read. Nobody was watching
between requests. A user who placed a resting order and closed the tab could
run straight through their daily loss limit with the switch never once
evaluating: `assert_not_halted` would have refused a NEW order, but no new
order was coming, and the working one kept filling.

That is the same shape of failure as a kill-switch that could not cancel - a
protection that only holds while someone is looking at it. The sweep closes it
by evaluating every armed limit periodically and flattening on trip.

The properties defended here:

  * it acts ONCE per day, not every interval (the drawdown condition stays true
    for the rest of the session, so an unlatched sweep would re-flatten and
    re-notify forever);
  * one bad account cannot stop the others from being checked;
  * the trip is recorded separately from the operator's manual switch, so
    "resume trading" never means "clear an automatic trip".
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import app.core.scheduler as sched
from app.models.risk import KillSwitchStatus, RiskSetting


@contextmanager
def _engine_patched(monkeypatch):
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


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _halted(reason: str = "daily_loss_limit", drawdown: float = -12.0) -> KillSwitchStatus:
    return KillSwitchStatus(
        halted=reason != "", manual_halt=reason == "manual", reason=reason,
        daily_loss_limit_pct=10.0, opening_equity=1000.0,
        current_equity=880.0, drawdown_pct=drawdown,
    )


def _calm() -> KillSwitchStatus:
    return KillSwitchStatus(
        halted=False, reason="", daily_loss_limit_pct=10.0,
        opening_equity=1000.0, current_equity=1010.0, drawdown_pct=1.0,
    )


class _Risk:
    """A stand-in for RiskService with real trip bookkeeping against the DB."""

    def __init__(self, session: Session, statuses: dict[int, KillSwitchStatus],
                 raises: dict[int, Exception] | None = None) -> None:
        self._session = session
        self._statuses = statuses
        self._raises = raises or {}
        self.evaluated: list[int] = []

    def users_with_automatic_limits(self, session):
        return list(
            session.exec(
                select(RiskSetting.user_id).where(RiskSetting.daily_loss_limit_pct > 0)
            ).all()
        )

    async def status(self, session, user_id):
        self.evaluated.append(user_id)
        if user_id in self._raises:
            raise self._raises[user_id]
        return self._statuses.get(user_id, _calm())

    def already_tripped_today(self, session, user_id):
        row = session.exec(
            select(RiskSetting).where(RiskSetting.user_id == user_id)
        ).first()
        return bool(row and row.auto_halt_tripped_on == _today())

    def mark_auto_halt_tripped(self, session, user_id):
        row = session.exec(
            select(RiskSetting).where(RiskSetting.user_id == user_id)
        ).first()
        row.auto_halt_tripped_on = _today()
        session.add(row)
        session.commit()


class _Trade:
    def __init__(self, canceled: int = 2, raises: Exception | None = None) -> None:
        self.flattened: list[tuple[int, str]] = []
        self._canceled = canceled
        self._raises = raises

    async def cancel_open_orders(self, session, user_id, source="kill_switch"):
        if self._raises:
            raise self._raises
        self.flattened.append((user_id, source))
        return self._canceled


class _Broadcaster:
    def __init__(self, raises: Exception | None = None) -> None:
        self.delivered: list[tuple[int, dict]] = []
        self._raises = raises

    async def deliver_alert(self, user_id, frame, contact=None):
        if self._raises:
            raise self._raises
        self.delivered.append((user_id, frame))
        return False


def _arm(s: Session, user_id: int, limit: float = 10.0,
         tripped_on: str | None = None) -> RiskSetting:
    row = RiskSetting(
        user_id=user_id, daily_loss_limit_pct=limit, auto_halt_tripped_on=tripped_on
    )
    s.add(row)
    s.commit()
    s.refresh(row)
    return row


def _wire(monkeypatch, risk, trade, broadcaster) -> None:
    import app.core.deps as deps

    monkeypatch.setattr(deps, "get_risk_service", lambda: risk)
    monkeypatch.setattr(deps, "get_trade_service", lambda: trade)
    monkeypatch.setattr(deps, "get_ws_broadcaster", lambda: broadcaster)


# ============================== THE TRIP ===================================

async def test_a_breached_limit_flattens_the_account(monkeypatch):
    """The headline: the automatic limit now DOES something on its own."""
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        trade = _Trade()
        _wire(monkeypatch, _Risk(s, {1: _halted()}), trade, _Broadcaster())

        await sched.run_risk_sweep()

        assert trade.flattened == [(1, "auto_halt")]


async def test_the_flatten_is_labelled_as_automatic_not_operator_driven(monkeypatch):
    """The metric label separates 'the market moved' from 'someone pulled the
    handle'. They page different people."""
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        trade = _Trade()
        _wire(monkeypatch, _Risk(s, {1: _halted()}), trade, _Broadcaster())

        await sched.run_risk_sweep()

        assert trade.flattened[0][1] == "auto_halt"


async def test_the_user_is_told_their_account_was_halted(monkeypatch):
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        bc = _Broadcaster()
        _wire(monkeypatch, _Risk(s, {1: _halted()}), _Trade(canceled=3), bc)

        await sched.run_risk_sweep()

        (user_id, frame) = bc.delivered[0]
        assert user_id == 1
        assert frame["type"] == "halt"
        assert frame["reason"] == "daily_loss_limit"
        assert frame["canceled_orders"] == 3
        assert frame["drawdown_pct"] == -12.0


async def test_the_halt_notice_goes_out_through_the_escalating_path(monkeypatch):
    """`deliver_alert`, not a plain publish: being flattened is exactly the
    event a user must not miss for want of an open socket."""
    calls = []

    class _Recording(_Broadcaster):
        async def deliver_alert(self, user_id, frame, contact=None):
            calls.append("deliver_alert")
            return False

        async def publish_alert(self, user_id, frame):  # pragma: no cover
            calls.append("publish_alert")

    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        _wire(monkeypatch, _Risk(s, {1: _halted()}), _Trade(), _Recording())

        await sched.run_risk_sweep()

        assert calls == ["deliver_alert"]


# ========================= ONCE PER DAY, NOT PER SWEEP =====================

async def test_a_second_sweep_the_same_day_does_not_re_flatten(monkeypatch):
    """The drawdown stays breached for the rest of the session, so an unlatched
    sweep would cancel and notify every five minutes forever."""
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        trade, bc = _Trade(), _Broadcaster()
        _wire(monkeypatch, _Risk(s, {1: _halted()}), trade, bc)

        await sched.run_risk_sweep()
        await sched.run_risk_sweep()
        await sched.run_risk_sweep()

        assert trade.flattened == [(1, "auto_halt")], "flattened exactly once"
        assert len(bc.delivered) == 1, "and notified exactly once"


async def test_the_trip_is_recorded_against_today(monkeypatch):
    with _engine_patched(monkeypatch) as s:
        row = _arm(s, 1)
        _wire(monkeypatch, _Risk(s, {1: _halted()}), _Trade(), _Broadcaster())

        await sched.run_risk_sweep()

        s.refresh(row)
        assert row.auto_halt_tripped_on == _today()


async def test_yesterdays_trip_does_not_suppress_todays(monkeypatch):
    """Drawdown resets with the daily opening snapshot, so tomorrow is a new
    limit and must be able to trip on its own."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1, tripped_on=yesterday)
        trade = _Trade()
        _wire(monkeypatch, _Risk(s, {1: _halted()}), trade, _Broadcaster())

        await sched.run_risk_sweep()

        assert trade.flattened == [(1, "auto_halt")]


async def test_the_automatic_trip_never_writes_the_operators_manual_switch(monkeypatch):
    """Overloading manual_halt would make 'resume trading' indistinguishable
    from 'clear an automatic trip'."""
    with _engine_patched(monkeypatch) as s:
        row = _arm(s, 1)
        _wire(monkeypatch, _Risk(s, {1: _halted()}), _Trade(), _Broadcaster())

        await sched.run_risk_sweep()

        s.refresh(row)
        assert row.manual_halt is False


# ====================== WHAT MUST NOT TRIGGER A FLATTEN ====================

async def test_an_account_within_its_limit_is_left_alone(monkeypatch):
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        trade, bc = _Trade(), _Broadcaster()
        _wire(monkeypatch, _Risk(s, {1: _calm()}), trade, bc)

        await sched.run_risk_sweep()

        assert trade.flattened == [] and bc.delivered == []


async def test_a_manual_halt_is_not_re_flattened_by_the_sweep(monkeypatch):
    """The operator toggle already flattened when it was pulled. Doing it again
    here would re-notify a user who has been told, on every interval."""
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        trade = _Trade()
        _wire(monkeypatch, _Risk(s, {1: _halted(reason="manual")}), trade, _Broadcaster())

        await sched.run_risk_sweep()

        assert trade.flattened == []


async def test_accounts_without_an_armed_limit_are_never_evaluated(monkeypatch):
    """A limit of 0 means the automatic halt is off. Sweeping those accounts is
    the difference between a cheap check and walking every user on the platform.
    """
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1, limit=0.0)
        _arm(s, 2, limit=5.0)
        risk = _Risk(s, {1: _halted(), 2: _calm()})
        _wire(monkeypatch, risk, _Trade(), _Broadcaster())

        await sched.run_risk_sweep()

        assert risk.evaluated == [2]


# ============================ CONTAINMENT =================================

async def test_one_exploding_account_does_not_abort_the_sweep(monkeypatch):
    """The account after the failure must still be evaluated."""
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        _arm(s, 2)
        risk = _Risk(s, {2: _halted()}, raises={1: RuntimeError("equity unavailable")})
        trade = _Trade()
        _wire(monkeypatch, risk, trade, _Broadcaster())

        await sched.run_risk_sweep()

        assert risk.evaluated == [1, 2]
        assert trade.flattened == [(2, "auto_halt")]


async def test_a_failing_flatten_does_not_kill_the_sweep(monkeypatch):
    with _engine_patched(monkeypatch) as s:
        _arm(s, 1)
        _arm(s, 2)
        risk = _Risk(s, {1: _halted(), 2: _halted()})
        _wire(monkeypatch, risk, _Trade(raises=RuntimeError("venue down")), _Broadcaster())

        await sched.run_risk_sweep()  # must not raise

        assert risk.evaluated == [1, 2]


async def test_a_failed_notification_does_not_undo_the_halt(monkeypatch):
    """The flatten already happened at the venue. Rolling back the local record
    because a push failed would let the sweep flatten again next interval."""
    with _engine_patched(monkeypatch) as s:
        row = _arm(s, 1)
        trade = _Trade()
        _wire(monkeypatch, _Risk(s, {1: _halted()}), trade,
              _Broadcaster(raises=RuntimeError("redis down")))

        await sched.run_risk_sweep()

        s.refresh(row)
        assert row.auto_halt_tripped_on == _today(), "the trip stands"
        assert trade.flattened == [(1, "auto_halt")]


async def test_an_unlistable_account_set_ends_the_sweep_quietly(monkeypatch):
    """A database failure at the very first step must not escape into APScheduler
    and take the job out of the rotation."""
    class _Broken(_Risk):
        def users_with_automatic_limits(self, session):
            raise RuntimeError("database gone")

    with _engine_patched(monkeypatch) as s:
        _wire(monkeypatch, _Broken(s, {}), _Trade(), _Broadcaster())

        await sched.run_risk_sweep()  # must not raise


# ============================ JOB REGISTRATION =============================

def test_the_sweep_is_registered_and_can_be_disabled():
    from app.core.config import Settings

    on = sched.build_scheduler(Settings(anthropic_api_key="", risk_sweep_enabled=True))
    off = sched.build_scheduler(Settings(anthropic_api_key="", risk_sweep_enabled=False))

    assert on.get_job("risk-sweep") is not None
    assert off.get_job("risk-sweep") is None


def test_the_sweeps_lock_expires_before_its_next_run_is_due():
    """A lock outliving its interval is worse than none: every run inside the
    remaining TTL finds it held and skips, so the job silently stops."""
    from app.core.config import Settings

    settings = Settings(anthropic_api_key="", risk_sweep_interval_minutes=5)
    ttl = sched._lock_ttl(settings.risk_sweep_interval_minutes * 60)

    assert 0 < ttl < settings.risk_sweep_interval_minutes * 60


# ==================== THE REAL RiskService BOOKKEEPING =====================
# The sweep tests above use a stand-in so they can drive drawdown directly.
# These pin the actual implementation the sweep calls.

from app.services.risk_service import RiskService  # noqa: E402


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


def _service() -> RiskService:
    return RiskService(portfolio=None)  # these methods never touch the portfolio


def test_only_accounts_with_an_armed_limit_are_listed():
    with _db() as s:
        _arm(s, 1, limit=0.0)
        _arm(s, 2, limit=5.0)
        _arm(s, 3, limit=0.5)

        assert sorted(_service().users_with_automatic_limits(s)) == [2, 3]


def test_an_account_that_never_tripped_reads_as_not_tripped():
    with _db() as s:
        _arm(s, 1)

        assert _service().already_tripped_today(s, 1) is False


def test_marking_a_trip_makes_it_read_back_as_tripped_today():
    with _db() as s:
        _arm(s, 1)
        svc = _service()

        svc.mark_auto_halt_tripped(s, 1)

        assert svc.already_tripped_today(s, 1) is True


def test_a_trip_recorded_yesterday_does_not_count_as_today():
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    with _db() as s:
        _arm(s, 1, tripped_on=yesterday)

        assert _service().already_tripped_today(s, 1) is False


def test_marking_a_trip_creates_the_row_when_there_is_none():
    """The sweep only visits armed accounts, but the method must not assume a
    row exists - a missing one would otherwise raise inside the sweep."""
    with _db() as s:
        svc = _service()

        svc.mark_auto_halt_tripped(s, 99)

        assert svc.already_tripped_today(s, 99) is True


def test_one_accounts_trip_does_not_mark_another():
    with _db() as s:
        _arm(s, 1)
        _arm(s, 2)
        svc = _service()

        svc.mark_auto_halt_tripped(s, 1)

        assert svc.already_tripped_today(s, 2) is False

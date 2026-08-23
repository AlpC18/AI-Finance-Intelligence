"""Background alert engine on AsyncIOScheduler — runs on the event loop.

Jobs are coroutines using the async market provider, so a sweep never blocks
request handling. Each alert is evaluated defensively in isolation.
"""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from app.core.config import Settings
from app.core.distributed_lock import with_leader_lock
from app.db.database import engine
from app.models.alert import Alert

logger = logging.getLogger("alerts")


async def run_alert_checks() -> None:
    from app.core.deps import get_market_service, get_ws_broadcaster
    from app.models.user import User
    from app.services.alert_service import AlertService

    service = AlertService(get_market_service())
    broadcaster = get_ws_broadcaster()
    with Session(engine) as session:
        alerts = list(
            session.exec(select(Alert).where(Alert.is_active == True)).all()  # noqa: E712
        )
        for alert in alerts:
            try:
                if await service.evaluate(alert):
                    triggered_at = datetime.now(timezone.utc)
                    logger.info(
                        "ALERT TRIGGERED: user=%s symbol=%s condition=%s threshold=%s",
                        alert.user_id,
                        alert.symbol,
                        alert.condition_type,
                        alert.threshold_value,
                    )
                    alert.last_triggered_at = triggered_at
                    session.add(alert)
                    contact = _contact_for(session.get(User, alert.user_id))
                    await _push_alert(broadcaster, alert, triggered_at, contact)
            except Exception as exc:  # noqa: BLE001 - one bad alert must not kill sweep
                logger.warning("Alert %s evaluation failed: %s", alert.id, exc)
        session.commit()


def _contact_for(user):
    """Build an out-of-band contact from the alerting user's email, if any."""
    from app.core.notifications import NotificationContact

    email = getattr(user, "email", "") or ""
    return NotificationContact(email=email) if email else None


async def _push_alert(broadcaster, alert: Alert, triggered_at: datetime, contact=None) -> None:
    """Deliver the alert cross-worker; escalate to the fallback sink if offline.

    A triggered watch alert is treated as CRITICAL: if no live socket is confirmed
    for the user, ``deliver_alert`` escalates it to email/webhook so it is not lost.
    """
    from app.models.ws import WsAlertFrame

    frame = WsAlertFrame(
        alert_id=alert.id,
        symbol=alert.symbol,
        condition=alert.condition_type,
        threshold=alert.threshold_value,
        triggered_at=triggered_at.isoformat(),
    ).model_dump()
    try:
        await broadcaster.deliver_alert(alert.user_id, frame, contact)
    except Exception as exc:  # noqa: BLE001 - a delivery failure must not fail the sweep
        logger.warning("Alert %s push failed: %s", alert.id, exc)


async def run_risk_sweep() -> None:
    """Evaluate every armed daily-loss limit and flatten the accounts that broke it.

    This is what makes the automatic halt a protection rather than a report.
    ``RiskService.status()`` computes drawdown only when something asks, and the
    only things that asked were an execute attempt and a status read - so a user
    who left a resting order and closed the tab could run straight through their
    limit with the switch never once evaluating.

    Defensive in the same shape as the other sweeps: one user's bad credentials
    or unreachable venue must not stop the others from being checked. Every
    account is evaluated inside its own try.
    """
    from app.core.deps import get_risk_service, get_trade_service, get_ws_broadcaster
    from app.models.user import User

    risk = get_risk_service()
    trade = get_trade_service()
    broadcaster = get_ws_broadcaster()
    with Session(engine) as session:
        try:
            user_ids = risk.users_with_automatic_limits(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Risk sweep could not list accounts: %s", exc)
            return
        for user_id in user_ids:
            try:
                await _evaluate_one_account(session, user_id, risk, trade, broadcaster)
            except Exception as exc:  # noqa: BLE001 - one account must not kill sweep
                logger.warning("Risk sweep failed for user %s: %s", user_id, exc)


async def _evaluate_one_account(session, user_id: int, risk, trade, broadcaster) -> None:
    """Trip the automatic halt for one account, at most once per UTC day."""
    from app.core.metrics import record_risk_halt_tripped

    status = await risk.status(session, user_id)
    if not status.halted or status.reason != "daily_loss_limit":
        return
    # The drawdown condition keeps evaluating true for the rest of the day, so
    # without this the sweep would re-flatten every interval and re-notify a
    # user who has already been told and already has nothing working.
    if risk.already_tripped_today(session, user_id):
        return

    canceled = await trade.cancel_open_orders(session, user_id, source="auto_halt")
    risk.mark_auto_halt_tripped(session, user_id)
    record_risk_halt_tripped()
    logger.warning(
        "AUTO HALT: user=%s drawdown=%.2f%% limit=%.2f%% flattened=%s",
        user_id, status.drawdown_pct, status.daily_loss_limit_pct, canceled,
    )
    await _push_halt(session, broadcaster, user_id, status, canceled)


async def _push_halt(session, broadcaster, user_id: int, status, canceled: int) -> None:
    """Tell the user their account was halted, escalating if they are offline.

    Delivered through ``deliver_alert`` rather than a plain publish: an account
    being flattened is precisely the event a user must not miss because they
    happened to have no socket open.
    """
    from app.models.user import User
    from app.models.ws import WsHaltFrame

    frame = WsHaltFrame(
        reason=status.reason,
        drawdown_pct=status.drawdown_pct,
        daily_loss_limit_pct=status.daily_loss_limit_pct,
        canceled_orders=canceled,
        halted_at=datetime.now(timezone.utc).isoformat(),
    ).model_dump()
    try:
        contact = _contact_for(session.get(User, user_id))
        await broadcaster.deliver_alert(user_id, frame, contact)
    except Exception as exc:  # noqa: BLE001 - a push failure must not undo the halt
        logger.warning("Halt push failed for user %s: %s", user_id, exc)


async def run_order_reconciliation() -> None:
    """Poll open broker orders and reconcile fills into the ledger (non-blocking)."""
    from app.core.deps import get_reconciliation_service, get_trade_service

    recon = get_reconciliation_service()
    trade = get_trade_service()
    with Session(engine) as session:
        def broker_for(user_id: int):
            try:
                return trade.provider_for_user(session, user_id)
            except Exception as exc:  # noqa: BLE001 - bad creds must not kill sweep
                logger.warning("Provider build failed for user %s: %s", user_id, exc)
                return None

        try:
            written = await recon.reconcile_open_orders(session, broker_for)
            if written:
                logger.info("Reconciled %s fill(s) into the ledger.", written)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Order reconciliation sweep failed: %s", exc)


async def run_position_sync() -> None:
    """Compare broker positions vs the ledger for every user holding orders."""
    from app.core.deps import get_reconciliation_service, get_trade_service
    from app.models.order import TradeOrder

    recon = get_reconciliation_service()
    trade = get_trade_service()
    with Session(engine) as session:
        # A single-column select yields scalars, not 1-tuples: unpacking them
        # raised TypeError and killed this sweep before it checked anybody.
        user_ids = set(session.exec(select(TradeOrder.user_id)).all())
        for user_id in user_ids:
            provider = trade.provider_for_user(session, user_id)
            if provider is None:
                continue
            try:
                await recon.position_drift(session, user_id, provider)  # logs drift
            except Exception as exc:  # noqa: BLE001 - one user must not kill sweep
                logger.warning("Position sync failed for user %s: %s", user_id, exc)


async def run_event_scan() -> None:
    """Fetch high-severity events and fan them out to all clients (non-blocking)."""
    from app.core.deps import get_event_service, get_ws_broadcaster
    from app.models.ws import WsEventFrame

    service = get_event_service()
    broadcaster = get_ws_broadcaster()
    try:
        events = await service.high_severity(threshold=8)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Event scan failed: %s", exc)
        return
    for event in events:
        frame = WsEventFrame(
            category=event.category,
            severity=event.severity,
            orientation=event.orientation,
            title=event.title,
            blocking=event.blocking,
        ).model_dump()
        try:
            await broadcaster.publish_event(frame)
        except Exception as exc:  # noqa: BLE001 - one push must not kill the scan
            logger.warning("Event push failed: %s", exc)


def _lock_ttl(interval_seconds: int) -> int:
    """Lock lifetime for a job that runs every `interval_seconds`.

    The TTL must expire STRICTLY BEFORE the next run is due. A lock that
    outlives its interval is worse than no lock: when a leader crashes
    mid-sweep, every run scheduled inside the remaining TTL finds the lock
    still held and skips silently, so the job just stops happening.

    Scaling to 90% of the interval keeps that margin at every interval length;
    the older `interval - 10` inverted for short poll intervals (a 5s poll got
    a 10s lock). One second is the floor, since Redis rejects a zero TTL.
    """
    return max(1, min(interval_seconds - 1, int(interval_seconds * 0.9)))


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    """Wire interval jobs, each guarded by a Redis leader lock so exactly one
    worker runs a sweep. Lock TTLs auto-expire below the interval so a crashed
    leader never deadlocks the job.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    alerts_ttl = _lock_ttl(settings.alert_interval_minutes * 60)
    recon_ttl = _lock_ttl(settings.order_poll_interval_seconds)
    possync_ttl = _lock_ttl(settings.position_sync_interval_minutes * 60)
    risk_ttl = _lock_ttl(settings.risk_sweep_interval_minutes * 60)

    scheduler.add_job(
        with_leader_lock("alert-checks", alerts_ttl)(run_alert_checks),
        trigger="interval",
        minutes=settings.alert_interval_minutes,
        id="alert-checks",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    if settings.order_reconciliation_enabled:
        scheduler.add_job(
            with_leader_lock("order-reconciliation", recon_ttl)(run_order_reconciliation),
            trigger="interval",
            seconds=settings.order_poll_interval_seconds,
            id="order-reconciliation",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    if settings.position_sync_enabled:
        scheduler.add_job(
            with_leader_lock("position-sync", possync_ttl)(run_position_sync),
            trigger="interval",
            minutes=settings.position_sync_interval_minutes,
            id="position-sync",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    if settings.risk_sweep_enabled:
        scheduler.add_job(
            with_leader_lock("risk-sweep", risk_ttl)(run_risk_sweep),
            trigger="interval",
            minutes=settings.risk_sweep_interval_minutes,
            id="risk-sweep",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    if settings.event_scan_enabled:
        scheduler.add_job(
            with_leader_lock(
                "event-scan", _lock_ttl(settings.event_scan_interval_minutes * 60)
            )(run_event_scan),
            trigger="interval",
            minutes=settings.event_scan_interval_minutes,
            id="event-scan",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    return scheduler

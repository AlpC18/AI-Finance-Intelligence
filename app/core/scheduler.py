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
        user_ids = {uid for (uid,) in session.exec(select(TradeOrder.user_id)).all()}
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


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    """Wire interval jobs, each guarded by a Redis leader lock so exactly one
    worker runs a sweep. Lock TTLs auto-expire below the interval so a crashed
    leader never deadlocks the job.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    alerts_ttl = max(30, settings.alert_interval_minutes * 60 - 10)
    recon_ttl = max(10, settings.order_poll_interval_seconds - 5)
    possync_ttl = max(60, settings.position_sync_interval_minutes * 60 - 10)

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
    if settings.event_scan_enabled:
        scheduler.add_job(
            with_leader_lock("event-scan", max(60, settings.event_scan_interval_minutes * 60 - 10))(run_event_scan),
            trigger="interval",
            minutes=settings.event_scan_interval_minutes,
            id="event-scan",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    return scheduler

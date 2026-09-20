"""Dependency injection: cache, providers, services, auth, vector store, WS manager."""
import logging
from functools import lru_cache

from app.core.cache import CacheBackend, InMemoryTTLCache
from app.core.config import get_settings
from app.core.token_store import InMemoryBlocklist, TokenBlocklist
from app.core.vector_store import InMemoryVectorStore, VectorStore
from app.providers.market_provider import YahooMarketProvider
from app.providers.rss_news_provider import RssNewsProvider
from app.services.ai_service import AIService
from app.services.alert_service import AlertService
from app.services.auth_service import AuthService
from app.services.event_intelligence_service import EventIntelligenceService
from app.services.market_service import MarketService
from app.services.news_service import NewsService
from app.services.portfolio_service import PortfolioService
from app.services.quant_service import QuantService
from app.services.backtest_service import BacktestService
from app.services.key_rotation_service import KeyRotationService
from app.services.performance_service import PerformanceService
from app.services.reconciliation_service import TradeReconciliationService
from app.services.signal_performance_service import SignalPerformanceService
from app.services.risk_service import RiskService
from app.services.trade_service import TradeService
from app.services.watchlist_service import WatchlistService
from app.services.tax_lot_service import TaxLotService
from app.services.paper_trading_service import PaperTradingService
from app.services.automation_service import AutomationService
from app.services.goal_service import GoalService
from app.services.rebalance_service import RebalanceService
from app.services.activity_service import ActivityService
from app.services.asset_service import AssetService
from app.services.consumer_service import ConsumerService

logger = logging.getLogger("deps")


@lru_cache
def get_cache() -> CacheBackend:
    settings = get_settings()
    fallback = InMemoryTTLCache(settings.cache_ttl_seconds)
    if settings.cache_backend == "redis":
        try:
            from app.core.redis_cache import RedisCache

            return RedisCache(settings.redis_url, settings.cache_ttl_seconds, fallback)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis cache init failed (%s); using in-memory.", exc)
    return fallback


@lru_cache
def get_token_store() -> TokenBlocklist:
    settings = get_settings()
    fallback = InMemoryBlocklist()
    if settings.cache_backend == "redis":
        try:
            from app.core.token_store import RedisTokenStore

            return RedisTokenStore(settings.redis_url, fallback)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis token store init failed (%s); using in-memory.", exc)
    return fallback


@lru_cache
def get_vector_store() -> VectorStore:
    """Shared RAG index: NewsService writes to it, AIService reads from it.

    On PostgreSQL, use the persistent pgvector store so embeddings survive restarts
    and are shared across all workers; otherwise (SQLite/dev) fall back to the
    in-memory store. A construction failure degrades to in-memory, never blocks boot.
    """
    settings = get_settings()
    if settings.database_url.startswith(("postgres://", "postgresql")):
        try:
            from app.core.pgvector_store import PgVectorStore

            return PgVectorStore(settings.database_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pgvector store init failed (%s); using in-memory.", exc)
    return InMemoryVectorStore()


def _redis_client_or_none():
    """A shared async Redis client when the redis backend is configured, else None."""
    settings = get_settings()
    if settings.cache_backend != "redis":
        return None
    try:
        from redis.asyncio import Redis

        return Redis.from_url(settings.redis_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis client init failed (%s); WS presence local-only.", exc)
        return None


@lru_cache
def get_connection_manager():
    """Shared WebSocket registry: WS route registers sockets, scheduler pushes.

    In redis mode it also maintains a cross-worker presence refcount so the alert
    fallback can tell whether a user is reachable on ANY worker.
    """
    from app.api.websockets import ConnectionManager

    return ConnectionManager(redis=_redis_client_or_none())


@lru_cache
def get_ai_service() -> AIService:
    return AIService(get_settings(), retriever=get_vector_store())


@lru_cache
def get_auth_service() -> AuthService:
    return AuthService(get_settings())


@lru_cache
def get_event_service() -> EventIntelligenceService:
    from app.providers.event_feed_provider import EventFeedProvider

    return EventIntelligenceService(EventFeedProvider())


@lru_cache
def get_failover_market_provider():
    """Yahoo primary with a Stooq fallback so one upstream outage can't freeze signals."""
    from app.providers.failover_market_provider import FailoverMarketProvider
    from app.providers.stooq_market_provider import StooqMarketProvider

    return FailoverMarketProvider(
        [("yahoo", YahooMarketProvider()), ("stooq", StooqMarketProvider())]
    )


@lru_cache
def get_market_service() -> MarketService:
    return MarketService(
        get_failover_market_provider(),
        get_ai_service(),
        get_cache(),
        events=get_event_service(),
    )


@lru_cache
def get_notification_service():
    """Out-of-band alert sink (webhook/email) for the offline-user fallback."""
    from app.core.notifications import NotificationService

    return NotificationService(get_settings())


@lru_cache
def get_news_service() -> NewsService:
    return NewsService(
        RssNewsProvider(), get_ai_service(), get_cache(), store=get_vector_store()
    )


@lru_cache
def get_portfolio_service() -> PortfolioService:
    return PortfolioService(YahooMarketProvider(), get_ai_service())


@lru_cache
def get_alert_service() -> AlertService:
    return AlertService(get_market_service())


@lru_cache
def get_risk_service() -> RiskService:
    return RiskService(get_portfolio_service())


@lru_cache
def get_reconciliation_service() -> TradeReconciliationService:
    return TradeReconciliationService(get_portfolio_service())


@lru_cache
def get_backtest_service() -> BacktestService:
    return BacktestService(YahooMarketProvider())


@lru_cache
def get_trade_service() -> TradeService:
    return TradeService(
        get_settings(), get_market_service(), risk=get_risk_service(),
        activity=get_activity_service(),
    )


@lru_cache
def get_watchlist_service() -> WatchlistService:
    return WatchlistService(get_activity_service())


@lru_cache
def get_activity_service() -> ActivityService:
    return ActivityService()


@lru_cache
def get_asset_service() -> AssetService:
    return AssetService()


@lru_cache
def get_consumer_service() -> ConsumerService:
    return ConsumerService()


@lru_cache
def get_task_queue():
    """Redis Streams in production; local trusted-task adapter otherwise."""
    from app.core.task_queue import LocalTaskQueue, RedisTaskQueue

    settings = get_settings()
    if settings.cache_backend == "redis" and settings.worker_queue_enabled:
        client = _redis_client_or_none()
        if client is not None:
            return RedisTaskQueue(client)
    return LocalTaskQueue()


@lru_cache
def get_tax_lot_service() -> TaxLotService:
    return TaxLotService(get_portfolio_service())


@lru_cache
def get_paper_trading_service() -> PaperTradingService:
    return PaperTradingService(get_market_service())


@lru_cache
def get_automation_service() -> AutomationService:
    return AutomationService(get_market_service(), get_paper_trading_service())


@lru_cache
def get_goal_service() -> GoalService:
    return GoalService(get_performance_service())


@lru_cache
def get_rebalance_service() -> RebalanceService:
    return RebalanceService(YahooMarketProvider(), get_portfolio_service())


@lru_cache
def get_signal_performance_service() -> SignalPerformanceService:
    """Grades executed signals against the live feed the rest of the app uses."""
    return SignalPerformanceService(get_market_service())


@lru_cache
def get_performance_service() -> PerformanceService:
    return PerformanceService(get_portfolio_service())


@lru_cache
def get_key_rotation_service() -> KeyRotationService:
    return KeyRotationService()


@lru_cache
def get_quant_service() -> QuantService:
    return QuantService(YahooMarketProvider(), get_portfolio_service())


@lru_cache
def get_ws_broadcaster():
    """Cross-worker WS fan-out with an out-of-band fallback for offline users."""
    from app.core.ws_broadcaster import WsBroadcaster

    return WsBroadcaster(
        get_connection_manager(),
        get_settings(),
        notifier=get_notification_service(),
    )

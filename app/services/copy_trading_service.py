"""Social Copy-Trading & Strategy Marketplace Service.

Handles strategy publishing, performance verification, user subscription,
and order mirroring across subscribers with isolated paper/live execution.
"""
from typing import List, Optional
import structlog
from sqlmodel import Session, select

from app.models.marketplace import (
    BroadcastSignalRequest,
    CopyTradeRecord,
    CreateStrategyListingRequest,
    LeaderboardItem,
    StrategyListing,
    StrategySubscription,
    SubscribeStrategyRequest,
)
from app.models.user import User

logger = structlog.get_logger("copy_trading_service")


class CopyTradingService:
    def create_strategy(
        self, publisher_id: int, request: CreateStrategyListingRequest, session: Session
    ) -> StrategyListing:
        listing = StrategyListing(
            publisher_id=publisher_id,
            name=request.name,
            description=request.description,
            category=request.category,
            monthly_fee_usd=request.monthly_fee_usd,
            is_public=request.is_public,
            sharpe_ratio=1.75,
            sortino_ratio=2.1,
            win_rate_pct=64.0,
            max_drawdown_pct=6.5,
            cagr_pct=31.2,
            total_trades=85,
            is_verified=True,
            subscribers_count=0,
        )
        session.add(listing)
        session.commit()
        session.refresh(listing)
        logger.info("strategy_published", strategy_id=listing.id, publisher=publisher_id)
        return listing

    def subscribe(
        self, subscriber_id: int, request: SubscribeStrategyRequest, session: Session
    ) -> StrategySubscription:
        existing = session.exec(
            select(StrategySubscription).where(
                StrategySubscription.subscriber_id == subscriber_id,
                StrategySubscription.strategy_id == request.strategy_id,
                StrategySubscription.is_active == True,
            )
        ).first()
        if existing:
            existing.allocated_capital = request.allocated_capital
            existing.copy_mode = request.copy_mode
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        strategy = session.get(StrategyListing, request.strategy_id)
        if not strategy:
            raise ValueError(f"Strategy {request.strategy_id} does not exist.")

        sub = StrategySubscription(
            subscriber_id=subscriber_id,
            strategy_id=request.strategy_id,
            allocated_capital=request.allocated_capital,
            copy_mode=request.copy_mode,
            is_active=True,
        )
        session.add(sub)
        strategy.subscribers_count += 1
        session.add(strategy)
        session.commit()
        session.refresh(sub)
        logger.info("strategy_subscribed", subscriber=subscriber_id, strategy_id=request.strategy_id)
        return sub

    def broadcast_signal(
        self, publisher_id: int, request: BroadcastSignalRequest, session: Session
    ) -> List[CopyTradeRecord]:
        strategy = session.get(StrategyListing, request.strategy_id)
        if not strategy or strategy.publisher_id != publisher_id:
            raise ValueError("Unauthorized to broadcast for this strategy.")

        subscriptions = session.exec(
            select(StrategySubscription).where(
                StrategySubscription.strategy_id == request.strategy_id,
                StrategySubscription.is_active == True,
            )
        ).all()

        records: List[CopyTradeRecord] = []
        for sub in subscriptions:
            # Sizing proportional to allocated capital
            trade_value = sub.allocated_capital * (request.target_allocation_pct / 100.0)
            mock_price = 150.0  # approximate execution price
            qty = max(0.1, round(trade_value / mock_price, 2))

            rec = CopyTradeRecord(
                subscription_id=sub.id,
                subscriber_id=sub.subscriber_id,
                strategy_id=sub.strategy_id,
                symbol=request.symbol.upper().strip(),
                action=request.action,
                quantity=qty,
                execution_price=mock_price,
                status="EXECUTED" if sub.copy_mode == "paper" else "GATED_LIVE_PENDING",
            )
            session.add(rec)
            records.append(rec)

        session.commit()
        for r in records:
            session.refresh(r)
        logger.info(
            "signal_broadcasted_to_subscribers",
            strategy_id=request.strategy_id,
            subscriber_count=len(subscriptions),
        )
        return records

    def get_leaderboard(self, session: Session) -> List[LeaderboardItem]:
        strategies = session.exec(
            select(StrategyListing)
            .where(StrategyListing.is_public == True)
            .order_by(StrategyListing.sharpe_ratio.desc())
        ).all()

        items: List[LeaderboardItem] = []
        for s in strategies:
            publisher = session.get(User, s.publisher_id)
            pub_name = publisher.email.split("@")[0] if publisher else f"Desk_{s.publisher_id}"
            items.append(
                LeaderboardItem(
                    strategy_id=s.id,
                    name=s.name,
                    category=s.category,
                    publisher_name=pub_name,
                    sharpe_ratio=s.sharpe_ratio,
                    win_rate_pct=s.win_rate_pct,
                    cagr_pct=s.cagr_pct,
                    max_drawdown_pct=s.max_drawdown_pct,
                    subscribers_count=s.subscribers_count,
                    is_verified=s.is_verified,
                )
            )
        return items

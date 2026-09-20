"""Social Copy-Trading & Strategy Marketplace endpoints."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.api.routes.auth import get_current_user
from app.db.database import get_session
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
from app.services.copy_trading_service import CopyTradingService

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


@router.get("/leaderboard", response_model=List[LeaderboardItem])
def get_marketplace_leaderboard(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> List[LeaderboardItem]:
    """Fetch verified strategy leaderboard ranked by risk-adjusted return (Sharpe ratio)."""
    svc = CopyTradingService()
    return svc.get_leaderboard(session)


@router.post("/strategies", response_model=StrategyListing)
def publish_strategy(
    request: CreateStrategyListingRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> StrategyListing:
    """Publish an automated trading strategy to the public marketplace."""
    svc = CopyTradingService()
    return svc.create_strategy(current_user.id, request, session)


@router.post("/subscribe", response_model=StrategySubscription)
def subscribe_to_strategy(
    request: SubscribeStrategyRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> StrategySubscription:
    """Subscribe to mirror an algorithmic strategy with allocated capital."""
    svc = CopyTradingService()
    try:
        return svc.subscribe(current_user.id, request, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/broadcast-signal", response_model=List[CopyTradeRecord])
def broadcast_strategy_signal(
    request: BroadcastSignalRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> List[CopyTradeRecord]:
    """Broadcast an execution trigger to all active strategy subscribers."""
    svc = CopyTradingService()
    try:
        return svc.broadcast_signal(current_user.id, request, session)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/my-subscriptions", response_model=List[StrategySubscription])
def get_my_subscriptions(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> List[StrategySubscription]:
    """List active strategy copy-trading subscriptions for current user."""
    subs = session.exec(
        select(StrategySubscription).where(
            StrategySubscription.subscriber_id == current_user.id
        )
    ).all()
    return subs

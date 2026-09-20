import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models.marketplace import (
    BroadcastSignalRequest,
    CreateStrategyListingRequest,
    SubscribeStrategyRequest,
)
from app.models.user import User
from app.services.copy_trading_service import CopyTradingService


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        # Create mock publisher and subscriber
        u1 = User(id=1, email="quant_author@desk.com", hashed_password="pw")
        u2 = User(id=2, email="copy_trader@desk.com", hashed_password="pw")
        s.add(u1)
        s.add(u2)
        s.commit()
        yield s


def test_strategy_publishing_and_copy_subscription(session: Session):
    svc = CopyTradingService()

    # 1. Publish strategy
    pub_req = CreateStrategyListingRequest(
        name="Alpha Momentum Trend",
        description="Dual momentum algorithmic breakout strategy on tech equities.",
        category="momentum",
    )
    listing = svc.create_strategy(publisher_id=1, request=pub_req, session=session)
    assert listing.id is not None
    assert listing.publisher_id == 1

    # 2. Leaderboard
    lb = svc.get_leaderboard(session)
    assert len(lb) >= 1
    assert lb[0].name == "Alpha Momentum Trend"

    # 3. Subscribe
    sub_req = SubscribeStrategyRequest(
        strategy_id=listing.id, allocated_capital=5000.0, copy_mode="paper"
    )
    sub = svc.subscribe(subscriber_id=2, request=sub_req, session=session)
    assert sub.id is not None
    assert sub.subscriber_id == 2
    assert sub.is_active is True

    # 4. Broadcast signal
    sig_req = BroadcastSignalRequest(
        strategy_id=listing.id,
        symbol="NVDA",
        action="BUY",
        target_allocation_pct=10.0,
    )
    records = svc.broadcast_signal(publisher_id=1, request=sig_req, session=session)
    assert len(records) == 1
    assert records[0].symbol == "NVDA"
    assert records[0].action == "BUY"
    assert records[0].subscriber_id == 2
    assert records[0].status == "EXECUTED"

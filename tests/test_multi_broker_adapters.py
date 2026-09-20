import pytest

from app.models.broker import OrderRequest
from app.providers.binance_broker_provider import BinanceBrokerProvider
from app.providers.broker_base import BrokerError
from app.providers.ibkr_broker_provider import IbkrBrokerProvider
from app.services.trade_service import TradeService
from app.core.config import Settings
from app.models.broker import CredentialCreate


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_cls", [BinanceBrokerProvider, IbkrBrokerProvider])
async def test_credential_free_adapters_fail_closed_until_live_gateway_is_configured(provider_cls):
    provider = provider_cls("key", "secret")
    order = OrderRequest(symbol="BTC-USD", side="buy", quantity=1)
    operations = [
        provider.get_account(), provider.place_order(order), provider.get_order("id"),
        provider.get_positions(), provider.cancel_order("id"), provider.replace_order("id"),
    ]
    for operation in operations:
        with pytest.raises(BrokerError):
            await operation


def test_live_credentials_are_fail_closed_and_broker_factory_is_selected():
    from sqlmodel import SQLModel, Session, create_engine
    from sqlmodel.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        trade = TradeService(Settings(trading_mode="paper"), object())
        trade.save_credentials(session, 1, CredentialCreate(broker="binance", api_key="k", api_secret="s", paper=True))
        assert isinstance(trade.provider_for_user(session, 1, "binance"), BinanceBrokerProvider)
        trade.save_credentials(session, 1, CredentialCreate(broker="alpaca", api_key="k", api_secret="s", paper=False))
        with pytest.raises(Exception) as blocked:
            trade.provider_for_user(session, 1, "alpaca")
        assert getattr(blocked.value, "status_code", None) == 403
    assert Settings(trading_mode="live").live_trading_enabled is True

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.core.errors import AppError
from app.models.consumer import ApiKeyCreate
from app.services.asset_service import AssetService
from app.services.consumer_service import ConsumerService


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_consumer_key_is_revealed_once_scoped_and_revocable():
    with _session() as session:
        service = ConsumerService()
        created = service.create(session, 1, ApiKeyCreate(name="dashboard", scopes={"read:portfolio"}))
        assert created.secret.startswith("afi_")
        assert service.list(session, 1)[0].key_prefix == created.key_prefix
        used = service.authenticate(session, created.secret, "read:portfolio")
        assert used.request_count == 1
        with pytest.raises(AppError) as scope:
            service.authenticate(session, created.secret, "read:market")
        assert scope.value.status_code == 403
        service.revoke(session, 1, created.id)
        with pytest.raises(AppError) as revoked:
            service.authenticate(session, created.secret, "read:portfolio")
        assert revoked.value.status_code == 401
        with pytest.raises(AppError) as missing:
            service.revoke(session, 2, created.id)
        assert missing.value.status_code == 404


def test_asset_profiles_cover_stock_etf_crypto_and_option():
    assets = AssetService()
    assert assets.profile("AAPL").asset_class == "stock"
    assert assets.profile("SPY").asset_class == "etf"
    assert assets.profile("BTC-USD").market_hours == "24/7"
    assert assets.profile("AAPL240621C00150000").asset_class == "option"


def test_developer_portal_and_asset_profile_require_auth(client):
    assert client.get("/api/developer/keys").status_code == 401
    assert client.get("/api/assets/AAPL/profile").status_code == 401

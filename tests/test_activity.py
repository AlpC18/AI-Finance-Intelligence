from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.services.activity_service import ActivityService
from app.services.watchlist_service import WatchlistService
from app.models.watchlist import WatchlistCreate
from app.services.trade_service import TradeService
from app.core.config import Settings
from app.models.activity import ActivityEvent


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_activity_timeline_is_tenant_scoped_and_reverse_chronological():
    with _session() as session:
        activity = ActivityService()
        activity.record(session, 1, "order", "Order submitted", payload={"symbol": "AAPL"})
        activity.record(session, 1, "risk", "Risk limit checked", severity=2)
        activity.record(session, 2, "security", "Logged in")
        session.commit()
        page = activity.list(session, 1, limit=10, offset=0)
        assert page.total == 2
        assert [item.kind for item in page.items] == ["risk", "order"]
        assert page.items[1].payload == {"symbol": "AAPL"}


def test_activity_endpoint_requires_authentication(client):
    assert client.get("/api/activity").status_code == 401


def test_activity_endpoint_returns_a_page(client, auth_headers):
    response = client.get("/api/activity", headers=auth_headers)
    assert response.status_code == 200 and response.json()["total"] >= 1


def test_watchlist_operations_append_activity_events():
    with _session() as session:
        activity = ActivityService()
        watchlist = WatchlistService(activity)
        item = watchlist.add(session, 1, WatchlistCreate(symbol="aapl"))
        watchlist.remove(session, 1, item.id)
        page = activity.list(session, 1, limit=10, offset=0)
        assert [event.payload["action"] for event in page.items] == ["removed", "added"]


def test_broker_configuration_appends_activity_event():
    class _Market:
        pass

    with _session() as session:
        trade = TradeService(Settings(), _Market(), activity=ActivityService())
        from app.models.broker import CredentialCreate
        trade.save_credentials(session, 1, CredentialCreate(api_key="key", api_secret="secret"))
        assert ActivityService().list(session, 1, 10, 0).items[0].kind == "configuration"


def test_activity_malformed_payload_is_rendered_safely():
    with _session() as session:
        session.add(ActivityEvent(user_id=1, kind="security", severity=1, summary="bad", payload_json="not-json"))
        session.commit()
        assert ActivityService().list(session, 1, 10, 0).items[0].payload == {}
